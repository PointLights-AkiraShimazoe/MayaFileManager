# -*- coding: utf-8 -*-
"""プロバイダ共通基盤（検出・コマンド実行・パス正規化・リンク解決）。"""
from core.diag import swallow as _swallow  # r112

import os
import shutil
import subprocess
import threading
import time
import time as _time   # r125: available プロパティから使う（別名で明示）

# ── 状態コード（アイコン描画・ツールチップで使う共通語彙） ─────────────
ST_CLEAN = "clean"          # 追跡済み・最新
ST_MODIFIED = "modified"    # 変更あり / チェックアウト中
ST_ADDED = "added"          # 追加予定
ST_DELETED = "deleted"      # 削除予定
ST_UNTRACKED = "untracked"  # 未追跡（デポに無い）
ST_CONFLICT = "conflict"    # 競合
ST_IGNORED = "ignored"      # 無視
ST_OUTDATED = "outdated"    # サーバーに新しい版がある（Perforce）
ST_LOCKED = "locked"        # 他者がロック中（Perforce: otherLock / SVN: lock）
ST_OTHER_OPEN = "other_open"  # 他者がチェックアウト中（Perforce: otherOpen）
# クラウド
ST_ONLINE_ONLY = "online_only"   # オンラインのみ（ローカルに実体なし）
ST_LOCAL = "local"               # ローカルにあり（必要に応じて解放され得る）
ST_PINNED = "pinned"             # 常にこのデバイスに保持
ST_SYNCING = "syncing"           # 同期中（判定できる場合のみ）

_NOWIN = {"CREATE_NO_WINDOW": 0x08000000}


def norm(p: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(p)) if p else ""
    except Exception:
        return ""


def which(*names):
    """PATH 上のコマンドを探す（最初に見つかったもの）。"""
    for n in names:
        try:
            p = shutil.which(n)
            if p:
                return p
        except Exception as _e:
            _swallow(_e, "core/integrations/base.py:44 which")
    return None


def first_existing(*paths):
    for p in paths:
        try:
            if p and os.path.isfile(p):
                return p
        except Exception as _e:
            _swallow(_e, "core/integrations/base.py:54 first_existing")
    return None


def program_files_candidates(*rel_paths):
    """Program Files / Program Files (x86) / LOCALAPPDATA 配下の候補を列挙。"""
    bases = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
             os.environ.get("ProgramW6432"), os.environ.get("LOCALAPPDATA")]
    out = []
    for b in bases:
        if not b:
            continue
        for r in rel_paths:
            out.append(os.path.join(b, r))
    return out


def run(cmd, cwd=None, timeout=8.0, input_text=None):
    """外部コマンドをウィンドウ無しで実行（ワーカースレッド専用）。
    Returns (returncode, stdout, stderr)。失敗は (-1, "", msg)。"""
    try:
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = _NOWIN["CREATE_NO_WINDOW"]
        env = dict(os.environ)
        env.setdefault("LC_ALL", "C")
        p = subprocess.run(cmd, cwd=cwd or None, capture_output=True,
                           timeout=timeout, input=input_text, env=env, **kw)
        return (p.returncode,
                p.stdout.decode("utf-8", "replace"),
                p.stderr.decode("utf-8", "replace"))
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:  # FileNotFoundError 等
        return -1, "", repr(e)


def spawn(cmd, cwd=None):
    """GUIツール等を待たずに起動する（ワーカー/UIどちらからでも可、即返る）。"""
    try:
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = subprocess.DETACHED_PROCESS | \
                subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(cmd, cwd=cwd or None, close_fds=True, **kw)
        return True
    except Exception:
        return False


def _readlink_component(p: str):
    """p 自身がリンク（symlink/ジャンクション）ならターゲットを返す。辿らない。"""
    try:
        t = os.readlink(p)
    except (OSError, ValueError):
        return None
    if not t:
        return None
    # Windows の \\?\ プレフィックスを除去
    if t.startswith("\\\\?\\"):
        t = t[4:]
    if not os.path.isabs(t):
        t = os.path.join(os.path.dirname(p), t)
    return os.path.normpath(t)


