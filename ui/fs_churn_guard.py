# -*- coding: utf-8 -*-
"""ファイルの大量更新の間だけ、ファイル監視を一時停止する（r146）。

ユーザー報告 2026-10-30: 「P4V で取得中にフリーズする」

原因（2026-10-30 に計測で確定）:
  QFileSystemModel は、見張っているフォルダに新しいファイルが現れるたびに
  «読み込み済みの全フォルダ» を並べ替え直し、layoutChanged を出す。
  それを受けたプロキシとカラム群の作り直しが Qt の C++ 側で重く、
  1 回あたり読み込み済み 2 万件で約 0.4 秒。P4V の取得は «一時ファイル →
  名前変更 → 読み取り専用» を何千回も繰り返すので、これが途切れなく続き、
  UI が丸ごと止まる（実機の mfm_freeze.log ではメインスレッドが Python の
  コードを 1 行も走らせていないまま 30 秒以上止まっていた）。
  監視を止めると詰まりは 0.02 秒まで消える。

やること:
  * 表示中より前から読み込み済みのフォルダで変化が続き、かつ UI が実際に
    重くなっている時だけ、監視を止める（DontWatchForChanges）。
    移動直後の読み込み（新しく開いたフォルダ）は数えない。
  * 止めている間は、表示中のフォルダの更新時刻を裏のスレッドで見張り、
    落ち着いたら監視を戻して表示中のフォルダを読み直す。
    長く続く時も一定間隔で表示だけは最新にする。
  * 止めている間に «読み込み済みだった» フォルダは、次に表示した時に
    読み直す（そのままだと古い中身が出たまま更新されない）。
"""
import os
import threading
import time
from collections import deque

from core.compat import QObject, QTimer, Signal, QFileSystemModel, QListView
from core.diag import swallow as _swallow
from core.diag import diag_off as _diag_off
from core.i18n import tr


def fswatch_log(msg: str):
    """監視の停止・再開を記録する（logs/mfm_fswatch.log）。失敗しても何もしない。"""
    try:
        import datetime
        from ui.browser_util import _MFM_LOG_DIR
        p = os.path.join(_MFM_LOG_DIR, "mfm_fswatch.log")
        try:
            if os.path.getsize(p) > 1024 * 1024:
                os.replace(p, p + ".1")
        except OSError:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write("[%s pid=%d] %s\n" % (
                datetime.datetime.now().strftime("%m-%d %H:%M:%S"), os.getpid(), msg))
    except Exception:
        pass


def _opt_dont_watch():
    try:
        return QFileSystemModel.Option.DontWatchForChanges
    except AttributeError:
        return QFileSystemModel.DontWatchForChanges


