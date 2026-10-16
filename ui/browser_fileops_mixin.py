# -*- coding: utf-8 -*-
"""BrowserPanel の ファイル操作（コピー／移動／削除／複製／クリップボード）と進捗・ロック・見張り（r146 で分離）。

ui/browser_panel.py が 4,400 行を超えて読めなくなっていたので、
責務ごとに Mixin へ分けた。**挙動は変えていない**（メソッド名も
呼び出し側もそのまま）。BrowserPanel がこれを継承する。
他の Mixin や本体の属性（self._fs_model など）を前提にしているので、
単体では使わない。
"""
from core.diag import swallow as _swallow  # r112
from core.diag import diag_off as _diag_off  # r146 切り分け用
from core.i18n import tr  # r118: モジュールレベルで提供（ローカル import 不要）

import os
from core import fast_stat as _fs  # r146: UI スレッドで待たない stat
import struct
import threading
from pathlib import Path
from typing import List, Optional, Callable

from core.compat import (
    Qt, Signal, QObject,
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QLineEdit, QToolButton,
    QSplitter, QListView,
    QSizePolicy, QFrame, QAbstractItemView, QFileSystemModel, QMenu, QAction, QMessageBox, QFileDialog, QInputDialog, QDialog,
    QModelIndex, QSize, QRect, QPixmap, QDir, QFileInfo, QUrl, QMimeData, QPoint,
    QTimer, QKeySequence, QDrag, )
from core.compat import QtCore as _QtCore
from core.compat import is_alive
try:  # PySide6: QtGui / PySide2: QtWidgets
    from core.compat import QIcon
except ImportError:  # pragma: no cover
    QIcon = None
try:
    from PySide6.QtGui import QFileIconProvider
except ImportError:
    try:
        from PySide6.QtWidgets import QFileIconProvider
    except ImportError:
        from PySide2.QtWidgets import QFileIconProvider
from core.path_guard import PathProber, DriveScanner, invalidate_cache
# r146: ここを «メソッドの中だけ» で import していたため、_copy_dialog /
# _move_dialog が NameError で無反応になっていた（r145 の取りこぼし）。
# 共通ダイアログのヘルパはモジュール先頭で取り込む。
from ui.dialog_util import pick_directory, pick_open_file  # noqa: F401
from core.file_operations import (
    open_with_default_app, reveal_in_explorer,
    copy_items, move_items, get_file_type_category, format_size,
    resolve_windows_shortcut,
    is_read_only, set_read_only,
    OperationCancelled as _OperationCancelled,
    MAYA_EXTENSIONS
)
from core.thumbnail_generator import ThumbnailManager

from ui.dcc_header import glyph_icon, integration_icon  # r142: アイコン列を揃える
from ui.browser_util import (  # noqa: F401  （再エクスポート）
    _cursor_over_maya_window, _cursor_over_dcc_window, _time_mod, _MFM_T0,
    _MFM_STARTUP_LOG, _MFM_FREEZE_LOG, _re_mod, _DRIVE_IN_LABEL_RE,
    _safe_file_path, _SafeIconProvider, _mfm_slow_note, _MFM_UI_LOG,
    _MFM_UILOG_ON, _mfm_warn, _mfm_uilog, _MFM_BLOCKING,
    mfm_blocking_begin, mfm_blocking_end, mfm_blocking_reason,
    _mfm_timeline, _MFM_LOG_PATH, _MFM_DEBUG, _mfm_log,
)
from ui.browser_delegates import (  # noqa: F401  （再エクスポート）
    _paint_expanded_mark, ThumbnailDelegate, _BADGE_STYLE, _P4_BADGE_PNG,
    _BADGE_ICON_DIR, _BADGE_PIXMAP_CACHE, _badge_pixmap, _badge_kind,
    _FileOpNotifier, _INTEG_RESULT_CONNECTED,
    _on_integration_action_finished, _badge_tooltip, StatusBadgeDelegate,
)
from ui.browser_models import (  # noqa: F401  （再エクスポート）
    FileFilterProxyModel,
)
from ui.browser_column_view import (  # noqa: F401  （再エクスポート）
    CappedColumnView, _DccAwareMime, _ColumnSizePopup,
    _ColumnResizeHandle, _SizeButtonHover,
)



from ui.browser_models import FileFilterProxyModel  # noqa: F401


def _bp():
    """本体モジュール（ui.browser_panel）を実行時に引く。

    テストは ``ui.browser_panel.QMenu`` などを差し替えて挙動を観察する。
    Mixin が自分の名前空間の QMenu を使うと、その差し替えが効かなくなる
    ので、差し替えの対象になる名前は **本体モジュール経由** で引く。
    （読み込みの循環を避けるため、関数の中で import する。）"""
    import ui.browser_panel as _m
    return _m


