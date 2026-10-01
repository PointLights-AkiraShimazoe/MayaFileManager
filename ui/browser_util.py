# -*- coding: utf-8 -*-
"""ブラウザ共通のログ・パス解決・アイコン提供（r110 で browser_panel から分離）。

ここは «他のどのブラウザ系モジュールからも import される» 最下層。
逆向きの import（browser_panel 等を読む）は循環するので禁止。"""

import os
import struct
import threading
from pathlib import Path
from typing import List, Optional, Callable

from core.compat import (
    Qt, Signal, QObject,
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QLineEdit, QToolButton,
    QSplitter, QColumnView, QListView,
    QSizePolicy, QFrame, QAbstractItemView, QSlider,
    QFileSystemModel, QSortFilterProxyModel,
    QMenu, QAction, QMessageBox, QFileDialog, QInputDialog, QDialog,
    QStyledItemDelegate, QStyle, QModelIndex, QSize, QRect, QPixmap, QPainter, QColor, QDir, QFileInfo, QUrl, QMimeData, QPoint,
    QFontMetrics, QTimer, QKeySequence, QDrag, QCursor,
)
from core.compat import QtCore as _QtCore
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
from core.file_operations import (
    open_with_default_app, reveal_in_explorer,
    copy_items, move_items, get_file_type_category, format_size,
    resolve_windows_shortcut,
    MAYA_EXTENSIONS
)
from core.thumbnail_generator import ThumbnailManager




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
# 起動タイムライン（初回読み込みの所要時間調査用・常時有効）
# 出力先: ツールフォルダ直下の mfm_startup.log（起動ごとに上書き）
# ---------------------------------------------------------------------------
import time as _time_mod
_MFM_T0 = _time_mod.monotonic()
_MFM_STARTUP_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mfm_startup.log")
try:
    with open(_MFM_STARTUP_LOG, "w", encoding="utf-8") as _f:
        _f.write("=== MayaFileManager 起動タイムライン ===\n")
except OSError:
    pass


_MFM_FREEZE_LOG = os.path.join(os.path.dirname(_MFM_STARTUP_LOG),
                               "mfm_freeze.log")


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
        except Exception:
            pass
        self._cache = {}
        self._lock = threading.Lock()
        self._folder = None
        self._file = None

    def _generic(self, kind):
        try:
            return super().icon(kind)
        except Exception:
            return QIcon()

    def icon(self, arg):
        try:
            if isinstance(arg, QFileInfo):
                if arg.isDir():
                    with self._lock:
                        if self._folder is None:
                            self._folder = self._generic(QFileIconProvider.Folder)
                        return self._folder
                ext = arg.suffix().lower()
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


_MFM_UI_LOG = os.path.join(os.path.dirname(_MFM_STARTUP_LOG), "mfm_ui.log")
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
    except Exception:
        pass


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
    except Exception:
        pass


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
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 展開中フォルダの目印（r81）
# ---------------------------------------------------------------------------