class FsChurnGuard(QObject):
    """BrowserPanel 1 つにつき 1 つ。モデルを作り直したら attach() し直す。"""

    WINDOW_S = 2.5              # 判定の窓
    MIN_EVENTS = 8              # 窓の中の «読み込み済みフォルダでの変化» の数
    MIN_BUSY = 0.30             # 窓の中で UI が塞がっていた割合
    LOADED_GRACE_S = 2.0        # 読み込んでからこの秒数の変化は «読み込み» とみなす
    BEAT_MS = 100
    POLL_MS = 1500              # 止めている間の見張り間隔
    QUIET_POLLS = 2             # これだけ続けて変化が無ければ再開
    REFRESH_WHILE_PAUSED_S = 20.0
    SELF_RELIST_COOLDOWN_S = 4.0   # 自分で読み直した直後は数えない（自分の変化で止まらない）

    _polled = Signal(object)

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self._model = None
        self._loaded_at = {}
        self._events = deque()
        self._lates = deque()
        self._paused = False
        self._paused_at = 0.0
        self._last_refresh = 0.0
        self._quiet = 0
        self._last_sig = None
        self._stale = set()
        self._polling = False
        self._ignore_until = 0.0
        self._last_beat = time.monotonic()
        self._beat = QTimer(self)
        self._beat.setInterval(self.BEAT_MS)
        self._beat.timeout.connect(self._on_beat)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._polled.connect(self._on_polled)
        if not _diag_off("no_churn_guard"):
            self._beat.start()

    # ── つなぐ ───────────────────────────────────────────────────
    def attach(self, model):
        old = self._model
        if old is not None:
            for sig, slot in self._slots(old):
                try:
                    sig.disconnect(slot)
                except Exception:
                    pass
        self._model = model
        self._loaded_at = {}
        self._events.clear()
        if model is None:
            return
        for sig, slot in self._slots(model):
            try:
                sig.connect(slot)
            except Exception as _e:
                _swallow(_e, "ui/fs_churn_guard.py attach")
        if self._paused:
            try:
                model.setOption(_opt_dont_watch(), True)
            except Exception as _e:
                _swallow(_e, "ui/fs_churn_guard.py attach(pause)")

    def _slots(self, m):
        return ((m.directoryLoaded, self._on_loaded),
                (m.rowsInserted, self._on_rows),
                (m.rowsRemoved, self._on_rows),
                (m.dataChanged, self._on_data))

    def is_paused(self) -> bool:
        return self._paused

    # ── 数える ───────────────────────────────────────────────────
    @staticmethod
    def _key(path):
        try:
            return os.path.normcase(os.path.normpath(path))
        except Exception:
            return path or ""

    def _on_loaded(self, path):
        """覚えるのは «最初に» 読み込んだ時刻だけ。

        見張っているフォルダが変わると、Qt はそのフォルダを読み直して
        もう一度 directoryLoaded を出す。毎回時刻を上書きすると «開いた
        ばかり» に見えて変化が 1 件も数えられない（実測 2026-10-30）。
        2 回目以降の読み直しは、それ自体を変化として数える。"""
        k = self._key(path)
        now = time.monotonic()
        t = self._loaded_at.get(k)
        if t is None:
            self._loaded_at[k] = now
        elif now - t >= self.LOADED_GRACE_S and now >= self._ignore_until:
            self._events.append(now)
        if self._paused:
            self._stale.add(k)

    def _note_change(self, parent_index):
        m = self._model
        if m is None:
            return
        try:
            path = m.filePath(parent_index) if parent_index.isValid() else ""
        except Exception:
            return
        if not path:
            return
        t = self._loaded_at.get(self._key(path))
        now = time.monotonic()
        if t is None or now - t < self.LOADED_GRACE_S:
            return              # 開いたばかり＝読み込み。変化ではない
        if now < self._ignore_until:
            return              # 自分で読み直した分
        self._events.append(now)

    def _on_rows(self, parent, *_a):
        self._note_change(parent)

    def _on_data(self, top_left, *_a):
        try:
            self._note_change(top_left.parent())
        except Exception:
            pass

    def _on_beat(self):
        now = time.monotonic()
        late = now - self._last_beat - self.BEAT_MS / 1000.0
        self._last_beat = now
        if late > 0.02:
            self._lates.append((now, late))
        cut = now - self.WINDOW_S
        while self._events and self._events[0] < cut:
            self._events.popleft()
        while self._lates and self._lates[0][0] < cut:
            self._lates.popleft()
        if self._paused or now < self._ignore_until:
            return
        busy = sum(x for _t, x in self._lates)
        if len(self._events) >= self.MIN_EVENTS and busy >= self.MIN_BUSY * self.WINDOW_S:
            self.pause("changes=%d busy=%.1fs/%.1fs" % (len(self._events), busy, self.WINDOW_S))

    # ── 止める・戻す ─────────────────────────────────────────────
    def pause(self, why=""):
        m = self._model
        if self._paused or m is None:
            return
        try:
            m.setOption(_opt_dont_watch(), True)
        except Exception as _e:
            _swallow(_e, "ui/fs_churn_guard.py pause")
            return
        self._paused = True
        self._paused_at = self._last_refresh = time.monotonic()
        self._quiet = 0
        self._last_sig = None
        # 読み込み済みのフォルダは全部 «見張りが無い» 状態になる
        self._stale = set(self._loaded_at)
        self._events.clear()
        fswatch_log("pause (%s)" % why)
        self._status(tr("ファイルの大量更新を検出 — 自動更新を一時停止中（落ち着いたら最新にします）",
                        "Many file changes detected - auto refresh paused "
                        "(will refresh when it settles)"))
        self._poll_timer.start()

    def resume(self, why=""):
        if not self._paused:
            return
        m = self._model
        self._poll_timer.stop()
        self._paused = False
        try:
            if m is not None:
                m.setOption(_opt_dont_watch(), False)
        except Exception as _e:
            _swallow(_e, "ui/fs_churn_guard.py resume")
        dirs = self.displayed_dirs()
        self._relist(dirs)
        for d in dirs:
            self._stale.discard(self._key(d))
        self._events.clear()
        self._lates.clear()
        fswatch_log("resume (%s) relisted=%d stale_left=%d" % (why, len(dirs), len(self._stale)))
        self._status(tr("自動更新を再開しました", "Auto refresh resumed"))

    def on_shown(self, path):
        """表示したフォルダが «止めている間に見張りが外れた» ものなら読み直す。"""
        if self._paused or not self._stale or not path:
            return
        todo = []
        p = path
        for _i in range(64):
            k = self._key(p)
            if k in self._stale:
                todo.append(p)
                self._stale.discard(k)
            parent = os.path.dirname(p.rstrip("/\\"))
            if not parent or parent == p:
                break
            p = parent
        if todo:
            self._relist(todo)

    def _relist(self, dirs):
        """dirs を読み直して見張りを付け直す（既存の付け直しの道を通す）。"""
        panel = self._panel
        dirs = [d for d in dirs if d]
        if not dirs:
            return
        # 付け直しは 900ms 後にまとめて走るので、その分も含めて数えない
        self._ignore_until = time.monotonic() + self.SELF_RELIST_COOLDOWN_S
        self._events.clear()
        self._lates.clear()
        try:
            for d in dirs:
                panel._unwatch_dir(d)       # «要再読» の印を付ける
            panel._rearm_fs_watch(dirs)     # fetchMore で読み直す（見張りも戻る）
        except Exception as _e:
            _swallow(_e, "ui/fs_churn_guard.py _relist")

    def displayed_dirs(self):
        out = []
        panel = self._panel
        try:
            cv = panel._column_view
            for v in cv.findChildren(QListView):
                if v.isHidden() or v.model() is None:
                    continue
                p = cv._path_for_index(v.rootIndex()) or ""
                if p and p not in out:
                    out.append(p)
        except Exception as _e:
            _swallow(_e, "ui/fs_churn_guard.py displayed_dirs")
        cur = getattr(panel, "_current_path", "") or ""
        if cur and cur not in out:
            out.append(cur)
        return out[-12:]

    # ── 止めている間の見張り（裏のスレッド）───────────────────────
    def _poll(self):
        if not self._paused or self._polling:
            return
        self._polling = True
        dirs = self.displayed_dirs()

        def _run():
            sig = []
            for d in dirs:
                try:
                    st = os.stat(d)
                    sig.append((d, st.st_mtime_ns))
                except OSError:
                    sig.append((d, None))
            self._polled.emit((tuple(sig), dirs))

        threading.Thread(target=_run, daemon=True, name="mfm-churn-poll").start()

    def _on_polled(self, payload):
        self._polling = False
        if not self._paused:
            return
        sig, dirs = payload
        if sig == self._last_sig:
            self._quiet += 1
        else:
            self._quiet = 0
        self._last_sig = sig
        if self._quiet >= self.QUIET_POLLS:
            self.resume("quiet")
            return
        now = time.monotonic()
        if now - self._last_refresh >= self.REFRESH_WHILE_PAUSED_S:
            # 長く続いている: 監視は止めたまま、表示だけ最新にする
            self._last_refresh = now
            self._relist(dirs)
            fswatch_log("refresh while paused (%d dirs)" % len(dirs))

    def _status(self, text):
        try:
            self._panel.status_message.emit(text)
        except Exception:
            pass
