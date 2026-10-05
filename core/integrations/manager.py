# -*- coding: utf-8 -*-
"""統括マネージャ: プロバイダ検出、状態キャッシュ、ワーカー実行、UIへの通知。

UIスレッドから呼んでよいのは status_for() / providers_for() / actions_for() /
request_status() / refresh() / summary() のみ（全て辞書参照 or キュー投入）。
"""
from core.diag import swallow as _swallow  # r112

import os
import queue
import threading
import time

from core.compat import QObject, Signal

# r121: ログは «ツールフォルダ» ではなく «ユーザーフォルダ» へ出す。
# 従来は __file__ から 3 つ上（＝ツールフォルダ直下）に書いていたが、
# EXE 版では展開先の一時フォルダになるため **ログが一切残らず**、
# 「EXE だけ連携が効かない」の原因究明ができなかった（2026-10-05）。
# Maya 連携ログ（mfm_maya.log）と同じ置き場に揃える。
def _resolve_log_path():
    try:
        d = os.path.join(os.path.expanduser("~"), ".maya_file_manager", "logs")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "mfm_integrations.log")
    except OSError:
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "mfm_integrations.log")


_LOG_PATH = _resolve_log_path()
try:
    with open(_LOG_PATH, "w", encoding="utf-8") as _f:
        _f.write("=== 外部サービス連携ログ（起動ごとに上書き） ===\n")
except OSError:
    pass


def _ilog(msg: str):
    """連携の検出・判定結果をツールフォルダ直下へ記録（調査用、常時）。"""
    try:
        import datetime
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), msg))
    except OSError:
        pass

from .base import norm, resolve_link_prefix
from .git_provider import GitProvider
from .svn_provider import SvnProvider
from .p4_provider import P4Provider
from .cloud_provider import CloudProvider


