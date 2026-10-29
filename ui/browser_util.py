# -*- coding: utf-8 -*-
"""ブラウザ共通のログ・パス解決・アイコン提供（r110 で browser_panel から分離）。

ここは «他のどのブラウザ系モジュールからも import される» 最下層。
逆向きの import（browser_panel 等を読む）は循環するので禁止。"""
from core.diag import swallow as _swallow  # r112

import os
import threading

from core.compat import (
    Qt, QObject,
    QApplication, QMenu, QInputDialog, QRect, QPixmap, QFileInfo, QPoint,
    QDrag, )
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
from core.file_operations import (
    open_with_default_app
)




def _cursor_over_maya_window() -> bool:
    """後方互換: カーソル直下が Maya か。"""
    return _cursor_over_dcc_window() == "maya"


def _cursor_over_dcc_window():
    """マウスカーソル直下のウィンドウのプロセスが DCC なら "maya" / "blender"、
    それ以外は None（Windows専用）。D&D終了時に «どこへドロップされたか» を
    判定するために使う（r65 で Blender 対応）。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        pt = wt.POINT()
        if not u32.GetCursorPos(ctypes.byref(pt)):
            return None
        hwnd = u32.WindowFromPoint(pt)
        if not hwnd:
            return None
        pid = wt.DWORD(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k32.OpenProcess(0x1000, False, pid.value)
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if not k32.QueryFullProcessImageNameW(h, 0, buf,
                                                  ctypes.byref(size)):
                return None
            exe = buf.value.replace("\\", "/").rsplit("/", 1)[-1].lower()
            if exe == "maya.exe":
                return "maya"
            if exe == "blender.exe":
                return "blender"
            return None
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 起動タイムライン・フリーズ記録・UI操作ログ（常時有効のもの）
#
# 【重要】出力先は **ユーザープロファイル直下**（~/.maya_file_manager/logs）。
# r117: 以前はツールフォルダ直下（= ツールを置いたドライブ）に書いていたが、
# **そのドライブが応答しなくなると、記録しようとした書き込み自体がブロック
# される**。2026-10-01 に D: が無応答になった際、UI は固まっているのに
# mfm_freeze.log へ 1 行も残らず、原因を特定できなかった。
# 監視ログは «監視対象と別のディスク» に置く。
# ---------------------------------------------------------------------------
import time as _time_mod
_MFM_T0 = _time_mod.monotonic()


def _log_dir() -> str:
    """ログの置き場所。ホーム直下に作れなければツールフォルダへ退避する。"""
    try:
        d = os.path.join(os.path.expanduser("~"), ".maya_file_manager", "logs")
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_MFM_LOG_DIR = _log_dir()
_MFM_STARTUP_LOG = os.path.join(_MFM_LOG_DIR, "mfm_startup.log")
try:
    with open(_MFM_STARTUP_LOG, "w", encoding="utf-8") as _f:
        _f.write("=== MayaFileManager 起動タイムライン ===\n")
except OSError:
    pass


_MFM_FREEZE_LOG = os.path.join(_MFM_LOG_DIR, "mfm_freeze.log")


import re as _re_mod
# 「ローカル ディスク (C:)」「Local Disk (C:)」「OS (C:)」等からドライブレターを抽出
_DRIVE_IN_LABEL_RE = _re_mod.compile(r"\(([A-Za-z]):\)\s*$")


def _safe_file_path(model, index) -> str:
    """QFileSystemModel.filePath() の «I/Oなし» 代替。

    Qtの filePath() は内部で `node->isSymLink() && resolveSymlinks() && ...`
    の順に評価するため、setResolveSymlinks(False) でも isSymLink()
    （リパースポイント判定＝ファイルシステムアクセス）が先に走る。
    OneDrive/到達不能なネットワーク先ではこれが約21秒ブロックし、
    フィルタ再評価のたびにUIが固まった（faulthandler で391行目を確定。2026-09）。
    fileName() は resolveSymlinks() を先に判定するためI/Oが無い。
    本関数は fileName() を親方向へ連結してパスを組み立てる。
    UIスレッドでは filePath() を直接呼ばず必ずこちらを使うこと。"""
    try:
        if index is None or not index.isValid():
            return ""
        names = []
        idx = index
        while idx.isValid():
            names.append(model.fileName(idx))
            idx = idx.parent()
        names.reverse()
        if not names:
            return ""
        # 【注意】ドライブ階層のノードは、環境（MayaのQt）によって fileName() が
        # 「C:」ではなく表示名「ローカル ディスク (C:)」を返す。これを
        # そのまま連結すると壊れたパスになり、スピナーが消えない／Mayaへ
        # 不正なパスを送る、を引き起こした（2026-09 実機）。表示名から
        # ドライブレターを取り出して正規化する。
        top = names[0]
        m = _DRIVE_IN_LABEL_RE.search(top)
        if m:
            names[0] = m.group(1).upper() + ":"
        elif len(top) == 2 and top[1] == ":":
            names[0] = top[0].upper() + ":"
        if names[0] == "/":                     # POSIX ルート
            return "/" + "/".join(names[1:])
        p = "/".join(names)
        if len(p) == 2 and p.endswith(":"):     # Windows ドライブ直下
            p += "/"
        return p
    except Exception:
        return ""


class _SafeIconProvider(QFileIconProvider):
    """シェル拡張を走らせないアイコンプロバイダ（r77）。

    - フォルダ: 標準フォルダアイコン（desktop.ini 照会なし）
    - ファイル: 拡張子ごとに 1 回だけ、存在しないダミー名で問い合わせる
      → Windows は SHGFI_USEFILEATTRIBUTES 相当でレジストリの既定アイコンを
      返し、そのファイル固有のアイコンハンドラ（P4EXP 等）は呼ばれない
    - exe / lnk / url / ico: 個別抽出・リンク解決を伴うため汎用ファイルアイコン
    QFileSystemModel は収集スレッドから icon() を呼ぶので、キャッシュはロックで守る。"""

    _GENERIC_EXT = {"exe", "lnk", "url", "ico", "scr", "dll", "cpl", "msi"}

    def __init__(self):
        super().__init__()
        try:
            self.setOptions(QFileIconProvider.DontUseCustomDirectoryIcons)
        except Exception as _e:
            _swallow(_e, "ui/browser_util.py:180 __init__")
        self._cache = {}
        self._lock = threading.Lock()
        self._folder = None
        self._file = None
        # «フォルダかどうか» の答えを覚える／裏で調べる（r146）
        import queue as _queue
        self._isdir = {}
        self._asking = set()
        self._ask = _queue.Queue()
        threading.Thread(target=self._isdir_worker, daemon=True,
                         name="mfm-icon-isdir").start()

    def _generic(self, kind):
        try:
            return super().icon(kind)
        except Exception:
            return QIcon()

    # ── «フォルダかどうか» を UI スレッドで待たない（r146、実機 2026-10-15）
    #
    # mfm_freeze.log に 21.1 秒のフリーズが記録され、止まっていたのは
    # ここの ``arg.isDir()`` だった。QFileInfo.isDir() はディスクに
    # 問い合わせる。OneDrive の «実体の無いファイル» や、消した直後のパス、
    # 応答の悪い共有では、1 回で数十秒返らないことがある。アイコンは
    # **見た目だけ** のものなので、ここで待つ価値は無い。
    #
    # 代わりに
    #   * 答えを覚えているならそれを使う
    #   * 知らなければ «拡張子が無ければフォルダ» と仮に決めて即返し、
    #     本当の答えは裏のスレッドで調べて覚える（次の描画で正しくなる）
    # とする。待たないので UI は止まらない。
    def _isdir_nowait(self, path: str, has_ext: bool) -> bool:
        from core.diag import diag_off as _d
        if _d("old_icon_isdir"):
            import os
            try:
                return os.path.isdir(path)
            except OSError:
                return False
        if not path:
            return not has_ext
        with self._lock:
            known = self._isdir.get(path)
            if known is None and path not in self._asking:
                self._asking.add(path)
                self._ask.put(path)
        if known is not None:
            return known
        return not has_ext              # 仮の答え（拡張子が無ければフォルダ）

    def _isdir_worker(self):
        import os
        while True:
            path = self._ask.get()
            try:
                v = os.path.isdir(path)
            except OSError:
                v = False
            with self._lock:
                self._isdir[path] = v
                self._asking.discard(path)
                if len(self._isdir) > 20000:
                    self._isdir.clear()   # 覚えすぎない

    def type(self, info):
        """«種類» の文字列。**I/O もシェル呼び出しもしない**（r146）。

        QFileSystemModel は、フォルダの変化が届くたびにメインスレッドで
        getInfo() → iconProvider.type() を 1 ファイルずつ呼ぶ。既定の
        QFileIconProvider.type は Windows のシェル（SHGetFileInfo）に
        種類名を聞きに行き、外付け／ネットワークのボリュームでは 1 件ごとに
        待たされる。フォルダ移動のあと数十秒固まった（実機 2026-10-16
        16:00、Python 側のスタックは app.exec() のみ＝C++ の中）要因の
        一つとして切る。種類は拡張子から決めるだけで十分。

        **答えは拡張子だけで決め、二度聞かれても同じ答えを返す**（r146、
        実機計測 2026-10-16 22:30）。ここで _isdir_nowait（仮の答え →
        後で本当の答え）を使っていたところ、QFileSystemModel は再読のたびに
        «種類（displayType）» を前回と比べ、違えば dataChanged を出す。
        仮の答えが外れた項目（拡張子の無いファイル、名前に . を含む
        フォルダ）が 7,500 件中 1,700 件あった Temp では、フォルダが
        少し変わるたびに dataChanged が 1,700 回出て、その都度カラムの
        組み直し（7,500 件 × 8 役割の data()）が走り、1 回の移動で
        2 分固まった（Windows 回帰スイートの BrowserPanel 系が軒並み
        100 秒超）。種類の文字列は見た目だけのものなので、正確さより
        «揺れない» ことを取る。"""
        try:
            if isinstance(info, QFileInfo):
                ext = info.suffix()
                return (ext.upper() + " File") if ext else "File Folder"
        except Exception as _e:
            _swallow(_e, "ui/browser_util.py _SafeIconProvider.type")
        try:
            return super().type(info)
        except Exception:
            return ""

    def icon(self, arg):
        try:
            if isinstance(arg, QFileInfo):
                ext0 = arg.suffix().lower()
                if self._isdir_nowait(arg.filePath(), bool(ext0)):
                    with self._lock:
                        if self._folder is None:
                            self._folder = self._generic(QFileIconProvider.Folder)
                        return self._folder
                ext = ext0
                if not ext or ext in self._GENERIC_EXT:
                    with self._lock:
                        if self._file is None:
                            self._file = self._generic(QFileIconProvider.File)
                        return self._file
                with self._lock:
                    ic = self._cache.get(ext)
                if ic is not None:
                    return ic
                # 実在しないダミー名 → シェルは拡張子の既定アイコンだけを返す
                ic = super().icon(QFileInfo("mfm_icon_probe_no_such_file." + ext))
                if ic is None or ic.isNull():
                    ic = self._generic(QFileIconProvider.File)
                with self._lock:
                    self._cache[ext] = ic
                return ic
            return super().icon(arg)
        except Exception:
            return QIcon()


def _mfm_slow_note(msg: str):
    """UIスレッドで0.5秒超かかった呼び出しを mfm_freeze.log に記録する
    （フリーズ原因の«呼び出し単位»の特定用）。"""
    try:
        import datetime
        with open(_MFM_FREEZE_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] SLOW: %s\n"
                    % (datetime.datetime.now().strftime("%H:%M:%S"), msg))
    except OSError:
        pass


_MFM_UI_LOG = os.path.join(_MFM_LOG_DIR, "mfm_ui.log")
# r109: 既定オフ。クリック・ドラッグのたびにファイルへ書いていたため、
# 操作のたびに同期 I/O が走っていた（調査用の常時ログは平時は不要）。
# 切り分けが要る時だけ MFM_UILOG=1 で有効にする。
_MFM_UILOG_ON = bool(os.environ.get("MFM_UILOG"))
if _MFM_UILOG_ON:
    try:
        with open(_MFM_UI_LOG, "w", encoding="utf-8") as _f:
            _f.write("=== UI操作ログ（MFM_UILOG=1 のときだけ記録） ===\n")
    except OSError:
        pass


def _mfm_warn(msg: str):
    """**常時有効**の警告ログ（~/mfm_debug.log）。失敗した時だけ書く。

    r109: 調査ログを既定オフにしたが、«失敗が無言で消える» のは r96 で
    痛い目を見た。頻度の低い失敗だけはここで必ず残す。"""
    try:
        import datetime
        with open(_MFM_LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] WARN: %s\n"
                    % (datetime.datetime.now().strftime("%H:%M:%S"), msg))
    except Exception as _e:
        _swallow(_e, "ui/browser_util.py:258 _mfm_warn")


def _mfm_uilog(msg: str, with_stack: bool = False):
    """UI 操作の調査ログ（MFM_UILOG=1 のときだけ ツール直下 mfm_ui.log）。"""
    if not _MFM_UILOG_ON:
        return
    try:
        import datetime
        line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], msg)
        if with_stack:
            import traceback
            frames = traceback.format_stack(limit=6)[:-1]
            line += "\n" + "".join("    " + l for l in frames)
        with open(_MFM_UI_LOG, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")
    except Exception as _e:
        _swallow(_e, "ui/browser_util.py:275 _mfm_uilog")


# r108: «意図的に UI スレッドを占有している区間» の宣言。
# Windows の D&D（OLE DoDragDrop）は、ドラッグ中ずっとこちらのスレッドを
# 握ったまま戻らない。フリーズ監視はこれを «停止» として記録してしまい、
# mfm_freeze.log が本物のフリーズで埋もれる（現行ビルドの記録の約半分が
# ドラッグの誤検出だった）。区間中は監視側で記録を抑止する。
_MFM_BLOCKING = {"n": 0, "why": ""}


def mfm_blocking_begin(why: str = ""):
    _MFM_BLOCKING["n"] += 1
    _MFM_BLOCKING["why"] = why or _MFM_BLOCKING["why"]


def mfm_blocking_end():
    _MFM_BLOCKING["n"] = max(0, _MFM_BLOCKING["n"] - 1)
    if _MFM_BLOCKING["n"] == 0:
        _MFM_BLOCKING["why"] = ""


def mfm_blocking_reason() -> str:
    """意図的な占有中ならその理由、そうでなければ空文字。"""
    return _MFM_BLOCKING["why"] if _MFM_BLOCKING["n"] > 0 else ""


# r146（ユーザー報告 2026-10-16「コンパクトモードのとき、manager 外への
# D&D が死んでいます」）: **自分が始めたドラッグ** を数える。
# QDrag.exec（OLE DoDragDrop）の最中もタイマーは回るので、コンパクトの
# 見張り（ui/compact_mode.py _tick）はこれを見て «畳んでもドラッグを
# 殺さない» 畳み方に切り替える。mouseButtons() は外部ドラッグで信用
# できない（_busy の注記）が、こちらは自分で立てる旗なので確か。
_MFM_DRAG = {"n": 0}


def mfm_drag_begin():
    _MFM_DRAG["n"] += 1


def mfm_drag_end():
    _MFM_DRAG["n"] = max(0, _MFM_DRAG["n"] - 1)


def mfm_drag_active() -> bool:
    """このアプリが始めたドラッグ（QDrag.exec）が進行中か。"""
    return _MFM_DRAG["n"] > 0


def _mfm_timeline(msg: str):
    """起動から120秒間だけ、経過秒つきでイベントを記録する。"""
    try:
        t = _time_mod.monotonic() - _MFM_T0
        if t > 120.0:
            return
        with open(_MFM_STARTUP_LOG, "a", encoding="utf-8") as f:
            f.write("[%7.2fs] %s\n" % (t, msg))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 外から落とされたファイルの受け方（r146・データ消失の根治）
# ---------------------------------------------------------------------------
# 実機事故 2026-10-24: Explorer からドロップしたファイルが «元の場所にも
# 落とした先にも無い» 状態で消えた。
#
# Windows の D&D では、受け手（ここ）が Drop に «移動した（DROPEFFECT_MOVE）»
# と返すと、送り手（Explorer）は «非最適化移動» として **元ファイルを自分で
# 消す**（Qt は MoveAction を受理すると CFSTR_PERFORMEDDROPEFFECT=MOVE を
# 立てる）。こちらは «コピー／移動» をメニューで選ばせてから処理するので、
# 選んだ時には元がもう無い → 何も起きずに消える。
#
# 規則: **外部ファイルのドロップに OS へ返す答えは常に «コピー»**。
# 移動したい時は、こちらが自分で（コピー確認の後に）元を消す。こうすれば
# 送り手が元を消すことは絶対に無い。自分発のドラッグは戻り値を見て
# いないので、ここで «コピー» と答えても内部の動作は変わらない。
# 全ての dropEvent / Drop 分岐はこの関数で受けること
# （test_drop_never_hands_over_move が見張る）。
def classify_url(text: str):
    """アドレス欄・Preset に入れられた文字列が URL かを判定する（r146）。

    戻り値 (kind, value):
      ("web", url)   … ブラウザで開くもの（http/https/ftp/mailto、www. 始まり）
      ("file", path) … file:// の URL（ローカルパスにして返す）
      (None, text)   … URL ではない（普通のパス）
    ドライブ文字（C:/…）を URL と取り違えないよう、スキームは 2 文字以上。"""
    import re as _re
    t = (text or "").strip()
    if not t:
        return None, t
    low = t.lower()
    if low.startswith("www."):
        return "web", "https://" + t
    if low.startswith("mailto:"):
        return "web", t
    m = _re.match(r"^([a-z][a-z0-9+.\-]+)://", low)
    if not m:
        return None, t
    scheme = m.group(1)
    if scheme == "file":
        try:
            from core.compat import QUrl
            local = QUrl(t).toLocalFile()
        except Exception:
            local = ""
        if not local:
            from urllib.parse import urlparse, unquote
            local = unquote(urlparse(t).path)
        if _re.match(r"^/[A-Za-z]:", local):
            local = local[1:]              # /C:/… → C:/…
        return "file", local
    return "web", t


def accept_drop_as_copy(event):
    """ドロップを受理し、OS（送り手）には «コピーした» と答える。"""
    try:
        event.setDropAction(Qt.CopyAction)
    except Exception as _e:
        _swallow(_e, "ui/browser_util.py accept_drop_as_copy")
    event.accept()


def fileops_log(msg: str):
    """ファイル操作（ドロップ・移動・コピー）を常時記録する（r146）。

    データ消失の調査でログが 1 行も無かった反省。logs/mfm_fileops.log、
    2MB を超えたら .1 へ回す。失敗しても何もしない。"""
    try:
        import datetime
        p = os.path.join(_MFM_LOG_DIR, "mfm_fileops.log")
        try:
            if os.path.getsize(p) > 2 * 1024 * 1024:
                os.replace(p, p + ".1")
        except OSError:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write("[%s pid=%d] %s\n" % (
                datetime.datetime.now().strftime("%m-%d %H:%M:%S"),
                os.getpid(), msg))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 診断ログ（ショートカット/カラム構築の不具合切り分け用）
# 出力先: ユーザーホーム直下の mfm_debug.log
# ---------------------------------------------------------------------------
_MFM_LOG_PATH = os.path.join(os.path.expanduser("~"), "mfm_debug.log")
# 既定はオフ。環境変数 MFM_DEBUG=1 を設定した時だけ ~/mfm_debug.log に診断を書き出す。
_MFM_DEBUG = bool(os.environ.get("MFM_DEBUG"))


def _mfm_log(msg: str):
    if not _MFM_DEBUG:
        return
    try:
        import datetime
        with open(_MFM_LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], msg))
    except Exception as _e:
        _swallow(_e, "ui/browser_util.py:331 _mfm_log")


# ---------------------------------------------------------------------------
# 展開中フォルダの目印（r81）
# ---------------------------------------------------------------------------