def resolve_link_prefix(path: str, max_depth: int = 8) -> str:
    """パスの各構成要素を上から見て、リンクがあれば実体へ置き換える。
    lstat/readlink のみ（stat でリンク先へ触らない）。ネットワーク先の
    実体は「文字列として」得られるだけで、ここでは I/O しない。"""
    try:
        p = os.path.normpath(path)
        for _ in range(max_depth):
            drive, rest = os.path.splitdrive(p)
            parts = rest.strip("\\/").split(os.sep) if rest.strip("\\/") else []
            cur = drive + os.sep if drive else os.sep
            replaced = False
            for i, comp in enumerate(parts):
                cur = os.path.join(cur, comp)
                t = _readlink_component(cur)
                if t:
                    tail = parts[i + 1:]
                    p = os.path.join(t, *tail) if tail else t
                    replaced = True
                    break
            if not replaced:
                return p
        return p
    except Exception:
        return path


def walk_up_for(path: str, marker_names, max_up: int = 40):
    """path から親方向へ marker（ファイル/フォルダ名）を探し、見つかった
    ディレクトリを返す。lstat のみ。"""
    try:
        cur = os.path.normpath(path)
        for _ in range(max_up):
            for m in marker_names:
                cand = os.path.join(cur, m)
                try:
                    os.lstat(cand)
                    return cur
                except OSError:
                    pass
            parent = os.path.dirname(cur)
            if not parent or parent == cur:
                return None
            cur = parent
    except Exception:
        return None
    return None