class IntegrationManager(QObject):
    # ディレクトリ（表示上のパス）の状態が更新された
    status_updated = Signal(str)
    # 検出が完了した
    detected = Signal()
    # 操作（CLI）が終了した: (ok, provider_label, message)。ワーカーから emit
    # されるため UI 側は QueuedConnection で受ける（Signal 既定で自動）。
    action_finished = Signal(bool, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.providers = [GitProvider(), SvnProvider(), P4Provider(),
                          CloudProvider()]
        for _p in self.providers:
            _p.log = _ilog   # プロバイダ内の判定経緯もログへ
            _p.report = (lambda ok, msg, _label=_p.label:
                         self._on_action_report(ok, _label, msg))
        self.enabled = True
        self._detected = False
        self._status = {}        # norm(表示パス) -> (provider_key, state)
        self._dir_providers = {} # norm(表示ディレクトリ) -> [(provider, root, eff_dir)]
        self._dir_ts = {}        # norm(表示ディレクトリ) -> 取得時刻
        self._queue = queue.Queue()
        self._pending = set()
        self._lock = threading.Lock()
        self._worker = None

    def _on_action_report(self, ok: bool, label: str, msg: str):
        _ilog("action %s ok=%s: %s" % (label, ok, (msg or "").replace("\n", " | ")[:300]))
        try:
            self.action_finished.emit(bool(ok), str(label), str(msg or ""))
        except Exception as _e:
            _swallow(_e, "core/integrations/manager.py:72 _on_action_report")

    # ── 起動 ─────────────────────────────────────────────────────────────
    def start(self, enabled: bool = True):
        self.enabled = bool(enabled)
        if not self.enabled:
            return
        if self._worker is None:
            self._worker = threading.Thread(target=self._loop, daemon=True,
                                            name="mfm-integrations")
            self._worker.start()
        self._queue.put(("detect", None))

    def _loop(self):
        while True:
            try:
                kind, arg = self._queue.get()
                if kind == "detect":
                    self._do_detect()
                elif kind == "status":
                    self._do_status(arg)
            except Exception as _e:
                _swallow(_e, "core/integrations/manager.py:94 _loop")

    def _do_detect(self):
        for p in self.providers:
            try:
                p.detect()
            except Exception as e:
                p.available = False
                _ilog("detect %s: 例外 %r" % (p.key, e))
            _ilog("detect %s: available=%s info=%r" % (p.key, p.available, p.info))
        self._detected = True
        try:
            self.detected.emit()
        except Exception as _e:
            _swallow(_e, "core/integrations/manager.py:108 _do_detect")

    # ── 状態取得（ワーカー） ───────────────────────────────────────────────
    def _do_status(self, directory: str):
        key = norm(directory)
        try:
            eff = resolve_link_prefix(directory)
            found = []
            merged = {}
            # r72: まずプロバイダ（ワークスペースの root）だけ確定して公開する。
            # 従来は fetch_status（p4 fstat 等、数秒かかる）の完了後にしか
            # _dir_providers が埋まらず、表示直後の右クリックで Perforce
            # サブメニューが «1回目だけ出ない» 原因になっていた。
            for p in self.providers:
                if not p.available:
                    continue
                try:
                    root = p.root_for(eff)
                except Exception as e:
                    _ilog("root_for %s %r: 例外 %r" % (p.key, eff, e))
                    root = None
                if not root:
                    continue
                found.append((p, root, eff))
            with self._lock:
                self._dir_providers[key] = found
            for p, root, _e in found:
                try:
                    states = p.fetch_status(root, eff)
                except Exception as e:
                    p.note_failure("fetch_status 例外")
                    _ilog("fetch_status %s %r: 例外 %r" % (p.key, eff, e))
                    states = {}
                _ilog("status %s dir=%r root=%r entries=%d"
                      % (p.key, eff, root, len(states)))
                effn = norm(eff)
                for ep, st in states.items():
                    # 実体パス → 表示パス（同じ basename で対応付け）
                    if os.path.dirname(ep) != effn:
                        continue
                    disp = os.path.join(key, os.path.basename(ep))
                    # 複数プロバイダが重なる場合は VCS を優先（クラウドは補助）
                    if disp in merged and p.key == "cloud":
                        continue
                    merged[disp] = (p.key, st)
            with self._lock:
                # 古いエントリを掃除してから差し替え
                for k in [k for k in self._status if os.path.dirname(k) == key]:
                    self._status.pop(k, None)
                self._status.update(merged)
                self._dir_providers[key] = found
                self._dir_ts[key] = time.monotonic()
            _ilog("dir %r (実体 %r): providers=%s" % (
                directory, eff, [p.key for p, _r, _e in found] or "なし"))
        finally:
            with self._lock:
                self._pending.discard(key)
        try:
            self.status_updated.emit(directory)
        except Exception as _e:
            _swallow(_e, "core/integrations/manager.py:168 _do_status")

    # ── UIスレッド API ─────────────────────────────────────────────────────
    def request_status(self, directory: str, force: bool = False):
        """表示中ディレクトリの状態取得をキューへ（重複は捨てる）。"""
        if not self.enabled or not directory:
            return
        key = norm(directory)
        with self._lock:
            if key in self._pending:
                return
            if not force and key in self._dir_ts:
                return   # 表示時＋手動更新の方針: 取得済みなら再取得しない
            self._pending.add(key)
        self._queue.put(("status", directory))

    def refresh(self, directory: str):
        """手動更新: プロバイダ側のキャッシュも破棄して再取得。"""
        key = norm(directory)
        with self._lock:
            self._dir_ts.pop(key, None)
            provs = list(self._dir_providers.get(key, []))
        for p, root, _eff in provs:
            inv = getattr(p, "invalidate_status", None)
            if callable(inv):
                try:
                    inv(root)
                except Exception as _e:
                    _swallow(_e, "core/integrations/manager.py:196 refresh")
        self.request_status(directory, force=True)

    def status_for(self, path: str):
        """(provider_key, state) or None。辞書参照のみ。"""
        if not self.enabled:
            return None
        return self._status.get(norm(path))

    def providers_for(self, directory: str):
        with self._lock:
            return list(self._dir_providers.get(norm(directory), []))

    def actions_for(self, paths):
        """右クリック用: [(provider_label, [(label, cb), ...]), ...]"""
        if not self.enabled or not paths:
            return []
        paths = [p for p in paths if p]
        # r72: 複数フォルダにまたがる選択（平坦ビュー）に対応する。従来は
        # paths[0] のフォルダだけでプロバイダを決め、他フォルダのファイルまで
        # その実体フォルダに basename で結合していた → 存在しないパスを p4 に
        # 渡して revert 等が失敗した。フォルダごとに実体パスへ変換し、
        # 同じ (プロバイダ, root) ごとに1グループにまとめる。
        dirs = []
        for x in paths:
            d = os.path.dirname(x)
            if d and d not in dirs:
                dirs.append(d)
        with self._lock:
            unknown = [d for d in dirs if norm(d) not in self._dir_providers]
        if unknown:
            # 未取得（表示直後など）: 取得を仕掛け、プロバイダ確定（root_for、
            # 数ms）だけ短時間待つ。状態（fstat 等）は待たない。これで
            # 「1回目の右クリックだけメニューが違う」を無くす（r72）。
            for d in unknown:
                _ilog("actions_for dir=%r: 未取得（request）" % d)
                self.request_status(d)
            deadline = time.monotonic() + 0.4
            while time.monotonic() < deadline:
                with self._lock:
                    if all(norm(d) in self._dir_providers for d in unknown):
                        break
                time.sleep(0.02)
        groups = {}          # (provider.key, root) -> (provider, root, [eff_paths])
        order = []
        for x in paths:
            d = os.path.dirname(x)
            for p, root, eff_dir in self.providers_for(d):
                key = (p.key, norm(root))
                if key not in groups:
                    groups[key] = (p, root, [])
                    order.append(key)
                groups[key][2].append(os.path.join(eff_dir, os.path.basename(x)))
        out = []
        multi_root = {}
        for key in order:
            multi_root[key[0]] = multi_root.get(key[0], 0) + 1
        for key in order:
            p, root, eff_paths = groups[key]
            try:
                acts = p.actions(root, eff_paths)
            except Exception:
                acts = []
            if not acts:
                continue
            label = p.label
            if p.key == "cloud":
                svc = p.service_for(root)
                label = svc[0] if svc else label
            if multi_root.get(p.key, 0) > 1:
                # 同じプロバイダで別ワークスペースが混在 → root 名で区別
                label = "%s (%s)" % (label, os.path.basename(root.rstrip("/\\")) or root)
            out.append((label, acts))
        return out

    def summary(self):
        return {p.key: (p.available, p.info) for p in self.providers}


_manager = None


def get_manager():
    global _manager
    if _manager is None:
        _manager = IntegrationManager()
    return _manager