class FileOpsMixin:
    """ファイル操作（コピー／移動／削除／複製／クリップボード）と進捗・ロック・見張り。"""

    def _release_fs_handles(self, folder: str, level: int = 1):
        """リネーム／移動の前に «Manager 自身が掴んでいるもの» を手放す（r98）。

        QFileSystemModel（QFileInfoGatherer）は fetch したディレクトリを
        監視スレッドで掴み続けるため、Windows では «Manager で開いている
        フォルダ» のリネーム／移動が [WinError 5] で弾かれる。
        level 1 = rootPath を親へ逃がす＋サムネイル停止、
        level 2 以上 = モデルごと作り直して監視ハンドルを完全に手放す。"""
        try:
            parent = os.path.dirname(folder.rstrip("/\\")) or folder
            tm = getattr(self, "_thumb_mgr", None)
            if tm is not None:
                for name in ("cancel_all", "clear_queue", "stop"):
                    fn = getattr(tm, name, None)
                    if callable(fn):
                        try:
                            fn()
                        except Exception as _e:
                            _swallow(_e, "ui/browser_panel.py:2123 _release_fs_handles")
                        break
            if level <= 1:
                # r146（実機計測 2026-10-15）: **setRootPath("") は使わない。**
                # Qt の setRootPath は «それまでの rootPath» の見張りだけを外す
                # （子フォルダ全部ではない）。"" を挙げると、その上モデルの
                # 根＝ドライブ一覧を一度だけ読みに行き、切れたネットワーク
                # ドライブがあると **監視スレッドが 16 秒止まる**（その間、
                # どのフォルダの変化もモデルに届かない＝「反映されない」）。
                # 外したいフォルダを一度 root にしてから親へ逃がす。これで
                # そのフォルダの見張りだけが外れ、節は «要再読» の印が付く
                # （付け直しは _rearm_fs_watch）。
                self._unwatch_dir(folder)
                self._flush_deleted()
                return
            self._recreate_fs_model(parent)
        except Exception as e:
            _mfm_log("release_fs_handles error: %r" % (e,))

    def _unwatch_dir(self, folder: str):
        """folder の見張り（Windows ではディレクトリのハンドル）を外す（r146）。

        QFileSystemModel が見張りを外すのは «rootPath から離れる時» だけ
        なので、folder を root にしてから親へ移る。親が «要再読» なら
        ここで読み直す（≒ 現在地なら表示が新しくなる）。"""
        m = self._fs_model
        try:
            if not folder or not _fs.isdir(folder):
                return
            parent = os.path.dirname(folder.rstrip("/\\")) or folder
            if os.path.normcase(os.path.normpath(parent)) == os.path.normcase(
                    os.path.normpath(folder)):
                return      # ドライブの根は外せない（外す必要もない）
            m.setRootPath(folder)
            m.setRootPath(parent)
        except Exception as e:
            _mfm_log("unwatch_dir error: %r" % (e,))

    def _recreate_fs_model(self, root_hint: str = ""):
        """QFileSystemModel を作り直して «監視スレッドのハンドル» を完全に手放す。

        Qt には «このディレクトリの監視だけ外す» API が無いため、掴みっぱなしを
        確実に解くにはモデルごと捨てるしかない（r98）。重い操作なので
        リネーム／移動が権限エラーで弾かれた時だけ通る道。"""
        old = self._fs_model
        new = QFileSystemModel()
        try:
            new.setIconProvider(_SafeIconProvider())
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:2145 _recreate_fs_model")
        new.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Files | QDir.Hidden)
        new.setReadOnly(False)
        for setter, arg in ((new.setResolveSymlinks, False),):
            try:
                setter(arg)
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:2152 _recreate_fs_model")
        try:
            new.setOption(QFileSystemModel.DontUseCustomDirectoryIcons, True)
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:2156 _recreate_fs_model")
        try:
            new.fileRenamed.connect(self._on_fs_file_renamed)
            new.directoryLoaded.connect(self._on_fs_dir_loaded)
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:2161 _recreate_fs_model")
        self._fs_model = new
        try:
            self._proxy.setSourceModel(new)
        except Exception as e:
            _mfm_log("recreate model: setSourceModel failed %r" % (e,))
        try:
            old.directoryLoaded.disconnect()
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:2170 _recreate_fs_model")
        try:
            old.fileRenamed.disconnect()
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:2174 _recreate_fs_model")
        # r146: 以前はここで old.setRootPath("") をしていたが撤去。
        # "" はドライブ一覧の読み込みを起こし（切れたネットワークドライブで
        # 16 秒）、捨てる側の監視スレッドがそれを終えるまで、破棄
        # （~QFileInfoGatherer の wait）で UI が止まる。捨てれば監視は
        # スレッドごと終わるので、何も伝える必要は無い。
        try:
            old.setParent(None)
            old.deleteLater()
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:2179 _recreate_fs_model")
        # r128: **ここが肝。** processEvents() は DeferredDelete を流さないので、
        # 従来は old が «捨てたつもりで生きたまま» だった＝監視ハンドルを握った
        # ままで、Windows の [WinError 5] が解けなかった。
        self._flush_deleted()
        _mfm_log("fs model recreated (root_hint=%s)" % (root_hint,))

    @staticmethod
    def _flush_deleted():
        """deleteLater() を «今» 実行させる（r128）。

        QApplication.processEvents() は DeferredDelete を流さない。
        監視ハンドルを手放すには、捨てた QObject が本当に消える必要がある。"""
        try:
            QApplication.processEvents()
            QApplication.sendPostedEvents(None, _QtCore.QEvent.DeferredDelete)
            QApplication.processEvents()
        except Exception as e:
            _mfm_log("flush_deleted error: %r" % (e,))

    def _release_fs_handles_for(self, paths, level: int = 1):
        """paths に含まれるフォルダの監視ハンドルを手放す（r144）。

        リネーム／移動では昔からやっていたが、削除では抜けていて
        «使用中または権限なし» で弾かれていた（ユーザー報告 2026-10-08:
        «managerを閉じてエクスプローラーで消すと消えました»）。"""
        try:
            targets = [p for p in (paths or []) if p and _fs.isdir(p)]
            if not targets:
                # ファイルだけでも、親フォルダの監視が邪魔をすることがある
                targets = [os.path.dirname(p.rstrip("/\\")) for p in (paths or []) if p]
                targets = [t for t in targets if t and _fs.isdir(t)]
            if not targets:
                return
            if level >= 2:
                # モデルごと作り直す＝全部のハンドルが落ちるので 1 回で足りる
                self._release_fs_handles(targets[0], level=2)
                return
            seen = set()
            for t in targets:
                k = os.path.normcase(os.path.abspath(t))
                if k in seen:
                    continue
                seen.add(k)
                self._release_fs_handles(t, level=1)
        except Exception as e:
            _mfm_log("release_fs_handles_for error: %r" % (e,))

    def _resync_dir(self, folder: str):
        """そのフォルダをモデルに読み直させる（r146）。

        **rootPath を空へ逃がしてはいけない。**（ユーザー報告 2026-10-15）
          「フォルダを削除したら、カラムが一度左に移動し、再度サーチされる
            ように右に戻る挙動をしました。」
          「カラムは D ドライブまで巻き戻りました。」
        setRootPath("") はカラムを組み直して左へ飛ばす上、ドライブ一覧の
        読み込みで監視スレッドを長く止める（_release_fs_handles 参照）。
        付け直すのは «そのフォルダ» だけで足りる。"""
        try:
            if not folder or not _fs.isdir(folder):
                return
            self._rearm_fs_watch([folder])      # 編集中なら中で後回しになる
        except Exception as e:
            _mfm_log("resync_dir error: %r" % (e,))

    def _rearm_fs_watch(self, dirs):
        """フォルダの «見張り» を付け直す（r146、実機 2026-10-15）。

        ユーザー報告:
          「コピーでも移動でも、結果が即座に反映されておらず、完了しても
            反映されずなので駄目です。1 分近く待っても反映がされてないです。」

        原因はここ。削除・移動の **前** に、Windows のハンドルを手放すため
        見張りを外している（それ自体は必要。_release_fs_handles）。
        ところが **後で付け直していなかった** ので、そのフォルダで何が起きても
        モデルに届かなくなっていた＝いつまで経っても表示が変わらない。
        操作が終わったら必ずここを通して付け直す。"""
        if _diag_off("no_rearm"):
            return
        # r146（実機 2026-10-15）: **すぐにはやらない。**
        # 付け直しはカラムを組み直すので、操作の直後にやると «その場編集を
        # 開いた直後» とぶつかる。少し置いてからまとめて 1 回だけ行う。
        want = [d for d in (dirs or []) if d]
        pend = getattr(self, "_rearm_pending", None)
        if pend is None:
            pend = self._rearm_pending = []
        for d in want:
            if d not in pend:
                pend.append(d)
        t = getattr(self, "_rearm_timer", None)
        if t is None:
            t = self._rearm_timer = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._rearm_fs_watch_now)
        t.start(900)

    def _rearm_fs_watch_now(self):
        """溜めておいた «見張りの付け直し» を 1 回で片付ける（r146）。"""
        dirs = list(getattr(self, "_rearm_pending", []) or [])
        self._rearm_pending = []
        # r146（実機 2026-10-15）: **名前を打っている最中は触らない。**
        # 見張りの付け直しはカラムを組み直すことがあり、その巻き添えで
        # インライン編集の QLineEdit が消える（Windows の回帰スイートで
        # «Internal C++ object already deleted» として出た）。終わるまで待つ。
        # 判定は «自分で立てた旗» だけで行う（実機 2026-10-15）。
        # ウィジェットの生存や可視を見る形にしていたが、守りが素通りする
        # 場面があった。始めた／終えたを自分で覚える。
        if getattr(self, "_inline_editing", False):
            # 名前を打っている最中は触らない。終わってからやる。
            self._rearm_pending = list(dirs or [])
            t = getattr(self, "_rearm_timer", None)
            if t is not None:
                t.start(700)
            return
        m = self._fs_model
        # r146（実機計測 2026-10-15）: **setRootPath("") は使わない**
        # （理由は _release_fs_handles）。見張りが外れた節には Qt が
        # «要再読» の印（canFetchMore）を付けているので、fetchMore で
        # 読み直させれば見張りも一緒に戻る。root は動かさないので、
        # カラムが組み直されることも巻き戻ることもない。
        # 最後は «今いる場所» を root に戻す（ここが見張られていないと
        # 一番困る）。その時に外れる «前の root» も読み直しておく。
        cur = self._current_path or ""
        try:
            tgt = cur if _fs.isdir(cur) else os.path.dirname(cur or "")
        except OSError:
            tgt = ""
        prev = ""
        try:
            prev = m.rootPath() or ""
            if (tgt and _fs.isdir(tgt) and os.path.normcase(
                    os.path.normpath(prev or "x")) != os.path.normcase(
                    os.path.normpath(tgt))):
                m.setRootPath(tgt)
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _rearm_fs_watch(cur)")
        done = []
        for d in list(dirs or []) + [prev, tgt]:
            if not d:
                continue
            try:
                if not _fs.isdir(d):
                    d = os.path.dirname(d.rstrip("/\\"))
                if not d or not _fs.isdir(d):
                    continue
            except OSError:
                continue
            k = os.path.normcase(os.path.normpath(d))
            if k in done:
                continue
            done.append(k)
            try:
                idx = m.index(d)
                if idx.isValid() and m.canFetchMore(idx):
                    m.fetchMore(idx)
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py _rearm_fs_watch")
            if len(done) >= 12:
                break
        self._flush_deleted()
        self._refresh_flat_view()

    def _resync_touched(self, paths):
        """操作が触ったフォルダを読み直す（r146）。

        見張りの取りこぼしで «消したのに残っている / 増えたのに出ない» が
        起きる。対象はせいぜい数件なので、終わったときに素直に読み直す。"""
        dirs = []
        for p in (paths or []):
            if not p:
                continue
            try:
                d = p if _fs.isdir(p) else os.path.dirname(p)
            except OSError:
                d = os.path.dirname(p)
            if d and d not in dirs:
                dirs.append(d)
            parent = os.path.dirname(p.rstrip("/\\"))
            if parent and parent not in dirs:
                dirs.append(parent)
        for d in dirs[:8]:
            try:
                self._resync_dir(d)
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py _resync_touched")

    # ── 長時間になり得るファイル操作の共通経路（r62） ─────────────────────
    # 方針: コピー/移動/削除/Undo/Redo は必ずワーカースレッドで実行し、
    # 0.4 秒を超えたら進捗ダイアログ（キャンセル付き）を出す。UI スレッドで
    # shutil を回してフリーズさせない。キャンセル時も «完了した分» は
    # Undo 記録に残す（部分完了を黙って捨てない）。
    class _OpCancelled(_OperationCancelled):
        """キャンセルの印（r146 で core 側の印を継承）。

        core/file_operations.py はフォルダの中を 1 件ずつ写すようになった。
        その途中で進捗コールバックが投げるこの例外を «失敗» と取り違えて
        FileOperationError に包むと、キャンセルしたのにエラーダイアログが
        出る。core 側が «これだけは包まない» と分かるように、共通の親を持つ。
        """

    def _busy_guard(self, paths, verb: str) -> bool:
        """その場所が «走っている操作» と重なっていないか（r146）。

        進捗ダイアログを非モーダルにしたので（ユーザー指示 2026-10-13）、
        走っている最中でもマネージャーは触れる。ただし **その操作が触って
        いる場所だけ** は通さない。移動元・移動先と、その上下関係にあるもの。
        無関係なフォルダでの操作はこれまでどおり通る。"""
        from core.file_locks import conflicts
        bad = conflicts(paths)
        if not bad:
            return True
        names = []
        for raw, label in bad[:5]:
            names.append("  %s（%s）" % (os.path.basename(raw.rstrip("/\\")) or raw,
                                        label or tr("処理中", "in progress")))
        QMessageBox.information(
            self, verb,
            tr("この項目は処理中のため、いま %s できません。\n\n%s\n\n"
               "終わってからもう一度お試しください。",
               "These items are busy and cannot be %s right now.\n\n%s\n\n"
               "Please try again once the running operation finishes.")
            % (verb, "\n".join(names)))
        return False

    def _run_file_op(self, title: str, work, on_done=None, total: int = 0,
                     busy_paths=None):
        """work(progress_cb) をワーカーで実行。progress_cb(i, n) はワーカーから
        呼ばれ、キャンセル済みなら _OpCancelled を投げて中断させる。
        on_done(result, cancelled: bool, error: str) は UI スレッドで呼ばれる。
        busy_paths … その操作が触る場所（移動元・移動先）。走っている間だけ
        予約し、同じ場所への別の操作を断る（r146）。"""
        from core.compat import QProgressDialog
        from core import file_locks
        lock_token = file_locks.acquire(busy_paths or [], title)
        notifier = _FileOpNotifier(self)
        dlg = QProgressDialog(title, tr("キャンセル", "Cancel"),
                              0, max(0, int(total)), self)
        dlg.setWindowTitle(title)
        # r146（ユーザー指示 2026-10-13）:
        #   「移動中やコピー中にマネージャーが止まらない様にしてください。
        #     進捗バーは出してください。」
        # 実処理はもともとワーカースレッドなので、止めていたのは «進捗ダイアログを
        # WindowModal にしていたこと» だけだった。非モーダルにすれば、進捗を
        # 出したままフォルダを見て回れる（キャンセルもそのまま効く）。
        dlg.setWindowModality(Qt.NonModal)
        dlg.setModal(False)
        from ui.dialog_util import apply_window_buttons as _awb
        _awb(dlg)                        # r146: ✕ を押せる状態にしておく
        dlg.setMinimumDuration(400)      # 短い操作ではダイアログを出さない
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        state = {"cancel": False, "done": False}
        dlg.canceled.connect(lambda: state.__setitem__("cancel", True))
        t0 = _time_mod.monotonic()

        def cb(i, n):
            notifier.progress.emit(int(i), int(n))
            if state["cancel"]:
                raise self._OpCancelled()

        def run():
            try:
                res = work(cb)
                notifier.finished.emit(res, "")
            except self._OpCancelled:
                notifier.finished.emit(None, "__cancel__")
            except Exception as e:
                notifier.finished.emit(None, str(e) or e.__class__.__name__)

        # r146（ユーザー指示 2026-10-15）:
        #   「1 ファイルが長いと、動いていないようにも見えるので、処理が
        #     動いている事がわかるように、回転するサークルなりを出して
        #     ください。それが止まっていると、処理自体が止まっているのかと
        #     気付けるようにしたい意図です。」
        # 進捗の «数» は 1 ファイルが大きいと何十秒も動かない。別に
        # «回り続ける印» を出し、**止まったら止まっていると分かる** ようにする。
        # 回しているのは UI スレッドのタイマーなので、UI が固まれば止まる
        # ＝ユーザーの意図どおりの合図になる。
        SPIN = "◐◓◑◒"
        state["spin"] = 0
        state["label"] = title

        def _paint_label():
            mark = SPIN[state["spin"] % len(SPIN)]
            dlg.setLabelText("%s  %s" % (state["label"], mark))

        def on_progress(i, n):
            if n > 0 and dlg.maximum() != n:
                dlg.setMaximum(n)
            dlg.setValue(i)
            state["label"] = ("%s  (%d / %d)" % (title, i, n)) if n > 0 else title
            _paint_label()

        # 項目数が少なく1件が重い（巨大ファイル）時も、0.4秒経ったら必ず出す
        tick = QTimer(self)
        tick.setInterval(200)

        spin = QTimer(self)
        spin.setInterval(160)

        def _spin():
            if state["done"]:
                spin.stop()
                return
            state["spin"] += 1
            _paint_label()
        spin.timeout.connect(_spin)
        spin.start()

        def on_tick():
            """0.4 秒を越えたら進捗ダイアログを «1 回だけ» 出す。

            r146（ユーザー報告 2026-10-14）:
              「複製している時に進捗バーのキャンセルを押しても何度も
                ウィンドウが出てきてキャンセル出来ません」
            原因はここ。200ms ごとに «見えていなければ出す» をしていたので、
            キャンセル（QProgressDialog は押された時点で自分を隠す）や ✕ で
            閉じた直後に、この見張りがまた出していた。出すのは一度きりで
            足りるし、ユーザーが閉じたものを勝手に出し直してはいけない。"""
            if state["done"] or state["cancel"]:
                tick.stop()
                return
            if _time_mod.monotonic() - t0 > 0.4 and not dlg.isVisible():
                dlg.show()
                tick.stop()
        tick.timeout.connect(on_tick)
        tick.start()

        def done(res, err):
            state["done"] = True
            tick.stop()
            spin.stop()
            file_locks.release(lock_token)
            try:
                dlg.close()
                dlg.deleteLater()
                notifier.deleteLater()
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:2706 done")
            cancelled = (err == "__cancel__")
            if on_done:
                try:
                    on_done(res, cancelled, "" if cancelled else err)
                except Exception as e:
                    _mfm_log("file op on_done error: %r" % (e,))
            elif err and not cancelled:
                QMessageBox.critical(self, title, err)
            # r72: 平坦ビューは静的モデル（QStandardItemModel）なので、削除/移動/
            # Undo 後に自分で作り直す（放置すると「削除できない」ように見える）
            self._refresh_flat_view()
            # r146（ユーザー報告 2026-10-15「削除したものが残ったままの現象」）:
            # カラムは QFileSystemModel の «フォルダの見張り» 頼みで、
            # 消えた・増えたの通知は取りこぼすことがある（ネットワーク先や
            # OneDrive、短時間に大量に動かした時）。操作が終わった時点で、
            # 触ったフォルダだけ自分で読み直す。
            # r146: 自分で動かしたパスの stat の答えは捨てる（fast_stat）
            try:
                _fs.invalidate(*list(busy_paths or []))
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py _run_file_op(invalidate)")
            if not _diag_off("no_post_resync"):
                self._resync_touched(busy_paths)
                # 見張りを «今いる場所» へ付け直す（これを忘れると、以後その
                # フォルダの変化が永久に届かない＝反映されない）
                self._rearm_fs_watch([self._current_path])
        notifier.progress.connect(on_progress)
        notifier.finished.connect(done)
        threading.Thread(target=run, daemon=True, name="mfm-file-op").start()

    def _delete_confirm(self, paths: List[str]):
        # 何を消すかを必ず見せる（誤対象＝パンくず等の事故防止）
        names = [os.path.basename(p.rstrip("/\\")) or p for p in paths]
        shown = "\n".join("  " + n for n in names[:10])
        if len(names) > 10:
            shown += "\n  …他 %d 件" % (len(names) - 10)
        if not self._busy_guard(paths, tr("削除", "Delete")):
            return
        msg = f"{len(paths)} 件を削除しますか？\n\n{shown}"
        ret = QMessageBox.warning(self, tr("削除の確認", "Confirm delete"), msg,
                                  QMessageBox.Yes | QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return
        # r62: 削除は «同一ボリュームの作業ごみ箱» へ移動して Undo 可能にする
        # （作業ごみ箱へ移せないものは OS のごみ箱へ直接＝Undo 不可）。
        # ワーカー＋進捗ダイアログ。キャンセル時も完了分は Undo 記録に残す。
        from core.undo_stack import delete_to_work_trash, make_delete_op
        pairs = []
        done_direct = []

        # r144: «Manager 自身が掴んでいる» フォルダは Windows では消せない
        # （QFileSystemModel の監視スレッドがハンドルを握る）。削除を始める前に
        # 手放しておく。ワーカースレッドからは Qt を触れないので «前» にやる。
        self._release_fs_handles_for(paths, level=1)

        def _run(targets, retried):
            failed_paths = []

            def work(cb):
                return delete_to_work_trash(targets, progress_cb=cb, pairs=pairs,
                                            failed_paths=failed_paths)

            def done(res, cancelled, err):
                failed, direct = [], []
                if res is not None:
                    _op, failed, direct = res
                done_direct.extend(direct)
                # まだ掴まれている → モデルごと作り直して «一度だけ» 再試行する。
                if failed_paths and not retried and not cancelled and not err:
                    self._release_fs_handles_for(failed_paths, level=2)
                    _run(list(failed_paths), True)
                    return
                op = make_delete_op(pairs)
                if op is not None:
                    self._undo_stack().push(op)
                if err:
                    QMessageBox.critical(self, tr("削除", "Delete"), err)
                elif failed:
                    QMessageBox.warning(self, tr("削除エラー", "Delete Error"),
                                        tr("%d 件の削除に失敗しました:\n",
                                           "Failed to delete %d item(s):\n") % len(failed) +
                                        "\n".join(failed))
                elif cancelled:
                    self.status_message.emit(
                        tr("削除をキャンセルしました（完了 %d 件）",
                           "Delete cancelled (%d done)") % (len(pairs) + len(done_direct)))
                else:
                    hint = "（Ctrl+Z で元に戻せます）" if op is not None else ""
                    self.status_message.emit(
                        tr("%d 件を削除しました%s", "Deleted %d item(s)%s")
                        % (len(paths), hint))
                self._check_current_gone_async()
            self._run_file_op("削除", work, done, total=len(targets),
                              busy_paths=list(targets))

        _run(list(paths), False)

    def _reveal_path(self, target: str):
        """エクスプローラーで表示（r146: 失敗を黙らせない）。

        r145 までは reveal_in_explorer を直接 connect していたので、
        失敗しても «何も起きない» だけでユーザーには理由が届かなかった。"""
        from core.file_operations import reveal_in_explorer, FileOperationError
        try:
            reveal_in_explorer(target)
        except FileOperationError as e:
            self.status_message.emit(
                tr("エクスプローラーで表示できません: %s",
                   "Cannot show in Explorer: %s") % e)
        except Exception as e:
            _mfm_log("reveal failed: %r" % (e,))
            self.status_message.emit(
                tr("エクスプローラーで表示できません", "Cannot show in Explorer"))

    # ── Undo / Redo（r62、Explorer 相当: 名前変更・移動・コピー・削除） ─────
    def _undo_stack(self):
        from core.undo_stack import get_undo_stack
        return get_undo_stack()

    def _undo_op(self):
        st = self._undo_stack()
        if not st.can_undo():
            self.status_message.emit(tr("元に戻す操作はありません", "Nothing to undo"))
            return
        label = st.undo_label()

        def done(res, cancelled, err):
            if err:
                QMessageBox.warning(self, tr("元に戻す", "Undo"),
                                tr("元に戻せませんでした:\n%s",
                                   "Could not undo:\n%s") % err)
            else:
                self.status_message.emit(tr("元に戻しました: ", "Undone: ") + label)
        self._run_file_op("元に戻す: " + label, lambda cb: st.undo(), done)

    def _redo_op(self):
        st = self._undo_stack()
        if not st.can_redo():
            self.status_message.emit(tr("やり直す操作はありません", "Nothing to redo"))
            return
        label = st.redo_label()

        def done(res, cancelled, err):
            if err:
                QMessageBox.warning(self, tr("やり直す", "Redo"),
                                tr("やり直せませんでした:\n%s",
                                   "Could not redo:\n%s") % err)
            else:
                self.status_message.emit(tr("やり直しました: ", "Redone: ") + label)
        self._run_file_op("やり直す: " + label, lambda cb: st.redo(), done)

    def _ask_drop_action(self, gpos, n: int):
        """ドロップ時に «どうするか» を選ばせる（r146、持ち越し課題）。

        Unreal Engine の D&D と同じ考え方。«落とした瞬間に移動» は取り返しが
        付きにくく、Ctrl を押し忘れただけで移動になるのが怖い、という話だった。
        修飾キーを押していた時は Explorer と同じで即実行（呼び出し側で判定）。
        設定で切れば従来どおり «即移動»。

        戻り値: "move" / "copy" / "advanced" / None（取りやめ）"""
        if not bool(self._sm.get(self.DROP_MENU_KEY, True)):
            return "move"
        from core.compat import QMenu
        from ui.dcc_header import glyph_icon
        menu = _bp().QMenu(self)
        cnt = tr("（%d 件）", " (%d)") % n if n > 1 else ""
        a_move = menu.addAction(glyph_icon("➡"), tr("ここに移動", "Move Here") + cnt)
        a_copy = menu.addAction(glyph_icon("📋"), tr("ここにコピー", "Copy Here") + cnt)
        a_adv = menu.addAction(glyph_icon("🧩"),
                               tr("高度なコピー…", "Advanced Copy…") + cnt)
        a_adv.setToolTip(tr("名前を付け替えながらコピーします",
                            "Copy while renaming"))
        menu.addSeparator()
        menu.addAction(tr("キャンセル", "Cancel"))
        menu.setToolTipsVisible(True)
        act = menu.exec(gpos) if hasattr(menu, "exec") else menu.exec_(gpos)
        if act is a_move:
            return "move"
        if act is a_copy:
            return "copy"
        if act is a_adv:
            return "advanced"
        return None

    def _advanced_copy_to(self, paths: List[str], dest_dir: str):
        """落とし先へ «名前を付け替えながら» コピーする（r146）。"""
        from ui.duplicate_dialog import DuplicateDialog
        from core.file_operations import duplicate_items
        from core.undo_stack import CopyOp
        if not self._busy_guard(list(paths) + [dest_dir],
                                tr("高度なコピー", "Advanced Copy")):
            return
        dlg = DuplicateDialog(paths, parent=self.window(), dest_dir=dest_dir)
        # モーダルのまま（例外）: ここで決まる «名前の付け方» を使って
        # 直後に実処理を行うため、返り値を待つ必要がある。
        if (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()) != QDialog.Accepted:
            return
        specs = dlg.specs()
        inside = dlg.rename_inside()
        results: List[str] = []

        def work(cb):
            duplicate_items(specs, progress_cb=cb, results=results,
                            rename_inside=inside)
            return results

        def done(res, cancelled, err):
            pairs = [(s, d) for (s, d) in specs if d in results]
            if pairs:
                self._undo_stack().push(CopyOp(pairs))
            if err:
                QMessageBox.critical(self, tr("高度なコピー", "Advanced Copy"), err)
            elif cancelled:
                self.status_message.emit(
                    tr("高度なコピーをキャンセルしました（完了 %d 件）",
                       "Advanced copy cancelled (%d done)") % len(results))
            else:
                self.status_message.emit(
                    tr("高度なコピー: %d 件（Ctrl+Z で元に戻せます）",
                       "Advanced copy: %d item(s) (Ctrl+Z to undo)") % len(results))
        self._run_file_op(tr("高度なコピー", "Advanced Copy"), work, done,
                          total=len(specs),
                          busy_paths=([s for s, _d in specs] + [dest_dir]
                                      + [d for _s, d in specs]))

    def _on_files_dropped(self, paths: List[str], dest_dir: str,
                          forced=None, gpos=None):
        """カラム間 D&D の実行（r146 でメニュー対応）。

        forced … "copy" / "move"（修飾キーで確定）/ None（メニューで選ばせる）
        同名が既にある時は «上書き / 名前を変えて / スキップ» を選ばせる。"""
        # r146: 旧シグネチャ（第 3 引数が move: bool）でも動くようにしておく。
        # 内部の呼び出しは全部直したが、真偽値が来た時に «コピー» に化けると
        # 気づきにくい事故になる（False は «選ばせる» ではなく «コピー»）。
        if forced is True:
            forced = "move"
        elif forced is False:
            forced = "copy"
        action = forced
        if action is None:
            from core.compat import QCursor
            action = self._ask_drop_action(gpos or QCursor.pos(), len(paths))
        if action is None:
            self.status_message.emit(tr("取りやめました", "Cancelled"))
            return
        if action == "advanced":
            if _fs.isdir(dest_dir):
                self._advanced_copy_to(list(paths), os.path.normpath(dest_dir))
            return
        move = (action == "move")
        from core.file_operations import (move_items, copy_items,
                                          CONFLICT_RENAME, CONFLICT_SKIP)
        from core.undo_stack import MoveOp, CopyOp

        dest_dir = os.path.normpath(dest_dir)
        if not _fs.isdir(dest_dir):
            self.status_message.emit(tr("移動先が見つかりません", "Destination not found"))
            return
        # 自分自身・自分の中への移動、同じ場所への移動は捨てる
        srcs = []
        dn = os.path.normcase(dest_dir)
        for p in paths:
            if not p or not os.path.lexists(p):
                continue
            pn = os.path.normcase(os.path.normpath(p))
            if pn == dn:
                continue
            if _fs.isdir(p) and dn.startswith(pn + os.sep):
                self.status_message.emit(
                    tr("フォルダを自分の中へは移動できません: %s",
                       "Cannot move a folder into itself: %s") % os.path.basename(p))
                continue
            if move and os.path.normcase(os.path.dirname(pn)) == dn:
                continue      # 同じフォルダ内への移動は何もしない
            srcs.append(p)
        srcs = CappedColumnView._drop_ancestor_dirs(srcs)
        if not srcs:
            return

        # 衝突の解決。ダイアログはワーカーから呼べないので «先に» まとめて聞く。
        from ui.conflict_dialog import ConflictDialog
        decisions = {}
        sticky = None
        pending = [p for p in srcs
                   if os.path.lexists(os.path.join(dest_dir, os.path.basename(p)))]
        for i, p in enumerate(pending):
            if sticky is not None:
                decisions[os.path.normcase(p)] = sticky
                continue
            dlg = ConflictDialog(p, os.path.join(dest_dir, os.path.basename(p)),
                                 remaining=len(pending) - i - 1, parent=self.window())
            # モーダルのまま（r119 の «止めない» 原則の例外）:
            # 1 件ずつの «上書きするか» を決めないとコピー処理が進まない。
            ok = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
            if not ok or dlg.choice() is None:
                self.status_message.emit(tr("移動を中止しました", "Move cancelled"))
                return
            decisions[os.path.normcase(p)] = dlg.choice()
            if dlg.apply_to_all():
                sticky = dlg.choice()

        def conflict_cb(src, dest):
            return decisions.get(os.path.normcase(src), CONFLICT_RENAME)

        # 全部スキップなら何もしない
        if pending and all(v == CONFLICT_SKIP for v in decisions.values()) \
           and len(pending) == len(srcs):
            self.status_message.emit(tr("移動する項目がありません", "Nothing to move"))
            return

        pairs: List[tuple] = []
        results: List[str] = []
        label = tr("移動", "Move") if move else tr("コピー", "Copy")

        def work(cb):
            if move:
                move_items(srcs, dest_dir, progress_cb=cb, results=results,
                           conflict_cb=conflict_cb, pairs=pairs)
            else:
                copy_items(srcs, dest_dir, progress_cb=cb, results=results)
            return results

        def done(res, cancelled, err):
            if move and pairs:
                self._undo_stack().push(MoveOp(pairs))
            elif not move and results:
                self._undo_stack().push(
                    CopyOp([(s, d) for s, d in zip(srcs, results)]))
            if err:
                QMessageBox.critical(self, label, err)
            elif cancelled:
                self.status_message.emit(
                    tr("%s をキャンセルしました（完了 %d 件）",
                       "%s cancelled (%d done)") % (label, len(results)))
            else:
                self.status_message.emit(
                    tr("%s: %d 件 → %s（Ctrl+Z で元に戻せます）",
                       "%s: %d item(s) → %s (Ctrl+Z to undo)")
                    % (label, len(results), dest_dir))
            self._refresh_flat_view()

        # 移動元・移動先の «結果のパス» も走っている間は触らせない
        self._run_file_op(label, work, done, total=len(srcs),
                          busy_paths=(list(srcs) + [dest_dir]))

    def _duplicate_selected(self, paths: List[str] = None):
        """Ctrl+D / 右クリック「複製」: 同じ場所へ «元名_Copy» で複製（r67）。
        ダイアログで名前 or Search/Replace（＋サブフォルダ以下へ適用）を指定。
        ワーカー＋進捗、Undo は CopyOp（複製先を消す）。"""
        paths = list(paths) if paths else self._operation_targets()
        paths = [p for p in paths if p and os.path.lexists(p)]
        if not paths:
            self.status_message.emit(tr("複製する項目を選択してください",
                                        "Select items to duplicate"))
            return
        from ui.duplicate_dialog import DuplicateDialog
        from core.file_operations import duplicate_items
        from core.undo_stack import CopyOp
        dlg = DuplicateDialog(paths, parent=self.window())
        # モーダルのまま（例外）: ここで決まる «複製の仕方» を使って
        # 直後に実処理を行うため、返り値を待つ必要がある。
        if (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()) != QDialog.Accepted:
            return
        specs = dlg.specs()
        inside = dlg.rename_inside()
        if not self._busy_guard([s for s, _d in specs], tr("複製", "Duplicate")):
            return
        results: List[str] = []

        def work(cb):
            duplicate_items(specs, progress_cb=cb, results=results, rename_inside=inside)
            return results

        def done(res, cancelled, err):
            pairs = [(s, d) for (s, d) in specs if d in results]
            if pairs:
                self._undo_stack().push(CopyOp(pairs))
            if err:
                QMessageBox.critical(self, tr("複製", "Duplicate"), err)
            elif cancelled:
                self.status_message.emit(
                    tr("複製をキャンセルしました（完了 %d 件）",
                       "Duplicate cancelled (%d done)") % len(results))
            else:
                self.status_message.emit(
                    tr("複製: %d 件（Ctrl+Z で元に戻せます）",
                       "Duplicated %d item(s) (Ctrl+Z to undo)") % len(results))
        # r146（ユーザー報告 2026-10-15「複製先をリネーム出来てしまいました」）:
        # 走っている間は **複製元も複製先も** 触らせない。
        self._run_file_op("複製", work, done, total=len(specs),
                          busy_paths=([s for s, _d in specs]
                                      + [d for _s, d in specs]))

    def _operation_targets(self) -> List[str]:
        """キーボード操作（Ctrl+C/X/V, Delete）の対象。
        本体の選択にはパンくず（上位カラムで選択表示されている祖先フォルダ）が
        含まれるため、右クリックと同じく _drop_ancestor_dirs で除外する
        （r59。除外しないと Delete キーで祖先フォルダごと削除対象になる）。"""
        try:
            # r72: 平坦ビュー／共通子フォルダカラムにフォーカスがある時は、
            # その «最下層の選択物» だけを対象にする。従来は本体（カラム）の
            # 選択＝平坦表示の元になった上位フォルダが対象になり、平坦ビューで
            # Delete を押すと上のフォルダが削除対象になっていた（指摘）。
            deep = self._deep_view_targets()
            if deep is not None:
                return deep
            return CappedColumnView._drop_ancestor_dirs(self._get_selected_paths())
        except Exception:
            return []

    def _deep_view_targets(self) -> Optional[List[str]]:
        """フォーカスが平坦ビュー／共通子フォルダカラムにあれば、その選択パス
        （共通カラムは選択項目の実フォルダ群）。無ければ None（本体の選択へ）。"""
        try:
            fw = QApplication.focusWidget()
            if fw is None:
                return None
            fc = getattr(self, "_flat_col", None)
            if fc is not None and fc.isVisible() and (fc is fw or fc.isAncestorOf(fw)):
                return [p for p in fc.selected_paths() if p]
            for col in getattr(self, "_common_cols", []) or []:
                if col.isVisible() and (col is fw or col.isAncestorOf(fw)):
                    return [p for p in col.selected_sources() if p]
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:2929 _deep_view_targets")
        return None

    def _clipboard_copy(self):
        self._set_clipboard(self._operation_targets(), move=False)

    def _clipboard_cut(self):
        self._set_clipboard(self._operation_targets(), move=True)

    def _paste_target_dir(self) -> Optional[str]:
        """貼り付け先: 単一フォルダ選択中ならそのフォルダ、無ければ現在地。
        r70: 空白右クリックの「貼り付け」はそのカラムのフォルダ。"""
        ov = getattr(self, "_paste_override_dir", None)
        if ov and _fs.isdir(ov):
            return ov
        sel = self._operation_targets()
        if len(sel) == 1 and _fs.isdir(sel[0]):
            return sel[0]
        return self._current_path if _fs.isdir(self._current_path) else None

    def _clipboard_paste(self):
        mime = QApplication.clipboard().mimeData()
        if not mime or not mime.hasUrls():
            return
        paths = [u.toLocalFile() for u in mime.urls() if u.toLocalFile()]
        paths = [p for p in paths if _fs.exists(p)]
        if not paths:
            return

        # 移動/コピー判定（Explorer の Preferred DropEffect を尊重）
        move = False
        if mime.hasFormat("Preferred DropEffect"):
            data = bytes(mime.data("Preferred DropEffect"))
            if len(data) >= 4:
                effect = struct.unpack("<I", data[:4])[0]
                move = bool(effect & self._DROPEFFECT_MOVE)

        target = self._paste_target_dir()
        if not target:
            QMessageBox.warning(self, tr("貼り付け", "Paste"),
                                tr("貼り付け先フォルダを特定できません。",
                                   "Cannot determine the destination folder."))
            return

        # 同一フォルダへの移動は無意味なのでコピーへ降格
        if move and all(os.path.normpath(os.path.dirname(p)) ==
                        os.path.normpath(target) for p in paths):
            move = False

        if move:
            QApplication.clipboard().clear()  # 切り取りは1回限り
        self._transfer_with_progress(paths, target, move,
                                     "移動" if move else "貼り付け")

    def _transfer_with_progress(self, paths: List[str], target: str, move: bool, verb: str):
        """コピー/移動をワーカー＋進捗ダイアログで実行し、Undo 記録を積む（r62）。
        キャンセル時も完了した分は Undo 可能な記録として残す。"""
        from core.undo_stack import MoveOp, CopyOp
        if not self._busy_guard(list(paths) + [target], verb):
            return
        results: List[str] = []
        fn = move_items if move else copy_items
        # r98: «Manager で開いているフォルダ» は監視ハンドルのせいで Windows では
        # 移動できない。フォルダが対象に含まれる移動の前に手放しておく。
        if move:
            # r144: 1 件目のフォルダだけでなく «対象に含まれるフォルダ全部» を
            # 手放す（削除と同じ理由。掴みっぱなしが 1 つでもあると弾かれる）。
            self._release_fs_handles_for(paths, level=1)

        def work(cb):
            fn(paths, target, progress_cb=cb, results=results)
            return results

        def done(res, cancelled, err):
            pairs = list(zip(paths, results))
            if pairs:
                self._undo_stack().push(MoveOp(pairs) if move else CopyOp(pairs))
            if err:
                QMessageBox.critical(self, verb, err)
            elif cancelled:
                self.status_message.emit(
                    tr("%sをキャンセルしました（完了 %d 件）",
                       "%s cancelled (%d done)") % (verb, len(results)))
            else:
                self.status_message.emit(
                    tr("%s完了: %d 件", "%s finished: %d item(s)")
                    % (verb, len(results)))
            self._check_current_gone_async()          # r98/r107
        self._run_file_op(verb, work, done, total=len(paths),
                          busy_paths=list(paths) + [target])

    def _clipboard_delete(self):
        paths = self._operation_targets()
        if paths:
            self._delete_confirm(paths)

    def _clipboard_paste_into(self, folder: str):
        """右クリックしたカラムのフォルダへ貼り付け（_clipboard_paste の貼り付け先を差し替え）。"""
        self._paste_override_dir = folder
        try:
            self._clipboard_paste()
        finally:
            self._paste_override_dir = None

    def _copy_dialog(self, paths: List[str]):
        dst = pick_directory(self, tr("コピー先を選択", "Select a destination"), self._current_path,
                                               self._DIR_DLG_OPTS)
        if dst:
            self._transfer_with_progress(paths, dst, False, "コピー")

    def _move_dialog(self, paths: List[str]):
        dst = pick_directory(self, tr("移動先を選択", "Select a destination"), self._current_path,
                                               self._DIR_DLG_OPTS)
        if dst:
            self._transfer_with_progress(paths, dst, True, "移動")

    def _set_clipboard(self, paths: List[str], move: bool):
        """選択ファイルをクリップボードへ。Explorer と相互にペースト可能な形式で格納。"""
        paths = [p for p in paths if p and _fs.exists(p)]
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        effect = self._DROPEFFECT_MOVE if move else self._DROPEFFECT_COPY
        mime.setData("Preferred DropEffect",
                     _QtCore.QByteArray(struct.pack("<I", effect)))
        QApplication.clipboard().setMimeData(mime)
        self.status_message.emit(
            ("切り取り" if move else "コピー") + f": {len(paths)} 件")

    def _install_clipboard_actions(self):
        """子ビューにフォーカスがあっても効くショートカットを登録する。"""
        for seq, slot in [
            (QKeySequence.Copy,  self._clipboard_copy),
            (QKeySequence.Cut,   self._clipboard_cut),
            (QKeySequence.Paste, self._clipboard_paste),
            (QKeySequence.Delete, self._clipboard_delete),
            (QKeySequence(Qt.Key_F2), lambda _c=False: self._rename_inline()),
            (QKeySequence.Undo, lambda _c=False: self._undo_op()),
            (QKeySequence("Ctrl+Y"), lambda _c=False: self._redo_op()),
            (QKeySequence("Ctrl+D"), lambda _c=False: self._duplicate_selected()),
            (QKeySequence("Ctrl+Shift+N"), lambda _c=False: self._create_new_item("folder")),
            (QKeySequence("Ctrl+Shift+T"), lambda _c=False: self._create_new_item("text")),
        ]:
            act = QAction(self)
            act.setShortcut(seq)
            act.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            act.triggered.connect(slot)
            self.addAction(act)

    # ------------------------------------------------------------------
    # プロパティ（r124）
    # ------------------------------------------------------------------
    @staticmethod
    def _readonly_targets(paths, recursive: bool):
        """読み取り専用を付け外しする実体の一覧。

        r144: 選んだものは «フォルダでも» それ自身が対象（ユーザー指摘
        2026-10-08）。Windows はフォルダにも FILE_ATTRIBUTE_READONLY を持たせる
        ので、属性としては確かに «付く／付いている»。中身を保護する効果は無いが、
        状態として出さない／触らせないのは筋が通らない。
        recursive=True の時は配下のフォルダとファイルも全部対象にする。"""
        out, seen = [], set()
        def _add(p):
            k = os.path.normcase(os.path.abspath(p))
            if k not in seen:
                seen.add(k)
                out.append(p)
        for p in paths:
            try:
                is_dir = _fs.isdir(p)
            except OSError:
                continue
            _add(p)
            if not (is_dir and recursive):
                continue
            for root, dirs, files in os.walk(p):
                for d in dirs:
                    _add(os.path.join(root, d))
                for f in files:
                    _add(os.path.join(root, f))
        return out

    def _show_properties(self, paths):
        """プロパティ（r124: 読み取り専用スイッチだけはその場で変更できる）。

        - Perforce 同期のファイルは読み取り専用で降りてくるため、«外す» を
          エクスプローラーまで出て行わずに済むようにする。
        - 複数選択でも読み取り専用の変更だけは行える。
        - フォルダを選んだ時は «下層フォルダまで含める» を選べる。
        他の属性は表示のみ＝誤操作の余地を作らない（ユーザー指示 2026-10-06）。"""
        from core.compat import QCheckBox, QFormLayout, QLabel, QPushButton
        if isinstance(paths, str):
            paths = [paths]
        paths = [p for p in (paths or []) if p]
        if not paths:
            return
        import datetime
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("プロパティ", "Properties"))
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        has_dir = any(os.path.isdir(p) for p in paths)
        if len(paths) == 1:
            path = paths[0]
            info = Path(path)
            try:
                st = info.stat()
            except OSError as e:
                QMessageBox.warning(self, tr("プロパティ", "Properties"), str(e))
                return
            form.addRow(tr("名前:", "Name:"), QLabel(info.name))
            pl = QLabel(path)
            pl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(tr("パス:", "Path:"), pl)
            if not os.path.isdir(path):
                form.addRow(tr("サイズ:", "Size:"), QLabel(format_size(st.st_size)))
            form.addRow(tr("更新日時:", "Modified:"),
                        QLabel(str(datetime.datetime.fromtimestamp(st.st_mtime))))
            form.addRow(tr("種類:", "Type:"), QLabel(get_file_type_category(path)))
        else:
            n_dir = sum(1 for p in paths if os.path.isdir(p))
            n_file = len(paths) - n_dir
            form.addRow(tr("対象:", "Items:"),
                        QLabel(tr("%d 件（フォルダ %d / ファイル %d）",
                                  "%d item(s) (%d folder(s) / %d file(s))")
                               % (len(paths), n_dir, n_file)))
            total = 0
            for p in paths:
                try:
                    if not os.path.isdir(p):
                        total += os.path.getsize(p)
                except OSError:
                    pass
            if n_file:
                form.addRow(tr("サイズ（ファイル分）:", "Size (files only):"),
                            QLabel(format_size(total)))
            names = ", ".join(os.path.basename(p.rstrip("/\\")) for p in paths[:8])
            if len(paths) > 8:
                names += " …"
            nl = QLabel(names)
            nl.setWordWrap(True)
            form.addRow(tr("内訳:", "Selection:"), nl)
        lay.addLayout(form)

        rec_cb = QCheckBox(tr("下層フォルダまで含める", "Include subfolders"), dlg)
        if not has_dir:
            rec_cb.setVisible(False)

        cb = QCheckBox(tr("読み取り専用", "Read-only"), dlg)
        state_lb = QLabel("", dlg)
        state_lb.setWordWrap(True)

        def _targets():
            return self._readonly_targets(paths, rec_cb.isChecked())

        def _refresh_state():
            # r144: フォルダ自身も対象なので «対象なし» は基本起きない。
            # 一部だけ読み取り専用の時は中間状態で見せる（クリックで ON）。
            tg = _targets()
            ro = [p for p in tg if is_read_only(p)]
            cb.blockSignals(True)
            cb.setEnabled(bool(tg))
            if not tg:
                cb.setCheckState(Qt.Unchecked)
            elif len(ro) == len(tg):
                cb.setCheckState(Qt.Checked)
            elif ro:
                cb.setCheckState(Qt.PartiallyChecked)
            else:
                cb.setCheckState(Qt.Unchecked)
            cb.blockSignals(False)
            if not tg:
                state_lb.setText(tr("対象がありません。", "Nothing to change."))
            elif len(ro) in (0, len(tg)):
                state_lb.setText(tr("対象 %d 件。", "%d item(s) affected.")
                                 % len(tg))
            else:
                state_lb.setText(tr("対象 %d 件（うち読み取り専用 %d 件）。",
                                    "%d item(s) affected (%d currently read-only).")
                                 % (len(tg), len(ro)))

        def _apply(on):
            tg = _targets()
            if not tg:
                _refresh_state()
                return
            if len(tg) > 200:
                ret = QMessageBox.question(
                    dlg, tr("読み取り専用", "Read-only"),
                    tr("%d 件の読み取り専用属性を変更します。よろしいですか？",
                       "Change the read-only attribute of %d item(s)?") % len(tg),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if ret != QMessageBox.Yes:
                    _refresh_state()
                    return
            done, fails = 0, []
            for p in tg:
                ok, err = set_read_only(p, bool(on))
                if ok:
                    done += 1
                else:
                    fails.append((os.path.basename(p), err))
            if fails:
                detail = "\n".join("  %s — %s" % x for x in fails[:8])
                QMessageBox.warning(
                    dlg, tr("読み取り専用", "Read-only"),
                    tr("%d 件は変更できませんでした:\n%s",
                       "Could not change %d file(s):\n%s") % (len(fails), detail))
            self.status_message.emit(
                (tr("読み取り専用にしました: %d 件", "Set read-only: %d item(s)") if on
                 else tr("読み取り専用を外しました: %d 件",
                         "Cleared read-only: %d item(s)")) % done)
            _refresh_state()
            try:
                self._column_view.viewport().update()
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py _show_properties")

        # r144: 中間状態を自前で入れるので toggled ではなく clicked で拾う。
        cb.clicked.connect(lambda on: _apply(on))
        rec_cb.toggled.connect(lambda _on: _refresh_state())
        lay.addWidget(cb)
        if has_dir:
            lay.addWidget(rec_cb)
        lay.addWidget(state_lb)
        if has_dir:
            note = QLabel(tr("※ フォルダの読み取り専用属性は、Windows では中身の"
                             "保護には使われません（属性自体は設定されます）。",
                             "Note: Windows does not use a folder's read-only "
                             "attribute to protect its contents."), dlg)
            note.setWordWrap(True)
            note.setEnabled(False)
            lay.addWidget(note)
        _refresh_state()

        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton(tr("閉じる", "Close"), dlg)
        close_btn.setAutoDefault(False)
        close_btn.clicked.connect(dlg.accept)
        row.addWidget(close_btn)
        lay.addLayout(row)
        # r124: 非モーダル（原則どおり Manager を止めない）。属性を見ながら
        # カラムを触れた方が都合が良い。対象は «開いた時の選択» なので
        # reuse=False で毎回作り直す。
        from ui.dialog_util import show_tool_window, pick_directory, pick_open_file
        show_tool_window(self, "_props_dlg", lambda: dlg, reuse=False)