class Provider:
    """プロバイダ基底。サブクラスは detect()/find_root()/fetch_status()/
    actions() を実装する。全メソッドはワーカーから呼ばれる前提
    （actions() が返す callable は UI から呼ばれ、内部で spawn/ワーカー起動する）。"""

    key = "base"
    label = "Base"
    # 連続失敗でこの回数に達したら «一時的に» 休む
    MAX_FAILURES = 5
    # r125: 休む時間。**以前はここで永久に無効化していた。**
    # 連続失敗に達すると available=False のまま二度と戻らず、再度有効に
    # なるのはアプリを再起動して detect() が走る時だけだった。そのため
    # ネットワークの瞬断やクライアントビュー外のフォルダを数回開いただけで
    # 「Perforce の連携状態が途中で切れる」が起きていた
    # （ユーザー報告 2026-10-06）。休ませて、時間が経ったらまた試す。
    FAILURE_COOLDOWN_SEC = 60.0

    def __init__(self):
        self._failures = 0
        self._installed = False
        self._disabled_until = 0.0
        self.available = False
        self.info = {}
        self._lock = threading.Lock()
        self._root_cache = {}      # norm(dir) -> root or ""（"" = 非ワークスペース）
        # 判定経緯の記録先（manager が _ilog へ差し替える）。既定は捨てる。
        self.log = lambda *_a, **_k: None
        # 操作結果の通知先（manager が差し替える）: report(ok: bool, message: str)
        # ワーカースレッドから呼ばれる。UI への橋渡しは manager の Signal が行う。
        self.report = lambda ok, msg: None

    def run_cli_async(self, cmd, cwd, what: str, timeout: float = 120.0):
        """CLI をデーモンスレッドで実行し、終了時に self.report() へ結果を渡す。
        actions() が返す callable から使う（r58: 失敗を黙殺しない）。"""
        def _go():
            rc, out, err = run(cmd, cwd=cwd, timeout=timeout)
            ok, msg = self.classify_cli_result(rc, out, err)
            if ok:
                self.note_success()
                self.report(True, "%s: %s" % (what, msg))
            else:
                self.report(False, "%s に失敗しました。\n\n%s" % (what, msg))
        threading.Thread(target=_go, daemon=True, name="mfm-integ-cli").start()

    def cli_output_is_error(self, text: str) -> bool:
        """rc=0 でも出力がエラーを表す CLI（p4 等）向けのフック。"""
        return False

    def classify_cli_result(self, rc, out, err):
        """実行結果を (成功か, 画面に出す文言) に落とす。

        r121: **「一部は成功、一部は対象外」を失敗にしない** ためのフック。
        フォルダを指定した一括操作では「もう追加済みのファイル」等の
        警告が必ず混ざるが、それは失敗ではない。既定の判定（rc と
        エラー語）はそのまま、必要なプロバイダだけが上書きする。"""
        text = (err or out or "").strip()
        ok = (rc == 0) and not self.cli_output_is_error(text)
        if ok:
            return True, self.summarize_output(out)
        return False, text or "（出力なし / rc=%s）" % rc

    def summarize_output(self, out: str) -> str:
        lines = [l for l in (out or "").splitlines() if l.strip()]
        if not lines:
            return "完了"
        return lines[0] if len(lines) == 1 else "%s（他 %d 行）" % (lines[0], len(lines) - 1)

    # --- 検出 ----------------------------------------------------------
    def detect(self):
        """インストール検出。available と info を設定する。例外は握りつぶす。"""
        raise NotImplementedError

    # --- ワークスペース判定 -----------------------------------------------
    def find_root(self, directory: str):
        """directory（実体パス）が属するワークスペースのルート。無ければ None。"""
        raise NotImplementedError

    def root_for(self, directory: str):
        key = norm(directory)
        with self._lock:
            if key in self._root_cache:
                r = self._root_cache[key]
                return r or None
        try:
            r = self.find_root(directory)
        except Exception:
            r = None
        with self._lock:
            self._root_cache[key] = r or ""
        return r

    def invalidate_roots(self):
        with self._lock:
            self._root_cache.clear()

    # --- 状態 -------------------------------------------------------------
    def fetch_status(self, root: str, directory: str):
        """directory 配下（直下）の状態を {norm(path): state} で返す。"""
        raise NotImplementedError

    # --- 操作 -------------------------------------------------------------
    def actions(self, root: str, paths):
        """右クリック用: [(ラベル, callable), ...]。区切りは (None, None)。
        r58〜 第3要素に dict を付けられる:
          {"enabled": False, "tooltip": "理由"}  … 実行不可（灰色表示）
          {"confirm": "確認文"}                  … 実行前に Yes/No 確認
        UI（browser_panel._popup_context_menu）が解釈する。"""
        return []

    # --- 失敗管理 ----------------------------------------------------------
    #
    # r125: «使えない» は 2 種類ある。混ぜると戻ってこられない。
    #   1) そもそも入っていない（p4.exe が無い）… _installed=False。恒久的。
    #   2) 今は応答しない（瞬断・サーバー混雑）… _disabled_until。時限。
    # available は両方を見た «今この瞬間に使えるか» を返す。
    @property
    def available(self) -> bool:
        if not self._installed:
            return False
        until = self._disabled_until
        if until:
            if _time.monotonic() < until:
                return False
            # 休み明け: 何事も無かったことにして、もう一度試させる
            self._disabled_until = 0.0
            self._failures = 0
            self.info.pop("disabled_reason", None)
        return True

    @available.setter
    def available(self, value):
        self._installed = bool(value)
        if value:
            self._disabled_until = 0.0
            self._failures = 0
            self.info.pop("disabled_reason", None)

    def note_failure(self, why=""):
        self._failures += 1
        if self._failures >= self.MAX_FAILURES:
            self._disabled_until = _time.monotonic() + self.FAILURE_COOLDOWN_SEC
            self.info["disabled_reason"] = why or "連続失敗"
            # 黙って消えるのが一番たちが悪い（バッジが出なくなった理由が
            # 分からない）。必ずログに残す。
            try:
                self.log("%s: %d 回続けて失敗したので %.0f 秒休みます（%s）"
                         % (self.label, self._failures,
                            self.FAILURE_COOLDOWN_SEC,
                            (why or "").strip().splitlines()[0][:120]
                            if why else "理由不明"))
            except Exception as _e:
                _swallow(_e, "core/integrations/base.py note_failure(log)")

    def clear_cooldown(self):
        """手動更新で «今すぐもう一度試す»（r125）。"""
        self._failures = 0
        self._disabled_until = 0.0
        self.info.pop("disabled_reason", None)

    def note_success(self):
        self._failures = 0
        self._disabled_until = 0.0
        self.info.pop("disabled_reason", None)
