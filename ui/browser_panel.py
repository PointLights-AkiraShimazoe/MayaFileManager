"""
Browser Panel
=============
The main file browser widget.  Uses a column-based view (QColumnView) with a
custom proxy model for filtering / sorting and a thumbnail delegate.

Features implemented here
--------------------------
* Column view with configurable max depth
* Auto-width columns to longest visible item
* Sort by name / type / timestamp
* Text filter
* Single/double click action switching
* Drag-and-drop (source: file paths)
* Context menu with all file actions
* Thumbnail display via ThumbnailDelegate
* Drive selector (top-left combo)
"""

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

def _paint_expanded_mark(painter, option, index, is_expanded_fn):
    """右隣のカラムに中身を出しているフォルダ（＝展開中）を、選択とは別の
    控えめな色で示す。同じカラムでファイルを選ぶと選択ハイライトがファイルへ
    移り、右のカラムがどのフォルダ以下か分からなくなる（ユーザー指摘
    2026-09-17）ための目印。選択中はいつもの選択色に任せて描かない。
    色はテーマ依存にせずパレットの highlight を薄めて使う（淡い面＋左端の細い帯）。"""
    if is_expanded_fn is None or (option.state & QStyle.State_Selected):
        return
    try:
        if not is_expanded_fn(index):
            return
    except Exception:
        return
    r = option.rect
    hl = QColor(option.palette.highlight().color())
    painter.save()
    hl.setAlpha(46)
    painter.fillRect(r, hl)
    hl.setAlpha(150)
    painter.fillRect(r.left(), r.top(), 3, r.height(), hl)
    painter.restore()


# ---------------------------------------------------------------------------
# Thumbnail Delegate
# ---------------------------------------------------------------------------

class ThumbnailDelegate(QStyledItemDelegate):
    """
    Paints a thumbnail to the left of the file name in list/icon views.
    Falls back to system icon when thumbnail is not yet ready.
    """

    def __init__(self, thumb_mgr: ThumbnailManager, thumb_size: int = 64, parent=None,
                 is_expanded_index=None):
        super().__init__(parent)
        self._mgr = thumb_mgr
        self._thumb_size = thumb_size
        self._is_expanded_index = is_expanded_index   # r81: 展開中フォルダの目印

    _PAD = 4
    _TEXT_H = 26        # アイコン表示時に名前へ割く高さ（2行分）

    def _icon_mode(self, option) -> bool:
        """アイコン（グリッド）表示か。QListView.IconMode のときは
        «画像を上・名前を下» に置く。リスト表示は «画像を左・名前を右»。"""
        w = getattr(option, "widget", None)
        try:
            if w is not None and hasattr(w, "viewMode"):
                return w.viewMode() == QListView.IconMode
        except Exception:
            pass
        try:
            # QStyleOptionViewItem.Top（enum は option のクラス側から引く。
            # PySide2/6 で import 位置が違うため直接 import しない）
            return option.decorationPosition == type(option).Top
        except Exception:
            return False

    def sizeHint(self, option, index) -> QSize:
        if self._icon_mode(option):
            return QSize(self._thumb_size + self._PAD * 2 + 10,
                         self._thumb_size + self._PAD * 2 + self._TEXT_H)
        return QSize(self._thumb_size + 8, self._thumb_size + 8)

    def _fallback_pixmap(self, index, side: int):
        """サムネイルが «まだ無い/作れない» ときに必ず出す代替の絵。
        モデルの装飾アイコン（_SafeIconProvider のファイル種別アイコン）を使う。
        r84: これが無いと «グリッドに何も出ない» 状態になっていた。"""
        try:
            deco = index.data(Qt.DecorationRole)
        except Exception:
            deco = None
        if deco is None:
            return None
        try:
            if isinstance(deco, QPixmap):
                return self._fit(deco, side)
            if QIcon is not None and isinstance(deco, QIcon):
                # r102: QIcon.pixmap() は «要求サイズ以下の最大» しか返さず、
                # 足りなくても拡大しない。シェルのアイコンは 16/32/48 しか
                # 持たないことが多く、そのまま描くとグリッドの中で
                # 小さいまま（フォルダや README が豆粒。ユーザー報告 2026-09-25）。
                # 使える中で一番大きいものを取り出してからセルに合わせる。
                want = side
                try:
                    sizes = deco.availableSizes() or []
                    if sizes:
                        want = max(want, max(sz.width() for sz in sizes))
                except Exception:
                    pass
                pm = deco.pixmap(QSize(want, want))
                if pm is None or pm.isNull():
                    pm = deco.pixmap(side, side)
                if pm is None or pm.isNull():
                    return None
                return self._fit(pm, side)
        except Exception:
            pass
        return None

    @staticmethod
    def _fit(pm, side: int):
        """アイコンをセルの一辺に合わせる（拡大もする）。"""
        if pm is None or pm.isNull():
            return None
        if pm.width() == side or pm.height() == side:
            return pm
        return pm.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def paint(self, painter: QPainter, option, index: QModelIndex):
        self.initStyleOption(option, index)
        from core.theme_engine import qss_vars
        tv = qss_vars()
        icon_mode = self._icon_mode(option)
        pad = self._PAD

        # Draw selection background
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(tv["selection"]))
        _paint_expanded_mark(painter, option, index, self._is_expanded_index)

        r = option.rect
        if icon_mode:
            side = max(16, min(self._thumb_size,
                               r.width() - pad * 2, r.height() - pad * 2 - self._TEXT_H))
            img_rect = QRect(r.x() + (r.width() - side) // 2, r.y() + pad, side, side)
            text_rect = QRect(r.x() + 2, img_rect.bottom() + 2,
                              r.width() - 4, max(12, r.bottom() - img_rect.bottom() - 4))
            text_flags = Qt.AlignHCenter | Qt.AlignTop
            elide = Qt.ElideMiddle
        else:
            side = max(16, min(self._thumb_size, r.height() - pad * 2))
            img_rect = QRect(r.x() + pad, r.y() + (r.height() - side) // 2, side, side)
            text_rect = QRect(img_rect.right() + 8, r.y() + pad,
                              max(10, r.right() - img_rect.right() - 12),
                              r.height() - pad * 2)
            text_flags = Qt.AlignVCenter | Qt.AlignLeft
            elide = Qt.ElideMiddle

        model = index.model()
        source_model = model
        source_index = index
        # Unwrap proxy
        while hasattr(source_model, "sourceModel"):
            source_index = source_model.mapToSource(source_index)
            source_model = source_model.sourceModel()

        file_path = _safe_file_path(source_model, source_index) if hasattr(source_model, "filePath") else ""

        # フォルダはサムネイル生成の対象外（"?" の汎用チップになってしまう）。
        # モデルのフォルダアイコンをそのまま使う（r84）。
        is_dir = False
        try:
            if hasattr(source_model, "isDir"):
                is_dir = bool(source_model.isDir(source_index))
        except Exception:
            is_dir = False

        # 1) 生成済みサムネイル → 2) 種別アイコン（必ずどちらかは出す）
        pm = self._mgr.get(file_path) if (file_path and not is_dir) else None
        if pm is None or pm.isNull():
            pm = self._fallback_pixmap(index, side)
        if pm is not None and not pm.isNull():
            # r103: «ピクセル数» ではなく «描画先の矩形» で指定する。
            # 高 DPI ではシェルアイコンの devicePixelRatio が 1 でないことがあり、
            # pm.width() は «デバイスピクセル» を返す。その数値をそのまま座標に
            # 使うと、実際には 1/DPR の大きさで描かれ «アイコンだけ小さい» に
            # なる（README.md が豆粒のまま残っていた原因。2026-09-25）。
            # 論理サイズでアスペクト比を保ったまま side に合わせる。
            try:
                dpr = float(pm.devicePixelRatio()) or 1.0
            except Exception:
                dpr = 1.0
            lw = max(1.0, pm.width() / dpr)
            lh = max(1.0, pm.height() / dpr)
            k = float(side) / max(lw, lh)
            tw, th = max(1, int(round(lw * k))), max(1, int(round(lh * k)))
            target = QRect(img_rect.x() + (side - tw) // 2,
                           img_rect.y() + (side - th) // 2, tw, th)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawPixmap(target, pm)

        # File name（アイコンが取れなくても «名前» は必ず描く）
        painter.setPen(QColor(tv["on_selection"] if option.state & QStyle.State_Selected
                              else tv["on_surface"]))
        fm = QFontMetrics(option.font)
        display = index.data(Qt.DisplayRole) or ""
        if icon_mode:
            # 2行まで折り返して、入り切らない分だけ省略する
            lines = []
            rest = str(display)
            max_lines = max(1, text_rect.height() // max(1, fm.height()))
            while rest and len(lines) < max_lines:
                if fm.horizontalAdvance(rest) <= text_rect.width():
                    lines.append(rest)
                    break
                cut = len(rest)
                while cut > 1 and fm.horizontalAdvance(rest[:cut]) > text_rect.width():
                    cut -= 1
                if len(lines) == max_lines - 1:
                    lines.append(fm.elidedText(rest, elide, text_rect.width()))
                    break
                lines.append(rest[:cut])
                rest = rest[cut:]
            y = text_rect.y()
            for ln in lines:
                painter.drawText(QRect(text_rect.x(), y, text_rect.width(), fm.height()),
                                 text_flags, ln)
                y += fm.height()
        else:
            painter.drawText(text_rect, text_flags,
                             fm.elidedText(str(display), elide, text_rect.width()))


# ---------------------------------------------------------------------------
# Filter Proxy Model
# ---------------------------------------------------------------------------

class FileFilterProxyModel(QSortFilterProxyModel):
    """Filters by filename substring and controls sort column."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter = ""
        self._show_hidden = False
        # 隠し属性でも常に表示するパス集合（normcase済み）。
        # ショートカット先が AppData 等の隠しフォルダを経由する場合に、
        # その経路の祖先だけを表示してカラムチェーンを構築可能にする。
        self._force_visible = set()
        # カラム別(親パス別)のフィルタ/ソート状態。
        #   _col_filters: normcase(親パス) -> フィルタ文字列(小文字)
        #   _col_sorts:   normcase(親パス) -> (key, ascending)  key in name/type/date/size
        self._col_filters = {}
        self._col_excludes = {}   # 親パス別「排他フィルタ」（一致を除外）
        self._col_sorts = {}
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(True)
        # isHidden(=stat) の結果キャッシュ。filterAcceptsRow はモデル内の
        # «全読み込み済み行» に対して invalidateFilter の度に呼ばれるため、
        # 毎回statするとネットワーク先で1回のナビが数十秒級になる（実測21秒、
        # mfm_freeze.log で捕捉）。隠し属性は滅多に変わらないので永続キャッシュ。
        # さらに «初回のstatもUIスレッドでは行わない»（ジャンクション先が
        # ネットワークだと1件statに秒単位かかり、キャッシュ空の初回パスで
        # 10〜20秒フリーズした実測あり）。未判定の行はいったん表示し、
        # ワーカースレッドで判定 → 隠しと判明した時だけ一括で再評価する。
        self._hidden_cache = {}
        self._hidden_pending = set()        # 判定待ちの normcase パス
        self._hidden_raw = {}               # normcase パス → 実パス（stat用）
        self._hidden_lock = threading.Lock()
        self._hidden_worker_running = False
        self._hidden_found = False          # 隠しが新たに見つかったら再評価
        self._hidden_timer = QTimer(self)
        self._hidden_timer.setSingleShot(True)
        self._hidden_timer.setInterval(250)
        self._hidden_timer.timeout.connect(self._flush_hidden_results)

    @staticmethod
    def _norm(p: str) -> str:
        try:
            return os.path.normcase(os.path.normpath(p)) if p else ""
        except Exception:
            return ""

    def set_filter_string(self, text: str):
        self._filter = text.lower()
        self.invalidateFilter()

    def set_show_hidden(self, show: bool):
        self._show_hidden = show
        with self._hidden_lock:      # 表示切替時は属性を取り直す
            self._hidden_cache.clear()
            self._hidden_pending.clear()
            self._hidden_raw.clear()
        self.invalidateFilter()

    # --- 隠し属性の非同期解決（UIスレッドでのstat禁止） -------------------
    def _queue_hidden_probe(self, norm_path: str, raw_path: str):
        """UIスレッドから呼ぶ。判定待ちに積み、ワーカーとフラッシュを起動。"""
        need_worker = False
        with self._hidden_lock:
            if norm_path not in self._hidden_pending:
                self._hidden_pending.add(norm_path)
                self._hidden_raw[norm_path] = raw_path
            if not self._hidden_worker_running:
                self._hidden_worker_running = True
                need_worker = True
        if need_worker:
            threading.Thread(target=self._hidden_worker, daemon=True,
                             name="mfm-hidden-probe").start()
        if not self._hidden_timer.isActive():
            self._hidden_timer.start()

    def _hidden_worker(self):
        """ワーカースレッド: 判定待ちパスの隠し属性を lstat で解決する。"""
        import stat as _stat
        while True:
            with self._hidden_lock:
                if not self._hidden_pending:
                    self._hidden_worker_running = False
                    return
                norm = self._hidden_pending.pop()
                raw = self._hidden_raw.pop(norm, norm)
            hidden = False
            try:
                st = os.lstat(raw)
                attrs = getattr(st, "st_file_attributes", 0)
                hidden = bool(attrs & 2)   # FILE_ATTRIBUTE_HIDDEN
            except OSError:
                hidden = False
            with self._hidden_lock:
                if len(self._hidden_cache) > 200000:
                    self._hidden_cache.clear()
                self._hidden_cache[norm] = hidden
                if hidden:
                    self._hidden_found = True

    def _flush_hidden_results(self):
        """UIスレッド: 判定結果を反映。隠しが見つかった時だけ再フィルタ。"""
        with self._hidden_lock:
            busy = self._hidden_worker_running or bool(self._hidden_pending)
            found = self._hidden_found
            self._hidden_found = False
        if found:
            self.invalidateFilter()
        if busy:
            self._hidden_timer.start()

    def set_force_visible(self, paths):
        """隠し属性でも表示する祖先パス集合を設定する。
        内容が変わらない場合は invalidateFilter を «スキップ» する
        （invalidateFilter はモデル全行の再評価＝ナビ毎に呼ぶと重い）。"""
        new = set(self._norm(p) for p in paths)
        if new == self._force_visible:
            return
        self._force_visible = new
        self.invalidateFilter()

    # --- カラム別フィルタ/ソート ----------------------------------------
    def set_column_filter(self, parent_path: str, text: str):
        key = self._norm(parent_path)
        if text:
            self._col_filters[key] = text.lower()
        else:
            self._col_filters.pop(key, None)
        self.invalidateFilter()

    def get_column_filter(self, parent_path: str) -> str:
        return self._col_filters.get(self._norm(parent_path), "")

    def set_column_exclude(self, parent_path: str, text: str):
        key = self._norm(parent_path)
        if text:
            self._col_excludes[key] = text.lower()
        else:
            self._col_excludes.pop(key, None)
        self.invalidateFilter()

    def get_column_exclude(self, parent_path: str) -> str:
        return self._col_excludes.get(self._norm(parent_path), "")

    def set_column_sort(self, parent_path: str, key: str, ascending: bool = True):
        self._col_sorts[self._norm(parent_path)] = (key, ascending)
        # invalidate() は QFileSystemModel の非同期 populate と競合してクラッシュし得るため、
        # sort(-1)→sort(0) で安全に再ソートを強制する。
        self.sort(-1)
        self.sort(0, Qt.AscendingOrder)

    def get_column_sort(self, parent_path: str):
        return self._col_sorts.get(self._norm(parent_path), ("name", True))

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        # ホットパス注意: 本メソッドは invalidateFilter の度にモデル内の
        # «全読み込み済み行» へ呼ばれる。stat等のI/Oや不要なパス正規化を
        # 行うと1回のナビで数十秒フリーズする（2026-09 実測21秒）。
        # ドライブ階層（ルート直下の行 = C:/, W:/ …）は判定せず必ず通す。
        # 切断済みドライブマッピングの行に isDir()/filePath() 等で触れると
        # QFileInfo が stat を試みて約21秒ブロックする（SLOW記録 'W:/' 'X:/'
        # で確定。2026-09）。ドライブは隠し判定もフィルタも不要。
        if not source_parent.isValid():
            return True
        source_model = self.sourceModel()
        # 各Qt呼び出しの所要時間を計測し、0.5秒超なら記録する（PySideの
        # C++呼び出しはGILを離さないため、外部サンプラでは特定できない）
        _pc = _time_mod.perf_counter
        _t0 = _pc()
        index = source_model.index(source_row, 0, source_parent)
        _t1 = _pc()
        name = source_model.fileName(index)
        _t2 = _pc()
        if _t2 - _t0 > 0.5:
            _mfm_slow_note("filterAcceptsRow index()=%.2fs fileName()=%.2fs "
                           "parent=%r name=%r"
                           % (_t1 - _t0, _t2 - _t1,
                              _safe_file_path(source_model, source_parent), name))

        fp = None
        protected = False
        if self._force_visible:
            try:
                _t3 = _pc()
                raw_fp = _safe_file_path(source_model, index)
                _t4 = _pc()
                if _t4 - _t3 > 0.5:
                    _mfm_slow_note("filterAcceptsRow filePath()=%.2fs path=%r"
                                   % (_t4 - _t3, raw_fp))
                fp = self._norm(raw_fp)
            except Exception:
                fp = ""
            protected = fp in self._force_visible  # 現在ナビ中の経路は常に表示

        # Hidden files（隠し属性 or ドット名）。isHidden は stat を伴うため
        # キャッシュ必須（隠し属性は滅多に変わらない）。
        if not self._show_hidden and not protected:
            if name.startswith("."):
                return False
            if fp is None:
                try:
                    fp = self._norm(_safe_file_path(source_model, index))
                except Exception:
                    fp = ""
            is_hidden = self._hidden_cache.get(fp)
            if is_hidden is None:
                # 未判定はいったん表示し、statはワーカーで行う（UIスレッドで
                # statするとネットワーク先で初回パスが数十秒フリーズする）。
                # 隠しと判明した時だけ後から一括で再評価される。
                try:
                    raw = _safe_file_path(source_model, index)
                except Exception:
                    raw = ""
                if raw:
                    self._queue_hidden_probe(fp, raw)
                is_hidden = False
            if is_hidden:
                return False

        # フィルタ類が全て空なら以降の計算は不要（最頻ケースの早期リターン）
        if not (self._filter or self._col_filters or self._col_excludes):
            return True

        name_l = name.lower()

        # 全体フィルタ（ディレクトリはナビ維持のため常に通す）
        if self._filter and not source_model.isDir(index):
            if not self._fuzzy_match(self._filter, name_l):
                return False

        # カラム別フィルタ／排他フィルタ（その親=カラムにのみ適用。現在ナビ中の
        # 経路の祖先は保護してチェーンを壊さない）。
        # 一致は «部分一致(substring)»。例: "c00" は "c010" にはヒットしない。
        if (self._col_filters or self._col_excludes) \
                and source_parent.isValid() and not protected:
            pkey = self._norm(_safe_file_path(source_model, source_parent))
            cf = self._col_filters.get(pkey)
            if cf and cf not in name_l:
                return False
            ex = self._col_excludes.get(pkey)
            if ex and ex in name_l:
                return False   # 排他フィルタに一致 → 除外

        return True

    def hasChildren(self, parent=QModelIndex()):
        """空フォルダでも子カラムを出す（r62、ユーザー指示）。
        QSortFilterProxyModel の既定は「フェッチ済みで行が0（または全て
        フィルタ除外）」なら False を返し、QColumnView はその場合ヘッダの無い
        «プレビュー列»（空の QWidget）を出すため「子カラムが出ない」ように見えた。
        フォルダなら常に True を返して通常のカラム（ヘッダ付き・空）を作らせる。
        ドライブ階層（親が無効）の行には触れない（21秒ブロックの罠）。"""
        try:
            if parent.isValid():
                sp = self.mapToSource(parent)
                if sp.isValid() and sp.parent().isValid():
                    sm = self.sourceModel()
                    if sm is not None and sm.isDir(sp):
                        return True
        except Exception:
            pass
        return super().hasChildren(parent)

    def data(self, index, role=Qt.DisplayRole):
        # ツールチップに連携状態を付加（辞書参照のみ・ホバー時だけ呼ばれる）
        if role == Qt.ToolTipRole:
            try:
                from core.integrations import get_manager
                mgr = get_manager()
                if mgr.enabled:
                    sm = self.sourceModel()
                    si = self.mapToSource(index)
                    p = _safe_file_path(sm, si)
                    hit = mgr.status_for(p) if p else None
                    if hit:
                        # isDir はキャッシュ参照（ルート直下＝ドライブ行には触れない）
                        is_dir = bool(si.parent().isValid() and sm.isDir(si))
                        tip = _badge_tooltip(hit[0], hit[1], is_dir)
                        if tip:
                            base = super().data(index, role)
                            return ((str(base) + "\n") if base else "") + tip
            except Exception:
                pass
        return super().data(index, role)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """カラム別ソート。フォルダ優先＋親パス別のキー(name/type/date/size)。
        QFileInfo(stat)は再ソート中に不安定なため、モデルのキャッシュ値/DisplayRoleを使う。"""
        try:
            sm = self.sourceModel()
            parent = left.parent()
            if not parent.isValid():
                # ドライブ階層: isDir()/data() は切断ドライブで stat（21秒）を
                # 起こすため使わず、fileName（I/Oなし）の比較のみ
                return sm.fileName(left).lower() < sm.fileName(right).lower()
            ppath = self._norm(_safe_file_path(sm, parent))
            key, asc = self._col_sorts.get(ppath, ("name", True))
            an = (sm.data(left) or "").lower()
            bn = (sm.data(right) or "").lower()
            # フォルダは常に先頭（昇順/降順に関わらず）
            ld, rd = sm.isDir(left), sm.isDir(right)
            if ld != rd:
                return ld
            if key == "type":
                a = an.rsplit(".", 1)[-1] if "." in an else ""
                b = bn.rsplit(".", 1)[-1] if "." in bn else ""
                if a == b:
                    a, b = an, bn
            elif key == "date":
                a, b = sm.lastModified(left), sm.lastModified(right)
            elif key == "size":
                a, b = sm.size(left), sm.size(right)
            else:  # name
                a, b = an, bn
            return (a < b) if asc else (a > b)
        except Exception:
            return False

    @staticmethod
    def _fuzzy_match(pattern: str, name: str) -> bool:
        """
        部分一致 or 順序保存サブシーケンス一致 (N-3 ファジー検索)。
        例: 'chrahair' → 'chr_A_hair_sim_v012.ma' にヒット
        """
        if pattern in name:
            return True
        it = iter(name)
        return all(c in it for c in pattern)


# ---------------------------------------------------------------------------
# 外部サービス連携: 状態バッジ（アイコン右下の小さな丸）
# ---------------------------------------------------------------------------

# 状態 → (色ロール名, グリフ, ツールチップ)。実際の色は design_tokens.json の
# status_* から引く（テーマ追従。r82）。Mercury の «有彩色は1つ» は装飾に対する
# 規則で、意味を持つ状態色はその例外として彩度を落として使う。
_BADGE_STYLE = {
    "clean":       ("status_ok", "✓", ("最新", "Up to date")),
    "modified":    ("status_alert", "●", ("変更あり / チェックアウト中", "Modified / checked out")),
    "added":       ("status_ok", "+", ("追加予定", "Added")),
    "deleted":     ("status_muted", "−", ("削除予定", "Deleted")),
    "untracked":   ("status_info", "?", ("未追跡", "Untracked")),
    "conflict":    ("status_alert", "!", ("競合", "Conflict")),
    "ignored":     ("status_muted", "·", ("無視", "Ignored")),
    "outdated":    ("status_busy", "▲", ("サーバーに新しい版あり", "Newer revision on server")),
    "locked":      ("status_lock", "⚿", ("他者がロック中", "Locked by another user")),
    "other_open":  ("status_lock", "⚿", ("他者がチェックアウト中", "Checked out by another user")),
    "online_only": ("status_info", "☁", ("オンラインのみ", "Online-only")),
    "local":       ("status_ok", "✓", ("ローカルにあり", "Available locally")),
    "pinned":      ("status_ok", "●", ("常にこのデバイスに保持", "Always kept on this device")),
    "syncing":     ("status_busy", "↻", ("同期中", "Syncing")),
}


# Perforce は P4V のアイコン規約に合わせる（ChatGPT で P4V 準拠にデザインした
# PNG を resources/icons/badge_p4_*.png に配置）。
#   チェックアウト中=赤チェック / 追加=赤プラス / 削除=赤× / 未同期=黄三角 /
#   他者ロック=南京錠 / 他者チェックアウト=人＋チェック
#   「最新」「デポに無い」は P4V 同様に無印。フォルダにも付けない。
_P4_BADGE_PNG = {
    "modified":   "badge_p4_edit",
    "added":      "badge_p4_add",
    "deleted":    "badge_p4_delete",
    "outdated":   "badge_p4_outdated",
    "locked":     "badge_p4_locked",
    "other_open": "badge_p4_other",
}
_BADGE_ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "icons")
_BADGE_PIXMAP_CACHE = {}


def _badge_pixmap(stem: str):
    """バッジPNG（24x24）をキャッシュして返す。無ければ None（丸描画へ退避）。"""
    if stem in _BADGE_PIXMAP_CACHE:
        return _BADGE_PIXMAP_CACHE[stem]
    pm = None
    try:
        cand = QPixmap(os.path.join(_BADGE_ICON_DIR, stem + ".png"))
        if not cand.isNull():
            pm = cand
    except Exception:
        pm = None
    _BADGE_PIXMAP_CACHE[stem] = pm
    return pm


def _badge_kind(provider_key: str, state: str, is_dir: bool):
    """バッジの表示種別を決める（描画・ツールチップ共通の唯一の判定）。
    返り値: None（無印） / ("png", stem) / ("dot", _BADGE_STYLE の値)"""
    if provider_key == "p4":
        if is_dir:
            return None          # P4V 準拠: フォルダには状態を付けない
        stem = _P4_BADGE_PNG.get(state)
        if not stem:
            return None          # clean / untracked は無印
        if _badge_pixmap(stem) is not None:
            return ("png", stem)
        style = _BADGE_STYLE.get(state)
        return ("dot", style) if style else None
    style = _BADGE_STYLE.get(state)
    return ("dot", style) if style else None


class _FileOpNotifier(QObject):
    """ワーカースレッド → UI スレッドへの進捗/完了通知（r62、_run_file_op 用）。"""
    progress = Signal(int, int)
    finished = Signal(object, str)


_INTEG_RESULT_CONNECTED = False


def _on_integration_action_finished(ok: bool, label: str, msg: str):
    """連携 CLI 操作の結果（manager.action_finished、UIスレッドで受信。r58）。
    失敗は p4 等のメッセージをそのままダイアログで示す（黙殺しない）。
    成功はステータスバーへ。直後に表示中フォルダの状態を再取得してバッジを更新。
    複数エリアがあっても受け手はこの関数1つ（重複ダイアログ防止）。"""
    try:
        from core.compat import QApplication
        win = QApplication.activeWindow()
        if ok:
            sb = getattr(win, "statusBar", None)
            if callable(sb):
                sb().showMessage("⎇ %s — %s" % (label, msg), 8000)
        else:
            QMessageBox.warning(win, "⎇ %s" % label, msg)
        # 表示中の全ブラウザの状態を更新
        from core.integrations import get_manager
        mgr = get_manager()
        for w in QApplication.allWidgets():
            if isinstance(w, BrowserPanel):
                try:
                    mgr.refresh(w._current_path)
                except Exception:
                    pass
    except Exception as e:
        _mfm_log("integration result error: %r" % (e,))


def _badge_tooltip(provider_key: str, state: str, is_dir: bool = False) -> str:
    from core.i18n import tr
    style = _BADGE_STYLE.get(state)
    if not style:
        return ""
    if provider_key == "p4" and is_dir:
        return ""                # フォルダの Perforce 状態は fstat 直下のみで不正確
    names = {"git": "Git", "svn": "Subversion", "p4": "Perforce", "cloud": "Cloud"}
    return "%s: %s" % (names.get(provider_key, provider_key), tr(*style[2]))


class StatusBadgeDelegate(QStyledItemDelegate):
    """通常描画の後、連携状態があればアイコン右下にバッジを重ねる。
    状態は IntegrationManager の辞書参照のみ（描画中に I/O しない）。
    is_dir_of_index はプロキシindex→フォルダ判定（I/Oなし、_is_dir_index）。"""

    PNG_SIZE = 12   # 16px アイコンの右下に重ねる P4V 風バッジの描画サイズ

    def __init__(self, path_of_index, parent=None, is_dir_of_index=None,
                 is_expanded_index=None):
        super().__init__(parent)
        self._path_of_index = path_of_index
        self._is_dir_of_index = is_dir_of_index
        self._is_expanded_index = is_expanded_index   # r81: 展開中フォルダの目印
        try:
            from core.integrations import get_manager
            self._mgr = get_manager()
        except Exception:
            self._mgr = None

    def _is_dir(self, index) -> bool:
        fn = self._is_dir_of_index
        if fn is None:
            return False
        try:
            return bool(fn(index))
        except Exception:
            return False

    def paint(self, painter, option, index):
        # r81: 通常描画の前に「展開中フォルダ」の淡い下地を敷く（選択中は描かない）
        self.initStyleOption(option, index)
        _paint_expanded_mark(painter, option, index, self._is_expanded_index)
        super().paint(painter, option, index)
        mgr = self._mgr
        if mgr is None or not mgr.enabled:
            return
        try:
            p = self._path_of_index(index)
            if not p:
                return
            hit = mgr.status_for(p)
            if not hit:
                return
            kind = _badge_kind(hit[0], hit[1], self._is_dir(index))
            if not kind:
                return
            r = option.rect
            dec = option.decorationSize.width() if option.decorationSize.isValid() else 16
            if kind[0] == "png":
                pm = _badge_pixmap(kind[1])
                s = self.PNG_SIZE
                x = r.left() + min(dec, 18) - s + 4
                y = r.bottom() - s + 1
                painter.save()
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                painter.drawPixmap(x, y, s, s, pm)
                painter.restore()
                return
            role, glyph, _tip = kind[1]
            from core.theme_engine import qss_vars
            tv = qss_vars()
            size = 9
            # アイコン（左端 decorationSize）の右下に重ねる
            x = r.left() + min(dec, 18) - 3
            y = r.bottom() - size + 1
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QColor(tv["badge_ink"]))
            painter.setBrush(QColor(tv.get(role, tv["status_muted"])))
            painter.drawEllipse(x, y, size, size)
            f = painter.font()
            f.setPixelSize(7)
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(QColor(tv["on_primary"]))
            painter.drawText(x, y, size, size, Qt.AlignCenter, glyph)
            painter.restore()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# カラム幅リサイズハンドル
# Qt標準の QColumnViewGrip は「両スクロールバー表示時の角」にしか現れない
# （setCornerWidget 実装）ため実用不可。各カラム右端に自前のハンドルを重ねる。
# ---------------------------------------------------------------------------

class _DccAwareMime(QMimeData):
    """ドラッグするファイル一覧を «落とし先が DCC かどうか» で出し分ける mime（r90）。

    背景（2026-09-23 実害）: install.py を Maya のビューポートへ D&D すると、
    Maya は «OLE のドロップ処理の中で» スクリプトを同期実行する。インストーラが
    ダイアログを出すと Maya 側の Drop() が戻らず、ドラッグ元（このツール）は
    Windows の DoDragDrop の中で止まったまま（mfm_freeze.log で 250 秒以上）。
    さらに設定の «D&D 動作» もドロップ後に Manager から送るため、Maya 自身の
    ドロップ処理と二重実行になる（例: .ma が Maya に取り込まれ、さらに開く）。

    対策: ファイル一覧は «落とし先が取りに来た瞬間» に作る（Qt の遅延レンダリング:
    retrieveData）。カーソル下が Maya/Blender で、Manager 側で処理できる
    （ブリッジ接続中＋対応形式）なら、DCC には «空のファイル一覧» を渡して
    ネイティブ処理をさせない。ドロップ後に Manager がブリッジ経由（非同期）で
    実行するので、OLE の同期処理でツールが止まることは無い。
    自アプリ内・Explorer 等へは従来どおり実ファイルを渡す。"""

    def __init__(self, paths, decide):
        super().__init__()
        self._paths = list(paths)
        # () -> (app or None, [Manager が引き受けるパス])  ※r91 で bool から一覧へ
        self._decide = decide
        self.takeover_app = None         # Manager が引き受けた DCC
        self.handled = []                # Manager が引き受けたパス
        self.served_real_to_dcc = False  # DCC に実ファイルを渡した（ネイティブ処理）

    def formats(self):
        return ["text/uri-list"]

    def hasFormat(self, mimetype):
        return mimetype == "text/uri-list"

    def retrieveData(self, mimetype, preferred_type=None):
        if mimetype != "text/uri-list":
            return None
        try:
            app, handled = self._decide()
        except Exception:
            app, handled = None, []
        if not app:
            return [QUrl.fromLocalFile(p) for p in self._paths]
        handled = list(handled or [])
        if handled:
            self.takeover_app = app
            self.handled = handled
        # Manager が引き受けたものは DCC に渡さない。残り（Manager の各コマンドの
        # 対象外）は DCC 自身のドロップ処理に任せる
        keep = set(handled)
        served = [p for p in self._paths if p not in keep]
        if served:
            self.served_real_to_dcc = True
        return [QUrl.fromLocalFile(p) for p in served]


class _ColumnSizePopup(QFrame):
    """表示サイズ調整のスライダー（r85）。

    カラムヘッダの «表示切替（▦）» ボタンにマウスオーバーすると出る。
    リスト表示／サムネイル表示のどちらでも同じスライダーで調整し、
    値はモードごとに別々に覚える（リストは小さく、グリッドは大きくが自然なため）。
    ボタンとポップアップのどちらからもマウスが離れたら少し待って閉じる。"""

    LIST_RANGE = (16, 128)
    THUMB_RANGE = (48, 256)

    def __init__(self, owner):
        # r100: **Qt.Popup にしてはいけない**。Qt::Popup はマウスを grab するため、
        # ▦ にマウスオーバーした瞬間に入力を奪い、続く «▦ のクリック» は
        # ポップアップを閉じるだけで消費される（＝表示切替が効かない）。
        # r103: さらに «別ウィンドウ» をやめて **カラムビューの子ウィジェット**に
        # する。トップレベルだとウィンドウマネージャ側の都合（表示順・アクティブ
        # ウィンドウ判定・ツールチップとの重なり）で出ないことがあり、実機で
        # 「説明のツールチップしか出ない」状態になっていた（2026-09-25）。
        # 子ウィジェットなら必ず描かれ、grab もしない。
        super().__init__(owner)
        self.setAutoFillBackground(True)
        self._owner = owner          # CappedColumnView
        self._view = None            # 対象カラム（QListView）
        self._btn = None
        self._mode = "list"          # r104: 今どのモード用に出しているか
        self.setObjectName("mfmSizePopup")
        self.setFrameShape(QFrame.NoFrame)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)
        from core.i18n import tr
        self._icon = QLabel("▦ 全体", self)
        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setFixedWidth(130)
        self._slider.setToolTip(tr("表示サイズ", "Item size"))
        self._label = QLabel("", self)
        self._label.setFixedWidth(34)
        lay.addWidget(self._icon)
        lay.addWidget(self._slider)
        lay.addWidget(self._label)
        self._slider.valueChanged.connect(self._on_value)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(450)
        self._hide_timer.timeout.connect(self._maybe_hide)
        self.setMouseTracking(True)
        self._apply_style()

    def _apply_style(self):
        from core.theme_engine import qss_vars
        self.setStyleSheet(
            "#mfmSizePopup{background:%(surface_container_high)s;"
            "border:1px solid %(hairline_strong)s;border-radius:%(r_m)spx;}"
            "QLabel{color:%(on_surface_variant)s;font-size:%(label_px)spx;"
            "background:transparent;}"
            "QSlider::groove:horizontal{height:3px;background:%(hairline)s;"
            "border-radius:1px;}"
            "QSlider::handle:horizontal{width:12px;margin:-5px 0;"
            "background:%(primary)s;border-radius:6px;}"
            "QSlider::sub-page:horizontal{background:%(primary)s;border-radius:1px;}"
            % qss_vars())

    # ------------------------------------------------------------------
    def show_for(self, view, btn):
        """そのカラム用の値を読み込んで、ボタンの下に出す。"""
        self._view = view
        self._btn = btn
        mode = getattr(view, "_mfm_view_mode", "list")
        self._mode = mode          # r104: このスライダーが «今どのモード用» か
        lo, hi = self.THUMB_RANGE if mode == "thumb" else self.LIST_RANGE
        cur = self._owner.column_item_size(view)
        self._slider.blockSignals(True)
        self._slider.setRange(lo, hi)
        self._slider.setValue(max(lo, min(hi, int(cur))))
        self._slider.blockSignals(False)
        self._label.setText("%dpx" % self._slider.value())
        self._apply_style()
        self.adjustSize()
        # r103: 親（カラムビュー）の座標系で、ボタンの真下に出す。
        # はみ出す時は内側へ寄せる（子ウィジェットなのでクリップされるため）。
        pos = self._owner.mapFromGlobal(btn.mapToGlobal(QPoint(0, btn.height() + 2)))
        x = max(2, min(pos.x(), self._owner.width() - self.width() - 2))
        y = max(2, min(pos.y(), self._owner.height() - self.height() - 2))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self._hide_timer.stop()

    def _on_value(self, v):
        self._label.setText("%dpx" % v)
        if self._view is None:
            return
        mode = getattr(self._view, "_mfm_view_mode", "list")
        # r104: ▦ を押して表示モードが変わった直後は、スライダーの目盛りが
        # «前のモードのレンジ» のまま残っている。そのまま適用すると
        # リストの値（例: 40px）がグリッドのセル寸法として保存され、
        # «切り替えるたびにグリッドが小さい» 状態になる（ユーザー報告
        # 2026-09-25）。モードがズレていたら読み直すだけにする。
        if mode != getattr(self, "_mode", mode):
            self.show_for(self._view, self._btn)
            return
        # «全体的な表示サイズ» を変える。同じモードの全カラムへ適用。
        self._owner.set_item_size_all(v, mode)

    # --- 閉じる判定（ボタン上／ポップアップ上のどちらでもなければ閉じる） ---
    def request_hide(self):
        self._hide_timer.start()

    def keep_open(self):
        self._hide_timer.stop()

    def _under_cursor(self, w):
        try:
            return w is not None and w.rect().contains(
                w.mapFromGlobal(QCursor.pos()))
        except Exception:
            return False

    def _maybe_hide(self):
        if self._under_cursor(self) or self._under_cursor(self._btn):
            self._hide_timer.start()
            return
        self.hide()

    def enterEvent(self, e):
        self.keep_open()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.request_hide()
        super().leaveEvent(e)


class _SizeButtonHover(QObject):
    """▦ ボタンのホバーでサイズ調整スライダーを出すフィルタ（r85）。"""

    def __init__(self, owner, view, btn):
        super().__init__(btn)
        self._owner = owner
        self._view = view
        self._btn = btn

    def eventFilter(self, obj, event):
        et = event.type()
        if et == _QtCore.QEvent.Enter:
            try:
                self._owner.size_popup().show_for(self._view, self._btn)
            except Exception as e:
                _mfm_log("size popup error: %r" % (e,))
        elif et == _QtCore.QEvent.Leave:
            try:
                self._owner.size_popup().request_hide()
            except Exception:
                pass
        return False


class _ColumnResizeHandle(QWidget):
    """カラム境界の縦ハンドル。ドラッグで幅変更、ダブルクリックで内容に合わせる。

    QColumnView のビューポートを親にし、カラムの右辺を «またぐ» 形で配置する
    （左半分はカラム右端、右半分は隣カラムの左端）。カラム内に置くと縦
    スクロールバーと重なり細くせざるを得ず「掴みにくい」ため。"""

    WIDTH = 12
    MIN_W = 120
    MAX_W = 1400

    def __init__(self, column_view, view):
        super().__init__(column_view.viewport())
        self._cv = column_view
        self._view = view
        self._drag_x = None
        self._start_w = 0
        self._hover = False
        self.setFixedWidth(self.WIDTH)
        self.setCursor(Qt.SplitHCursor)
        self.setMouseTracking(True)
        self.setToolTip("ドラッグで幅を変更 / ダブルクリックで内容に合わせる")

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            # AuthKit ヘアライン（ホバー/ドラッグ中は帯全体を明るく）
            if self._drag_x is not None or self._hover:
                p.fillRect(self.rect(), QColor(186, 215, 247, 70))
            # r105: スクロールバーの左に置くようになったので、罫線は帯の
            # 右端に寄せる（掴める帯＝その左側、と分かるように）
            p.fillRect(self.width() - 2, 0, 2, self.height(),
                       QColor(186, 215, 247, 90))
        finally:
            p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_x = e.globalPosition().toPoint().x() \
                if hasattr(e, "globalPosition") else e.globalPos().x()
            self._start_w = self._view.width()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_x is None:
            return
        gx = e.globalPosition().toPoint().x() \
            if hasattr(e, "globalPosition") else e.globalPos().x()
        w = max(self.MIN_W, min(self.MAX_W, self._start_w + (gx - self._drag_x)))
        self._cv.set_column_width_for_view(self._view, w)
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._drag_x is not None:
            self._drag_x = None
            self._cv.persist_column_widths()
            self.update()
            e.accept()
        else:
            super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        w = self._cv.fit_width_for_view(self._view)
        if w:
            self._cv.set_column_width_for_view(self._view, w)
            self._cv.persist_column_widths()
        e.accept()


# ---------------------------------------------------------------------------
# Column-capped View
# ---------------------------------------------------------------------------

class CappedColumnView(QColumnView):
    """
    QColumnView with configurable maximum column depth.

    仕様: 選択がmax_depthより深くなったら、ルートを1段下げて
    可視カラム数をmax_depth以内に保つ（macOS Finder相当の挙動）。

    実装注意: currentChanged の中で setRootIndex を呼ぶと
    QColumnView内部のカラム再構築と競合してクラッシュするため、
    QTimer.singleShot(0) でイベントループ一巡後に実行する。
    """

    def __init__(self, max_depth: int = 4, parent=None):
        super().__init__(parent)
        self._max_depth = max_depth
        self._go_up_cb = None
        self._thumb_mgr = None
        self._merge_cb = None
        self._flat_cb = None        # インライン平坦カラム要求コールバック cb(dirs)
        self._thumb_prefetch_cb = None  # r84: サムネ表示化した列の先読み cb(folder)
        self._item_size_cb = None       # r102: サイズ変更を他ビューへ配る cb(px, mode)
        self._size_popup = None         # r85: 表示サイズ調整スライダー
        self._flatten_view = None   # 現在「平坦」ONのカラムビュー（排他制御用）
        self._pending_multi_drag = None
        self._maya_drop_cb = None      # Mayaへのドロップ検出時コールバック(paths)
        self._file_drop_cb = None      # r87: カラムへのファイルドロップ cb(paths, dest, move)
        self._dcc_takeover_cb = None   # r90: DCC への落下を Manager が引き受けるか cb(paths, app)
        self._selected_dir_paths = set()
        self._mfm_columns = []      # r81: 生成済みカラムビュー（展開中フォルダ判定用）
        # r83: ナビゲーション後にユーザーが項目をクリックしたか。
        # 遅延実行される «カラム自己修復» が選択を壊さないためのガード。
        self._mfm_user_selected = False
        # 未読み込みカラムの「読み込み中」スピナー（カラム毎に個別表示）。
        # 判定: canFetchMore（fetch未開始）に加え、fetch開始後も列挙が終わる
        # まで（directoryLoaded 受信まで）rowCount==0 なら読み込み中とみなす。
        # 完了待ちを directoryLoaded «だけ» に依存してはならない（pip版Qtの
        # 再発火しない罠）ため、rowCount>0 でも即座に完了扱いにする。
        self._loaded_dirs = set()      # directoryLoaded 済みパス（normcase）
        self._loading_first_seen = {}  # path → 初回観測時刻（安全弁用）
        self._spin_reported = {}       # path → 最終ログ時刻（長期表示の調査用）
        self._spin_phase = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(150)
        self._loading_timer.timeout.connect(self._update_loading_overlays)
        self._loading_timer.start()

    def set_max_depth(self, depth: int):
        self._max_depth = depth

    # ── スクロール整合性（r54） ───────────────────────────────────────────
    # QColumnView は「カラム位置 x」「内部 offset」「hbar.value」の3つが常に
    # x == offset == -value の関係で動く前提。全カラム再構築（setRootIndex →
    # closeColumns）で offset は 0 に戻るが、スクロールアニメーション中は
    # updateScrollbars() が早期 return して hbar の値が残るため、
    # 「offset=0 / value=70」のような食い違いが生まれ、以後 value とカラム位置の
    # 対応が恒久的に 70px ずれる。症状＝末尾カラムが途中で切れる／右に空白／
    # スクロール最右端でも見えない階層がある（2026-09 実機、オフスクリーンで再現）。
    # 対策: 再構築の直前に必ず value を 0 に戻す（value 変化はカラム移動と
    # offset 更新を伴うため整合が保たれる）。
    def _reset_hscroll_for_rebuild(self):
        try:
            hbar = self.horizontalScrollBar()
            if hbar is not None and hbar.value() != 0:
                hbar.setValue(0)
        except Exception:
            pass

    def setRootIndex(self, index):
        self._reset_hscroll_for_rebuild()
        super().setRootIndex(index)
        # r71: 深いパスへの再構築では 260ms 時点でまだ current の列が生成されて
        # いない（列挙が非同期）ことがあり、1回では右外に残った（mayapy の
        # 回帰スイート「navigate mid-anim」で再現）。数回に分けて再確認する。
        # 各回とも «既に見えていれば何もしない» ので副作用は無い。
        # 初回（260ms、Qt 自身のスクロールアニメーション後）は無条件。2回目以降は
        # 前回の確認以降に hbar が動いていたら（＝ユーザーが手でスクロールした）
        # 追いかけずに打ち切る。
        token = object()
        self._ensure_token = token
        self._ensure_last_val = None

        def _retry():
            if getattr(self, "_ensure_token", None) is not token:
                return
            hbar = self.horizontalScrollBar()
            last = getattr(self, "_ensure_last_val", None)
            if last is not None and hbar.value() != last:
                self._ensure_token = None
                return
            self._ensure_current_column_visible()
            self._ensure_last_val = hbar.value()

        for _ms in (260, 700, 1300, 2200):
            QTimer.singleShot(_ms, _retry)

    # ── ファイルではカラムを横スクロールさせない（r57） ────────────────
    # QColumnView::scrollTo は「index の列＋その子列」を可視化するために hbar を
    # アニメーションで動かす（QAbstractItemView::currentChanged 等から仮想呼び出し）。
    # ファイルは «操作対象» であり、クリックで列が滑ると操作の的が動いて
    # ストレスになる（指摘あり）。ファイルに対する scrollTo は横移動を行わず、
    # 所属列の縦スクロール（項目の可視化）だけ行う。フォルダは従来通り。
    def scrollTo(self, index, hint=QAbstractItemView.EnsureVisible):
        try:
            if index is not None and index.isValid() and not self._is_dir_index(index):
                _mfm_uilog("scrollTo(file) 抑止: %r" % (index.data(),))
                parent = index.parent()
                for v in self.findChildren(QListView):
                    if getattr(v, "_mfm_header", None) is not None \
                            and v.isVisible() and v.rootIndex() == parent:
                        try:
                            v.scrollTo(index)      # 縦方向のみ（列内）
                        except Exception:
                            pass
                        break
                return
        except Exception:
            pass
        super().scrollTo(index, hint)

    # ── ゆっくり2回クリックで名前変更（r67、Explorer 準拠） ──────────────
    def _schedule_reclick_rename(self, path: str):
        if not path:
            return
        self._cancel_reclick_rename()
        t = QTimer(self)
        t.setSingleShot(True)
        try:
            interval = QApplication.doubleClickInterval() + 80
        except Exception:
            interval = 480
        t.setInterval(interval)
        t.timeout.connect(lambda: self._fire_reclick_rename(path))
        t.start()
        self._reclick_timer = t

    def _cancel_reclick_rename(self):
        t = getattr(self, "_reclick_timer", None)
        if t is not None:
            try:
                t.stop()
                t.deleteLater()
            except Exception:
                pass
            self._reclick_timer = None

    def _fire_reclick_rename(self, path: str):
        self._reclick_timer = None
        cb = getattr(self, "_rename_cb", None)
        if callable(cb):
            try:
                cb(path)
            except Exception as e:
                _mfm_log("reclick rename error: %r" % (e,))

    def note_file_click(self, name):
        """ファイルクリック直後 1.2 秒間、水平スクロール値の変化を呼び出し元付きで
        記録する（原因切り分け用・常時有効・軽量）。"""
        try:
            self._file_click_t = _time_mod.monotonic()
            hbar = self.horizontalScrollBar()
            _mfm_uilog("file-click %r hval=%d/%d" % (name, hbar.value(), hbar.maximum()))
            if not getattr(self, "_hwatch_connected", False):
                hbar.valueChanged.connect(self._on_hbar_changed_watch)
                self._hwatch_connected = True
        except Exception:
            pass

    def _on_hbar_changed_watch(self, v):
        try:
            t0 = getattr(self, "_file_click_t", 0.0)
            if t0 and _time_mod.monotonic() - t0 < 1.2:
                _mfm_uilog("hscroll moved after file-click: value=%d" % v, with_stack=True)
        except Exception:
            pass

    def setModel(self, model):
        self._reset_hscroll_for_rebuild()
        super().setModel(model)

    def _ensure_current_column_visible(self):
        """再構築直後は Qt の scrollTo がアニメーション中で早期 return し、
        目的のカラムが右外に残ることがある。アニメーション無しで hbar の値を
        直接動かして «current のカラム＋その子カラム» を可視域に入れる
        （scrollTo は changeCurrentColumn を伴い選択モデルを触るため使わない）。"""
        try:
            cur = self.currentIndex()
            if not cur.isValid():
                return
            cols = self._column_views_sorted()
            if not cols:
                return
            target = None
            for i, v in enumerate(cols):
                if v.rootIndex() == cur.parent():
                    target = i
                    break
            if target is None:
                return
            hbar = self.horizontalScrollBar()
            vw = self.viewport().width()
            left = cols[target].x()
            right_i = min(target + 1, len(cols) - 1)
            right = cols[right_i].x() + cols[right_i].width()
            if left >= 0 and right <= vw:
                return
            # 右端を合わせる（左端が隠れるなら左端優先）
            delta = right - vw if right > vw else left
            new_val = max(hbar.minimum(), min(hbar.maximum(), hbar.value() + delta))
            if new_val != hbar.value():
                _mfm_log("ensure_visible: hval %d -> %d (left=%d right=%d vw=%d)"
                         % (hbar.value(), new_val, left, right, vw))
                hbar.setValue(new_val)
        except Exception:
            pass

    def set_go_up_callback(self, cb):
        """◀ボタン押下時に呼ぶコールバック（1階層上げる）を登録。"""
        self._go_up_cb = cb

    def note_dir_loaded(self, path: str):
        """QFileSystemModel.directoryLoaded の記録（スピナー消灯の根拠）。"""
        try:
            key = os.path.normcase(os.path.normpath(path))
            self._loaded_dirs.add(key)
            self._loading_first_seen.pop(key, None)
            self._spin_reported.pop(key, None)
        except Exception:
            pass

    # ── r81: 展開中フォルダの目印 ─────────────────────────────────────
    def _track_column(self, view):
        """生成されたカラムを覚え、破棄時に外す。右隣のカラムが出来た/消えた
        タイミングで各カラムを再描画し、目印の出入りを即反映する。"""
        self._mfm_columns.append(view)
        try:
            view.destroyed.connect(lambda *_: self._on_column_destroyed())
        except Exception:
            pass
        QTimer.singleShot(0, self._repaint_columns)

    def _on_column_destroyed(self):
        self._live_columns()
        self._repaint_columns()

    def _live_columns(self):
        alive = []
        for c in self._mfm_columns:
            try:
                c.rootIndex()
                alive.append(c)
            except RuntimeError:
                continue
        self._mfm_columns = alive
        return alive

    def _repaint_columns(self):
        for c in self._live_columns():
            try:
                c.viewport().update()
            except Exception:
                pass

    def _is_expanded_index(self, index) -> bool:
        """index のフォルダの中身を表示しているカラムが存在するか
        （＝そのフォルダが「展開中」か）。"""
        if not index.isValid():
            return False
        for c in self._live_columns():
            try:
                if c.rootIndex() == index:
                    return True
            except Exception:
                continue
        return False

    def createColumn(self, index):
        """各カラム生成時のセットアップ（D&D設定・ヘッダ・スピナー）。
        旧「◀ 上の階層へ」オーバーレイボタンは廃止（使われず紛らわしい、
        との指摘のため。2026-09-03）。"""
        view = super().createColumn(index)
        self._track_column(view)
        # 各カラム(子ビュー)へ Explorer 互換のD&D設定を適用。
        # これを怠るとカラム上でのドロップが効かない。
        try:
            view.setDragEnabled(True)
            view.setAcceptDrops(True)
            view.setDropIndicatorShown(True)
            view.setDragDropMode(QAbstractItemView.DragDrop)
            view.setDefaultDropAction(Qt.MoveAction)
            view.setDragDropOverwriteMode(False)
            view.setEditTriggers(QAbstractItemView.NoEditTriggers)
            # 各カラムで Shift/Ctrl の複数選択を効かせる（QColumnView単体だと
            # 列ビューに伝播せず効かないため明示設定）
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        except Exception:
            pass
        folder_path = self._path_for_index(index)
        # r85: 保存済みの «リスト表示のアイコンサイズ» を適用（既定 16px）
        try:
            view.setIconSize(QSize(self.column_item_size(view),
                                   self.column_item_size(view)))
        except Exception:
            pass
        # 連携状態バッジ付きデリゲート（通常描画＋右下に小さな丸）
        try:
            view.setItemDelegate(StatusBadgeDelegate(self._path_of_index, view,
                                                is_dir_of_index=self._is_dir_index,
                                                is_expanded_index=self._is_expanded_index))
        except Exception:
            pass
        # このカラムのフォルダの連携状態をワーカーへ要求（表示時に1回）
        try:
            if folder_path:
                self._integrations().request_status(folder_path)
        except Exception:
            pass
        # カラム上部に「このカラムだけに効く」フィルタ／ソートのヘッダを設置
        self._build_column_header(view, folder_path)
        # 右端のリサイズハンドル（幅変更・ダブルクリックで自動調整）
        view._mfm_resize_handle = _ColumnResizeHandle(self, view)
        view._mfm_resize_handle.show()
        # ハンドルはビューポートの子（カラムの子ではない）なので、カラム破棄時に
        # 一緒に消す
        _h = view._mfm_resize_handle
        view.destroyed.connect(lambda *_a, h=_h: h.deleteLater())
        self._reposition_column_header(view)
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)
        # 「読み込み中」スピナー（このカラムが未読み込みの間だけ表示）
        spin = QLabel(view.viewport())
        spin.setAttribute(Qt.WA_TransparentForMouseEvents)
        spin.setAlignment(Qt.AlignCenter)
        from core.theme_engine import qss_vars
        spin.setStyleSheet(
            "QLabel{color:%(on_surface_variant)s;background:%(scrim)s;"
            "border:1px solid %(hairline)s;border-radius:%(r_m)spx;"
            "padding:6px 14px;font-size:%(body_px)spx;}" % qss_vars())
        spin.hide()
        view._mfm_loading = spin
        return view

    def _update_loading_overlays(self):
        """各カラムの読み込み状態を確認し、未読み込みのカラムにだけ
        「読み込み中」スピナーを表示する（150ms毎のポーリング）。"""
        self._spin_phase = (self._spin_phase + 1) % 4
        glyph = "◐◓◑◒"[self._spin_phase]
        # レイアウトの自己修復（カラム群の右ずれ検知）も同じ周期で行う
        self._check_column_layout_health()
        try:
            views = [v for v in self.findChildren(QListView)
                     if getattr(v, "_mfm_loading", None) is not None]
        except RuntimeError:
            return
        for view in views:
            try:
                spin = view._mfm_loading
                root = view.rootIndex()
                if not root.isValid() or not view.isVisible():
                    spin.hide()
                    continue
                # プロキシ → ソース(QFileSystemModel)へ解決
                m = view.model()
                idx = root
                while m is not None and hasattr(m, "mapToSource"):
                    idx = m.mapToSource(idx)
                    m = m.sourceModel()
                if m is None or not hasattr(m, "canFetchMore"):
                    spin.hide()
                    continue
                # 【重要】ソース側indexが無効なら何もしない。無効indexは
                # QFileSystemModel 内部で «ルート（マイコンピュータ）» を指すため、
                # canFetchMore が True になり続け、fetchMore で全ドライブ列挙
                # （切断ドライブで21秒×N）を誘発する。
                if not idx.isValid():
                    spin.hide()
                    continue
                # このカラムのフォルダの列挙が完了しているか。
                # 注意: rowCount は列挙完了前でも >0 になり得る
                # （起動時のパス復元では祖先チェーンが先にノード化され、
                #  各カラムに1件だけ表示される。r23はこれを見逃した）。
                # そのため判定は「directoryLoaded 受信済みか」を軸にする。
                p = ""
                try:
                    p = os.path.normcase(os.path.normpath(_safe_file_path(m, idx)))
                except Exception:
                    pass
                if not p:
                    spin.hide()
                    continue
                import time as _time
                now = _time.monotonic()
                loading = False
                reason = ""
                if p in self._loaded_dirs:
                    loading = False          # 列挙完了済み（最優先で消灯）
                elif m.canFetchMore(idx):
                    # fetch未開始 → 読み込みを促しつつスピナー表示
                    reason = "canFetchMore"
                    try:
                        m.fetchMore(idx)
                    except Exception:
                        pass
                    first = self._loading_first_seen.setdefault(p, now)
                    loading = (now - first) < 60.0   # 安全弁
                else:
                    # fetchは開始済みだが列挙完了通知が未受信（列挙スレッド動作中）
                    reason = "awaiting directoryLoaded"
                    first = self._loading_first_seen.setdefault(p, now)
                    # 安全弁: directoryLoaded を取り逃しても60秒で消灯
                    loading = (now - first) < 60.0
                if loading:
                    # 5秒以上出続ける列は原因調査用に状態を記録（30秒毎）
                    first = self._loading_first_seen.get(p, now)
                    age = now - first
                    last = self._spin_reported.get(p, -999.0)
                    if age > 5.0 and now - last > 30.0:
                        self._spin_reported[p] = now
                        try:
                            rows = view.model().rowCount(root)
                        except Exception:
                            rows = -1
                        _mfm_slow_note(
                            "SPINNER %.0fs path=%r reason=%s rows=%d "
                            "loaded_recorded=%s" % (
                                age, p, reason, rows, p in self._loaded_dirs))
                if loading:
                    from core.i18n import tr as _tr
                    spin.setText("%s %s" % (
                        glyph, _tr("読み込み中...", "Loading...")))
                    spin.adjustSize()
                    vp = view.viewport()
                    spin.move(max(0, (vp.width() - spin.width()) // 2),
                              max(0, (vp.height() - spin.height()) // 2))
                    spin.show()
                    spin.raise_()
                else:
                    spin.hide()
            except RuntimeError:
                continue   # カラム再構築中に破棄されたビュー
            except Exception:
                continue

    def _emit_go_up(self, folder_path=None):
        if callable(self._go_up_cb):
            self._go_up_cb(folder_path)

    # ------------------------------------------------------------------
    # カラム別フィルタ／ソート ヘッダ
    # ------------------------------------------------------------------
    _COL_HEADER_H = 52   # 2行（フィルタ行＋ソート/平坦/表示 行）

    @property
    def _SORT_KEYS(self):
        from core.i18n import tr
        return [("name", tr("名前", "Name")), ("type", tr("種類", "Type")),
                ("date", tr("日付", "Date")), ("size", tr("サイズ", "Size"))]

    def set_thumb_mgr(self, mgr):
        self._thumb_mgr = mgr

    def set_merge_callback(self, cb):
        self._merge_cb = cb

    # ── r85: 表示サイズ（リスト／サムネイル共通のスライダー） ──────────
    DEFAULT_SIZE = {"list": 16, "thumb": 96}

    def size_popup(self):
        if self._size_popup is None:
            self._size_popup = _ColumnSizePopup(self)
        return self._size_popup

    def _size_key(self, mode: str) -> str:
        return "column_icon_size_thumb" if mode == "thumb" else "column_icon_size_list"

    def column_item_size(self, view) -> int:
        """そのカラムの表示サイズ(px)。モードごとに別の値を持つ。
        未設定なら設定ファイルの値 → 既定値の順。"""
        mode = getattr(view, "_mfm_view_mode", "list")
        cur = getattr(view, "_mfm_item_size", {}).get(mode) if \
            isinstance(getattr(view, "_mfm_item_size", None), dict) else None
        if cur:
            return int(cur)
        sm = getattr(self, "_sm_widths", None)
        if sm is not None:
            try:
                return int(sm.get(self._size_key(mode), self.DEFAULT_SIZE[mode]))
            except Exception:
                pass
        return self.DEFAULT_SIZE[mode]

    def set_column_item_size(self, view, px: int, save: bool = True):
        """表示サイズを適用する。リスト＝アイコン寸法、サムネイル＝セル寸法。
        同じスライダーで両モードを調整する（ユーザー指示）。"""
        mode = getattr(view, "_mfm_view_mode", "list")
        px = max(8, int(px))
        store = getattr(view, "_mfm_item_size", None)
        if not isinstance(store, dict):
            store = {}
            view._mfm_item_size = store
        store[mode] = px
        try:
            view.setIconSize(QSize(px, px))
            if mode == "thumb":
                view.setGridSize(QSize(px + 18, px + ThumbnailDelegate._TEXT_H + 8))
                if self._thumb_mgr is not None:
                    view.setItemDelegate(ThumbnailDelegate(
                        self._thumb_mgr, px, view,
                        is_expanded_index=self._is_expanded_index))
            else:
                view.setGridSize(QSize())
            view.doItemsLayout()
            view.viewport().update()
            self._reposition_column_header(view)
        except Exception as e:
            _mfm_log("set_column_item_size error: %r" % (e,))
        if save:
            sm = getattr(self, "_sm_widths", None)
            if sm is not None:
                try:
                    sm.set(self._size_key(mode), px)
                except Exception:
                    pass

    def set_item_size_all(self, px: int, mode: str = None, save: bool = True):
        """表示サイズを **同じモードの全カラムへ一括適用** する（r102）。

        スライダーは «全体的な表示サイズ» を変えるもの、というユーザー指示。
        モードごとに別の値を持つ（リストは小さく、グリッドは大きくが自然）。
        保存もここで 1 回だけ行い、新しく開くカラムにも効く。"""
        px = max(8, int(px))
        applied = 0
        for v in self._live_columns():
            if mode is not None and getattr(v, "_mfm_view_mode", "list") != mode:
                continue
            self.set_column_item_size(v, px, save=False)
            applied += 1
        if callable(getattr(self, "_item_size_cb", None)):
            try:
                self._item_size_cb(px, mode)      # 平坦／サムネビュー側へ
            except Exception:
                pass
        if save:
            sm = getattr(self, "_sm_widths", None)
            if sm is not None:
                try:
                    sm.set(self._size_key(mode or "list"), px)
                except Exception:
                    pass
        return applied

    def set_item_size_callback(self, cb):
        """カラム以外のビュー（平坦カラム等）へもサイズ変更を配るコールバック。"""
        self._item_size_cb = cb

    def set_dcc_takeover_callback(self, cb):
        """cb(paths, app) -> bool: DCC への落下を Manager が処理できるか（r90）。"""
        self._dcc_takeover_cb = cb

    def make_drag_mime(self, paths):
        """ドラッグ用の mime を作る（DCC への落下は Manager が引き受ける。r90）。
        判定はドラッグ 1 回につき DCC ごとに 1 度だけ（接続確認のコストを抑える）。"""
        cache = {}
        cb_ref = self

        def decide():
            app = _cursor_over_dcc_window()
            if not app:
                return None, []
            if app not in cache:
                cb = cb_ref._dcc_takeover_cb
                try:
                    r = cb(list(paths), app) if callable(cb) else []
                    # 旧仕様（bool）にも対応
                    cache[app] = (list(paths) if r is True else
                                  [] if not r else list(r))
                except Exception as e:
                    _mfm_log("dcc-takeover check error: %r" % (e,))
                    cache[app] = []
                _mfm_log("dcc-takeover: app=%s take=%d/%d"
                         % (app, len(cache[app]), len(paths)))
            return app, cache[app]

        mime = _DccAwareMime(paths, decide)
        mime._mfm_decide = decide
        return mime

    def set_file_drop_callback(self, cb):
        """カラムへのファイルドロップ cb(paths, dest_dir, move: bool)（r87）。"""
        self._file_drop_cb = cb

    def set_thumb_prefetch_callback(self, cb):
        """サムネイル表示に切り替えたカラムの先読みを依頼する cb(folder)。"""
        self._thumb_prefetch_cb = cb

    def set_flat_callback(self, cb):
        """インライン平坦カラムの表示要求コールバック cb(dirs) を登録。"""
        self._flat_cb = cb

    def _request_flat(self, dirs):
        if callable(self._flat_cb):
            self._flat_cb(list(dirs or []))

    def _reset_flatten_toggle(self):
        """平坦トグルの見た目と内部状態を確実にOFFへ戻す。

        平坦カラムがナビゲーション等で閉じた時に呼ぶ。ONのまま放置すると
        次のボタン押下が«OFF操作»になり「押しても平坦ビューが出ない」
        ように見える（実機報告の症状）。"""
        v = self._flatten_view
        self._flatten_view = None
        if v is not None:
            try:
                v._mfm_flatten = False
                b = getattr(v, "_mfm_flat_btn", None)
                if b is not None:
                    b.setChecked(False)
            except Exception:
                pass

    def _proxy_model(self):
        m = self.model()
        return m if hasattr(m, "set_column_filter") else None

    def _build_column_header(self, view, folder_path):
        """カラム上部に2行のヘッダを重ねる。
        1行目=フィルタ欄／2行目=ソート項目プルダウン＋昇順降順＋平坦化＋表示切替。"""
        proxy = self._proxy_model()
        if proxy is None or not folder_path:
            return
        try:
            view.setViewportMargins(0, self._COL_HEADER_H, 0, 0)
        except Exception:
            pass
        hdr = QWidget(view)
        hdr.setObjectName("mfmColHeader")
        # 注意: グローバルQSSの min-height(26px) がヘッダ内の固定20px指定を
        # 上書きし、2行合計がヘッダ予約高(_COL_HEADER_H)を超えて先頭項目に
        # 被った実例あり。ヘッダ内では min-height を必ず明示して打ち消す。
        from core.theme_engine import qss_vars
        hdr.setStyleSheet(
            "#mfmColHeader{background:%(column_header)s;"
            "border-bottom:1px solid %(hairline)s;}"
            "QLineEdit{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:10px;"
            "min-height:18px;max-height:20px;padding:0 8px;}"
            "QLineEdit:focus{border-color:%(primary)s;}"
            "QComboBox{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:10px;"
            "min-height:18px;max-height:20px;padding:0 8px;}"
            "QToolButton{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:9px;"
            "min-height:16px;max-height:18px;padding:0px 6px;}"
            "QToolButton:hover{background:%(fill_subtle_hover)s;}"
            "QToolButton:checked{background:%(cta_tint)s;color:%(on_primary_container)s;"
            "border-color:%(primary)s;}"
            % qss_vars()
        )
        vlay = QVBoxLayout(hdr)
        vlay.setContentsMargins(3, 3, 3, 3)
        vlay.setSpacing(3)
        # --- 1行目: フィルタ ＋ 排他フィルタ ---
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(3)
        from core.i18n import tr
        edit = QLineEdit(hdr)
        edit.setPlaceholderText(tr("フィルタ", "Filter"))
        edit.setClearButtonEnabled(True)
        edit.setFixedHeight(20)
        edit.setText(proxy.get_column_filter(folder_path))
        edit.textChanged.connect(
            lambda t, p=folder_path: proxy.set_column_filter(p, t))
        excl = QLineEdit(hdr)
        excl.setPlaceholderText(tr("排他", "Exclude"))
        excl.setClearButtonEnabled(True)
        excl.setFixedHeight(20)
        excl.setToolTip(tr("入力に一致するファイルを一覧から除外",
                           "Hide files matching this text"))
        excl.setText(proxy.get_column_exclude(folder_path))
        excl.textChanged.connect(
            lambda t, p=folder_path: proxy.set_column_exclude(p, t))
        row1.addWidget(edit, 1)
        row1.addWidget(excl, 1)
        vlay.addLayout(row1)
        # --- 2行目: ソート項目 + 昇順降順 + 平坦化 + 表示 ---
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(3)
        cur_key, cur_asc = proxy.get_column_sort(folder_path)
        sort_combo = QComboBox(hdr)
        sort_combo.setFixedHeight(20)
        for key, label in self._SORT_KEYS:
            sort_combo.addItem(label, key)
        for i in range(sort_combo.count()):
            if sort_combo.itemData(i) == cur_key:
                sort_combo.setCurrentIndex(i)
                break
        order_btn = QToolButton(hdr)
        order_btn.setCheckable(True)
        order_btn.setFixedSize(26, 20)
        order_btn.setChecked(not cur_asc)            # checked=降順
        order_btn.setText("▲" if cur_asc else "▼")
        order_btn.setToolTip("昇順／降順")

        def _apply_sort(p=folder_path, c=sort_combo, b=order_btn):
            key = c.currentData()
            asc = not b.isChecked()
            b.setText("▲" if asc else "▼")
            if proxy:
                proxy.set_column_sort(p, key, asc)
        sort_combo.currentIndexChanged.connect(lambda _i: _apply_sort())
        order_btn.clicked.connect(lambda _c=False: _apply_sort())

        flat_btn = QToolButton(hdr)
        flat_btn.setText(tr("平坦", "Flat"))
        flat_btn.setCheckable(True)
        flat_btn.setChecked(getattr(view, "_mfm_flatten", False))
        flat_btn.setFixedHeight(20)
        flat_btn.setToolTip(tr("選択中フォルダ以下の全ファイルを平坦表示"
                               "（選択が無ければこのカラムのフォルダ全体）",
                               "Show all files under the selected folder as a "
                               "flat list (whole column folder if nothing is "
                               "selected)"))

        def _on_flat_btn(checked, v=view, p=folder_path):
            # 状態変化の到達を最初に必ず記録（不達調査用）
            _mfm_log("flat_btn toggled: checked=%s col=%r" % (checked, p))
            try:
                self._toggle_flatten(v, checked, p)
            except Exception as e:
                _mfm_log("flat_btn ERROR: %r" % (e,))

        # clicked ではなく toggled を使う。toggled はチェック状態の変化
        # そのもので発火するため、ボタンの見た目が切り替わる限り必ず届く
        # （実機で clicked が slot に届かない事象への対策）
        flat_btn.toggled.connect(_on_flat_btn)
        view._mfm_flat_btn = flat_btn
        view_btn = QToolButton(hdr)
        view_btn.setText("▦")
        view_btn.setFixedSize(26, 20)
        # r103: 長いツールチップがスライダーと同じ位置に出て隠していたため短くする
        view_btn.setToolTip(tr("リスト⇄サムネイル切替",
                               "Toggle list/thumbnail view (hover for item size)"))
        view_btn.clicked.connect(lambda _c=False, v=view: self._toggle_view_mode(v))
        # r85: ボタンにマウスオーバー → 表示サイズのスライダーを出す
        _hover = _SizeButtonHover(self, view, view_btn)
        view_btn.installEventFilter(_hover)
        view_btn._mfm_size_hover = _hover     # GC 防止

        row2.addWidget(sort_combo, 1)
        row2.addWidget(order_btn, 0)
        row2.addWidget(flat_btn, 0)
        row2.addWidget(view_btn, 0)
        vlay.addLayout(row2)
        view._mfm_header = hdr
        self._reposition_column_header(view)
        hdr.show()
        hdr.raise_()

    def _toggle_view_mode(self, view):
        cur = getattr(view, "_mfm_view_mode", "list")
        self._set_column_view_mode(view, "thumb" if cur == "list" else "list")

    def _toggle_flatten(self, view, on, folder_path=None):
        """このカラムの平坦化トグル。ON時はそのカラムの選択フォルダ（無ければ
        そのカラム自身のフォルダ）以下を平坦表示する。平坦は «1カラムのみ» 排他。

        toggled シグナル経由の再入（排他OFFやリセットの setChecked）にも
        安全な構造にしてある。"""
        _mfm_log("toggle_flatten ENTER: on=%s folder=%r" % (on, folder_path))
        if on:
            # 排他制御: 他カラムの平坦ボタンがONなら OFF にする。
            # setChecked(False) が toggled 経由で OFF 処理を正しく走らせる
            prev = self._flatten_view
            if prev is not None and prev is not view:
                self._flatten_view = None
                pb = getattr(prev, "_mfm_flat_btn", None)
                try:
                    if pb is not None:
                        pb.setChecked(False)
                    else:
                        prev._mfm_flatten = False
                except Exception:
                    pass
            self._flatten_view = view
            view._mfm_flatten = True
            dirs = self._column_selected_dirs(view)
            if not dirs and folder_path and os.path.isdir(folder_path):
                dirs = [folder_path]   # 選択無し→このカラムのフォルダ自身
            _mfm_log("toggle_flatten ON: dirs=%r cb=%s"
                     % ([os.path.basename(d) for d in dirs],
                        callable(self._flat_cb)))
            if dirs:
                self._request_flat(dirs)
            else:
                _mfm_log("toggle_flatten: NO TARGET (folder_path=%r)"
                         % (folder_path,))
        else:
            was_active = (self._flatten_view is view)
            if was_active:
                self._flatten_view = None
            view._mfm_flatten = False
            _mfm_log("toggle_flatten OFF: was_active=%s" % was_active)
            # このカラムが平坦の発生源だった時だけ閉じる（排他OFFや
            # リセット経由の再入では、新しい平坦表示を巻き添えにしない）
            if was_active:
                self._request_flat([])

    def _set_column_view_mode(self, view, mode):
        """そのカラムをリスト／サムネイル表示に切り替える。"""
        try:
            if mode == "thumb":
                view._mfm_view_mode = "thumb"
                view.setViewMode(QListView.IconMode)
                view.setResizeMode(QListView.Adjust)
                view.setWrapping(True)
                view.setSpacing(6)
                # r85: セル寸法はスライダーの保存値から（既定 96px）
                self.set_column_item_size(view, self.column_item_size(view),
                                          save=False)
                # r83: 先読みはサムネ表示に «してから» 行う（リスト表示では走らせない）。
                # r84: 先読みは BrowserPanel 側の処理なのでコールバック経由で呼ぶ
                # （CappedColumnView には _prefetch_thumbs_async は無い）。
                if callable(self._thumb_prefetch_cb):
                    folder = self._path_for_index(view.rootIndex())
                    if folder:
                        self._thumb_prefetch_cb(folder)
            else:
                view._mfm_view_mode = "list"
                view.setViewMode(QListView.ListMode)
                view.setWrapping(False)
                view.setSpacing(0)
                view.setGridSize(QSize())
                view.setItemDelegate(StatusBadgeDelegate(self._path_of_index, view,
                                                is_dir_of_index=self._is_dir_index,
                                                is_expanded_index=self._is_expanded_index))
                # r85: リスト表示のアイコン寸法もスライダーの保存値から
                self.set_column_item_size(view, self.column_item_size(view),
                                          save=False)
            self._reposition_column_header(view)
            # r104: 開いたままのスライダーを新しいモードのレンジ／値へ更新する
            sp = getattr(self, "_size_popup", None)
            if sp is not None and sp.isVisible() and sp._view is view \
                    and sp._btn is not None:
                sp.show_for(view, sp._btn)
        except Exception as e:
            _mfm_warn("set_column_view_mode(%s) failed: %r" % (mode, e))

    def _column_selected_dirs(self, view):
        """そのカラムで選択中のフォルダのフルパス一覧（マージ対象）。"""
        out = []
        try:
            m = self.model()
            for idx in view.selectedIndexes():
                # そのカラムに «見えている» 行だけを数える。選択モデルは
                # モデル全体を張るため、他階層の不可視選択が混入し得る
                if idx.column() != 0 or idx.parent() != view.rootIndex():
                    continue
                src = idx
                sm = m
                while hasattr(sm, "mapToSource"):
                    src = sm.mapToSource(src)
                    sm = sm.sourceModel()
                fp = _safe_file_path(sm, src) if hasattr(sm, "filePath") else ""
                if fp and os.path.isdir(fp) and fp not in out:
                    out.append(fp)
        except Exception:
            pass
        return out

    def _deepest_selected_dirs(self):
        """表示中カラム全体から、選択されているフォルダをすべて返す。

        重要: QColumnView はカレントまでの祖先を各カラムで選択表示する
        （ナビゲーション連鎖）。この連鎖や選択スナップショット経由で
        «他の選択フォルダの祖先» が混入すると、平坦結果が祖先ツリー全体
        （数千ファイル）に化けて事実上フリーズするため、必ず除外する。"""
        out = []
        if self._selected_dir_paths:
            out = [p for p in sorted(self._selected_dir_paths) if os.path.isdir(p)]
        else:
            try:
                # パンくず（現在位置までの祖先チェーン）は «選択フォルダ» では
                # ないため除外する。含めると空白クリック解除後も平坦ビューが
                # 親フォルダで居座る
                cur_nc = ""
                try:
                    cur = self.currentIndex()
                    cp = self._path_of_index(cur) if cur.isValid() else ""
                    cur_nc = os.path.normcase(os.path.normpath(cp)) if cp else ""
                except Exception:
                    cur_nc = ""
                for view in self.findChildren(QListView):
                    if not hasattr(view, "selectedIndexes"):
                        continue
                    for idx in view.selectedIndexes():
                        if idx.column() != 0 or idx.parent() != view.rootIndex():
                            continue
                        fp = self._path_of_index(idx)
                        if not (fp and os.path.isdir(fp)):
                            continue
                        if cur_nc:
                            nf = os.path.normcase(os.path.normpath(fp))
                            if cur_nc == nf or cur_nc.startswith(nf + os.sep):
                                continue
                        if fp not in out:
                            out.append(fp)
            except Exception:
                pass
        return self._drop_ancestor_dirs(out)

    @staticmethod
    def _drop_ancestor_dirs(dirs):
        """他の選択フォルダの祖先（親・先祖）にあたるパスを除外して返す。
        例: {tmp, tmp/A, tmp/B} → {tmp/A, tmp/B}（最深のみ残す）。"""
        try:
            norm = {p: os.path.normcase(os.path.normpath(p)) for p in dirs}
            res = []
            for p in dirs:
                base = norm[p] + os.sep
                if any(o != p and norm[o].startswith(base) for o in dirs):
                    continue
                res.append(p)
            return res
        except Exception:
            return list(dirs)

    def _selection_snapshot(self, exclude_view=None, exclude_ancestors_of=None):
        """指定カラム以外の選択状態を保持する。

        exclude_ancestors_of: このパスの祖先（＝操作カラムまでのパンくず）は
        スナップに入れない。入れてしまうと、Ctrlトグルで最後の1件を解除した
        直後にパンくずが「選択フォルダ」として復活し、解除できない／
        勝手に平坦ビューが出る誤動作になる。"""
        snap = set()
        anc_nc = ""
        if exclude_ancestors_of:
            try:
                anc_nc = os.path.normcase(os.path.normpath(exclude_ancestors_of))
            except Exception:
                anc_nc = ""
        try:
            for view in self.findChildren(QListView):
                if view is exclude_view or not hasattr(view, "selectedIndexes"):
                    continue
                for idx in view.selectedIndexes():
                    # 不可視選択（他階層の残骸）はスナップに入れない。
                    # これが混入すると、Ctrlトグルで解除した項目が
                    # スナップ復元で選択に戻る（解除できない不具合の原因）
                    if (idx.isValid() and idx.column() == 0
                            and idx.parent() == view.rootIndex()):
                        fp = self._path_of_index(idx)
                        if not (fp and os.path.isdir(fp)):
                            continue
                        if anc_nc:
                            nf = os.path.normcase(os.path.normpath(fp))
                            if anc_nc == nf or anc_nc.startswith(nf + os.sep):
                                continue   # 操作カラムへのパンくず → 除外
                        snap.add(fp)
        except Exception:
            pass
        return snap

    def _restore_selection_snapshot(self, snap):
        """下階層操作で消えた上位カラムの複数選択を戻す。

        QColumnView はカレントまでの祖先を各カラムで選択表示するため、
        スナップショットにナビゲーション連鎖（祖先）が紛れ込む。統合時に
        «他の選択フォルダの祖先» を追跡から落とし、蓄積汚染を防ぐ。"""
        if snap:
            # 現在追跡中フォルダの«子孫»にあたるスナップは持ち込まない
            # （新しい選択系譜が勝つ。旧下位選択の残骸が復活すると、
            #   祖先除外で新選択自体が結果から落ちる＝動画の症状）
            cur = [os.path.normcase(os.path.normpath(d))
                   for d in self._selected_dir_paths]

            def _under_cur(p):
                np_ = os.path.normcase(os.path.normpath(p))
                return any(np_.startswith(c + os.sep) for c in cur)

            merged = set(self._selected_dir_paths)
            merged.update(p for p in snap
                          if os.path.isdir(p) and not _under_cur(p))
            self._selected_dir_paths = set(self._drop_ancestor_dirs(sorted(merged)))
        self._restore_tracked_selection()

    def _restore_tracked_selection(self):
        QISM = _QtCore.QItemSelectionModel
        paths = [p for p in self._selected_dir_paths if os.path.isdir(p)]
        indexes = [self._proxy_index_for_path(p) for p in paths]
        indexes = [i for i in indexes if i.isValid()]
        # パンくず（現在位置までの祖先チェーン）も全モデルへ焼き込む。
        # QColumnView はカラム再構築時に本体モデルの選択から複製するため、
        # 本体側に連鎖選択が無いと «一つ上の階層の選択が外れる»。
        try:
            cur = self.currentIndex()
            root = self.rootIndex()
            i = cur
            while i.isValid() and i != root:
                indexes.append(i)
                i = i.parent()
        except Exception:
            pass
        if not indexes:
            return
        targets = [v.selectionModel() for v in self.findChildren(QListView)]
        targets.append(self.selectionModel())  # 本体（再構築時の種）にも反映
        for sm in targets:
            try:
                if sm is None:
                    continue
                sel = _QtCore.QItemSelection()
                for idx in indexes:
                    sel.select(idx, idx)
                if not sel.isEmpty():
                    sm.select(sel, QISM.Select | QISM.Rows)
            except Exception:
                pass
        for view in self.findChildren(QListView):
            try:
                view.viewport().update()
            except Exception:
                pass

    def _restore_selection_snapshot_later(self, snap):
        if not snap:
            return
        QTimer.singleShot(0, lambda s=snap: self._restore_selection_snapshot(s))
        QTimer.singleShot(80, lambda s=snap: self._restore_selection_snapshot(s))

    def _proxy_index_for_path(self, path):
        try:
            m = self.model()
            sm = m.sourceModel() if hasattr(m, "sourceModel") else m
            src = sm.index(path) if hasattr(sm, "index") else QModelIndex()
            return m.mapFromSource(src) if hasattr(m, "mapFromSource") else src
        except Exception:
            return QModelIndex()

    @staticmethod
    def _norm_parent(path):
        try:
            return os.path.normcase(os.path.normpath(os.path.dirname(path)))
        except Exception:
            return ""

    def _sync_tracked_selection_for_view(self, view):
        """現在操作中のカラムだけ追跡状態を更新し、他カラムは触らない。"""
        dirs = self._column_selected_dirs(view)
        parents = {self._norm_parent(d) for d in dirs}
        if not parents:
            try:
                idx = view.currentIndex()
                fp = self._path_of_index(idx)
                parent = self._norm_parent(fp) if fp else ""
                parents = {parent} if parent else set()
            except Exception:
                parents = set()
        if parents:
            self._selected_dir_paths = {
                p for p in self._selected_dir_paths
                if self._norm_parent(p) not in parents
            }
        # 新しく選択したフォルダの«子孫»や«祖先»にあたる古い追跡は破棄する。
        # 例: 以前 Assets/CHR 等を選択 → 今回 Assets〜Tools を範囲選択した場合、
        # CHR の残骸が残ると祖先除外で Assets 自体が結果から落ち、平坦カラムが
        # 古い内容のまま更新されない（動画の症状）。新選択の系譜が常に勝つ。
        if dirs:
            norm_new = [os.path.normcase(os.path.normpath(d)) for d in dirs]

            def _is_desc(p):
                np_ = os.path.normcase(os.path.normpath(p))
                return any(np_.startswith(nd + os.sep) for nd in norm_new)

            def _is_anc(p):
                np_ = os.path.normcase(os.path.normpath(p))
                return any(nd.startswith(np_ + os.sep) for nd in norm_new)

            removed_desc = {p for p in self._selected_dir_paths if _is_desc(p)}
            removed_anc = {p for p in self._selected_dir_paths
                           if p not in removed_desc and _is_anc(p)}
            self._selected_dir_paths -= (removed_desc | removed_anc)
            # 子孫の残骸はハイライトも外す。祖先（パンくず）は追跡からのみ
            # 外し、見た目の選択は温存する（「一つ上の階層の選択が外れる」
            # 不具合の修正）
            if removed_desc:
                self._deselect_paths(removed_desc)
        self._selected_dir_paths.update(dirs)

    @staticmethod
    def _scrollbar_extent(view) -> int:
        """そのビューが縦スクロールバーに割く幅（表示していなくても同じ値）。

        r105: 掴む位置をスクロールバーの左に固定するために使う。可視状態で
        計算すると «スクロールバーが出た瞬間にハンドルがずれる» ので、
        常に確保幅で計算する。"""
        try:
            sb = view.verticalScrollBar()
            w = sb.sizeHint().width() if sb is not None else 0
            if w <= 0:
                w = sb.width() if sb is not None else 0
        except Exception:
            w = 0
        if w <= 0:
            try:
                w = QApplication.style().pixelMetric(QStyle.PM_ScrollBarExtent)
            except Exception:
                w = 16
        return max(0, int(w))

    def _reposition_column_header(self, view):
        hdr = getattr(view, "_mfm_header", None)
        if hdr is None:
            return
        # ビューポート基準で配置する。view.width() を使うと縦スクロールバーや枠の
        # 下に ⚙ が潜って極小化・クリップされる（カラムに収まらない原因）。
        try:
            vp = view.viewport()
            x = vp.x()
            w = vp.width()
        except Exception:
            x, w = 0, view.width()
        hdr.setGeometry(x, 0, max(40, w), self._COL_HEADER_H)
        hdr.raise_()
        # 右端のリサイズハンドル（ヘッダの下からカラム下端まで）
        handle = getattr(view, "_mfm_resize_handle", None)
        if handle is not None:
            # r105: **必ず縦スクロールバーより左** に置く。
            # 従来はカラム右辺を «またぐ» 帯だったため、右端のカラムでは
            # 掴む場所が縦スクロールバーと重なり、掴み損ねてスクロールバーを
            # 動かす／誤って幅を変えて戻せない、という事故になっていた
            # （ユーザー報告 2026-09-30）。
            # 幅はスクロールバーの «表示有無によらず» 確保している分で計算し、
            # 出たり消えたりしても掴む位置が動かないようにする。
            hw = _ColumnResizeHandle.WIDTH
            try:
                sb_w = self._scrollbar_extent(view)
                x = view.x() + view.width() - sb_w - hw
                # カラムが極端に細い時でも最低限カラム内に収める
                x = max(view.x(), x)
                handle.setGeometry(x,
                                   view.y() + self._COL_HEADER_H, hw,
                                   max(1, view.height() - self._COL_HEADER_H))
                handle.setVisible(view.isVisible())
                handle.raise_()
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # カラム幅（ユーザー変更・保存・自動調整）
    # ------------------------------------------------------------------

    def _integrations(self):
        """IntegrationManager（遅延取得。状態更新時に全カラムを再描画）。"""
        mgr = getattr(self, "_integ_mgr", None)
        if mgr is None:
            from core.integrations import get_manager
            mgr = get_manager()
            self._integ_mgr = mgr
            try:
                mgr.status_updated.connect(self._on_integration_status)
            except Exception:
                pass
        return mgr

    def _on_integration_status(self, _directory: str):
        try:
            for v in self.findChildren(QListView):
                v.viewport().update()
        except RuntimeError:
            pass

    def set_settings(self, sm):
        """幅の保存先（SettingsManager）を登録し、保存済みの幅を適用する。"""
        self._sm_widths = sm
        try:
            saved = sm.get("column_widths", None)
            if isinstance(saved, list) and saved:
                widths = [max(_ColumnResizeHandle.MIN_W,
                              min(_ColumnResizeHandle.MAX_W, int(w)))
                          for w in saved]
            else:
                widths = [self.DEFAULT_COLUMN_W] * 12
            self.setColumnWidths(widths)
        except Exception:
            pass

    DEFAULT_COLUMN_W = 300   # 既定幅（従来256。名前の見切れが多いため拡大）

    def _column_views_sorted(self):
        cols = [v for v in self.findChildren(QListView)
                if getattr(v, "_mfm_header", None) is not None and v.isVisible()]
        cols.sort(key=lambda v: v.x())
        return cols

    def set_column_width_for_view(self, view, width: int):
        """指定カラム（表示位置基準）の幅を変更する。"""
        try:
            cols = self._column_views_sorted()
            if view not in cols:
                return
            i = cols.index(view)
            widths = list(self.columnWidths())
            fill = widths[-1] if widths else self.DEFAULT_COLUMN_W
            while len(widths) <= i:
                widths.append(fill)
            widths[i] = int(width)
            self.setColumnWidths(widths)
            # setColumnWidths はカラム自体の幅は変えるが右側カラムの再配置を
            # 行わない（縮めると隙間、広げると重なる）。再配置は «Qt自身の
            # doLayout»（resizeEvent 経由）に任せる。自前で setGeometry すると
            # 非表示カラムやスクロールオフセットと食い違い、カラム群が右へ
            # ずれて左に空白ができる致命的状態になった（2026-09 実機）。
            self._relayout_columns()
            for v in cols:
                self._reposition_column_header(v)
        except Exception as e:
            _mfm_log("set_column_width error: %r" % (e,))

    def _relayout_columns(self):
        """Qt 内部の doLayout（カラムを左詰めで連続配置）を resizeEvent 経由で
        起動する。QColumnView は resizeEvent で doLayout + スクロール範囲更新を
        行うため、同サイズの ResizeEvent を送るのが最も安全な再配置手段。"""
        try:
            from core.compat import QtGui as _QtGui
            ev = _QtGui.QResizeEvent(self.size(), self.size())
            # QColumnView::resizeEvent = doLayout() + updateScrollbars()
            self.resizeEvent(ev)
            # Qt の updateScrollbars は「右に空白ができる過スクロール状態」を
            # そのまま温存する（visibleLength=min(総幅+offset, 表示幅) のため、
            # 縮めた直後は最大値が減らない）。右端に空白があれば左へ詰め、
            # 総幅が表示幅以下なら先頭へ戻し、範囲を再計算させる（r54）。
            if self._normalize_hscroll():
                self.resizeEvent(_QtGui.QResizeEvent(self.size(), self.size()))
        except Exception as e:
            _mfm_log("relayout error: %r" % (e,))

    def _normalize_hscroll(self) -> bool:
        """右側の空白（過スクロール）を解消する。値を変えたら True。"""
        try:
            cols = self._column_views_sorted()
            hbar = self.horizontalScrollBar()
            if not cols or hbar is None:
                return False
            vw = self.viewport().width()
            total = sum(v.width() for v in cols)
            right = cols[-1].x() + cols[-1].width()
            if total <= vw:
                new_val = 0
            elif right < vw:
                new_val = max(0, hbar.value() - (vw - right))
            else:
                return False
            if new_val == hbar.value():
                return False
            hbar.setValue(new_val)
            return True
        except Exception:
            return False

    def _check_column_layout_health(self):
        """レイアウト自己修復（150ms毎）。先頭カラムの左に空白がある＝カラム群が
        右へずれた状態は、スクロールしても見えない階層が生じる致命的な症状。
        原因を問わず、検知したら水平スクロールを先頭に戻し Qt の doLayout で
        左詰めに再配置する。"""
        try:
            cols = [v for v in self.findChildren(QListView)
                    if getattr(v, "_mfm_header", None) is not None
                    and v.isVisible()]
            if not cols:
                return
            first_x = min(v.x() for v in cols)
            hbar = self.horizontalScrollBar()
            # 先頭カラムは常に x<=0（スクロール分だけ負）。正の空白は異常。
            if first_x > 2:
                _mfm_log("layout repair: first column x=%d hbar=%s/%s"
                         % (first_x, hbar.value() if hbar else "-",
                            hbar.maximum() if hbar else "-"))
                _mfm_slow_note("LAYOUT-REPAIR first_x=%d" % first_x)
                if hbar is not None:
                    hbar.setValue(hbar.minimum())
                # 先頭カラムを原点へ寄せてから Qt に連続配置させる
                shift = first_x
                for v in sorted(cols, key=lambda v: v.x()):
                    v.move(v.x() - shift, v.y())
                self._relayout_columns()
                for v in cols:
                    self._reposition_column_header(v)
        except RuntimeError:
            pass
        except Exception:
            pass

    def persist_column_widths(self):
        sm = getattr(self, "_sm_widths", None)
        if sm is None:
            return
        try:
            sm.set("column_widths", [int(w) for w in self.columnWidths()])
        except Exception:
            pass

    def fit_width_for_view(self, view) -> int:
        """カラム内の項目名が収まる幅を計算する（最大400件をサンプル）。"""
        try:
            m = view.model()
            root = view.rootIndex()
            fm = view.fontMetrics()
            n = min(m.rowCount(root), 400)
            longest = 0
            for r in range(n):
                idx = m.index(r, 0, root)
                s = idx.data() or ""
                longest = max(longest, fm.horizontalAdvance(str(s)))
            # アイコン + 余白 + 展開矢印 + スクロールバー
            return max(_ColumnResizeHandle.MIN_W,
                       min(_ColumnResizeHandle.MAX_W, longest + 24 + 16 + 20 + 14))
        except Exception:
            return 0

    def _path_for_index(self, index):
        """そのカラムが表示しているフォルダのフルパスを返す（プロキシ→ソース解決）。"""
        m = self.model()
        if m is None or not index.isValid():
            return ""
        idx = index
        src = m
        while hasattr(src, "mapToSource"):
            idx = src.mapToSource(idx)
            src = src.sourceModel()
        return _safe_file_path(src, idx) if hasattr(src, "filePath") else ""

    def eventFilter(self, obj, event):
        et = event.type()
        if et in (_QtCore.QEvent.Resize, _QtCore.QEvent.Show,
                  _QtCore.QEvent.Move, _QtCore.QEvent.Hide):
            view = obj if getattr(obj, "_mfm_header", None) is not None \
                else obj.parent()
            if view is not None and getattr(view, "_mfm_header", None) is not None:
                self._reposition_column_header(view)
        elif et == _QtCore.QEvent.Wheel and (event.modifiers() & Qt.ShiftModifier):
            # Shift+ホイールでブラウジングエリアを横スクロール（カラム間移動）
            hbar = self.horizontalScrollBar()
            if hbar is not None:
                d = event.angleDelta().y() or event.angleDelta().x()
                hbar.setValue(hbar.value() - d)
                return True
        elif et == _QtCore.QEvent.MouseButtonDblClick:
            # ── ダブルクリック（r66） ──────────────────────────────────
            # 本パネルは1回目のプレスを自前で消費するため、QAbstractItemView の
            # pressedIndex が更新されない。Qt の mouseDoubleClickEvent は
            # 「pressedIndex == index」の時だけ doubleClicked/activated を出し、
            # それ以外は «通常のプレス» として処理する。結果、1回目のダブル
            # クリックは pressedIndex を埋めるだけで反応せず、2回目で初めて
            # 開く（実機報告: Premiere ファイルが2回目で開く）。ここで自前に
            # activated を発火し、Qt 側の処理は消費する。
            view = obj.parent()
            if view is not None and hasattr(view, "indexAt") and \
                    event.button() == Qt.LeftButton:
                try:
                    pos = event.position().toPoint()
                except AttributeError:
                    pos = event.pos()
                idx = view.indexAt(pos)
                self._cancel_reclick_rename()      # ダブルクリック＝名前変更ではない
                if idx.isValid():
                    self._pending_multi_drag = None
                    self._swallow_release = True
                    _mfm_uilog("double-click: %r" % (idx.data(),))
                    try:
                        self.activated.emit(idx)
                    except Exception as e:
                        _mfm_log("dblclick emit error: %r" % (e,))
                    return True
        elif et in (_QtCore.QEvent.DragEnter, _QtCore.QEvent.DragMove,
                    _QtCore.QEvent.Drop):
            view = obj.parent()
            if view is not None and hasattr(view, "indexAt"):
                if self._handle_view_drag(view, event, et):
                    return True
        elif et == _QtCore.QEvent.MouseButtonPress:
            view = obj.parent()
            if view is not None and hasattr(view, "indexAt"):
                try:
                    pos = event.position().toPoint()
                except AttributeError:
                    pos = event.pos()
                idx = view.indexAt(pos)
                mods = event.modifiers()
                if idx.isValid():
                    # r83: 以降は自己修復より選択の維持を優先する
                    self._mfm_user_selected = True
                self._cancel_reclick_rename()      # 新しいプレスで «ゆっくり2回» は無効
                # QColumnView は «current の親カラム以外» を NoFocus にするため、
                # 末尾カラム（ファイルのある列）をクリックしてもフォーカスが移らず、
                # F2 / Ctrl+Z 等の WidgetWithChildrenShortcut が効かない（実機で
                # 「F2 が実行されない」原因、r62）。プレス時に明示的にフォーカスする
                # （MouseFocusReason なら focusInEvent は current を動かさない）。
                try:
                    if not view.hasFocus():
                        view.setFocus(Qt.MouseFocusReason)
                except Exception:
                    pass
                if _MFM_DEBUG:
                    # r109: 文字列組み立て自体がプレスごとのコストなので、
                    # デバッグ時以外は «式の評価ごと» 行わない
                    _anchor_dbg = getattr(view, "_mfm_sel_anchor", None)
                    _mfm_log(
                        "MousePress: valid=%s row=%s ctrl=%s shift=%s "
                        "anchor_valid=%s view=%s name=%r"
                        % (idx.isValid(), (idx.row() if idx.isValid() else -1),
                           bool(mods & Qt.ControlModifier),
                           bool(mods & Qt.ShiftModifier),
                           (_anchor_dbg is not None and _anchor_dbg.isValid()),
                           id(view), (idx.data() if idx.isValid() else None)))
                # ── 右クリック（r55）: 選択を壊さない（Explorer準拠） ──────────
                # 従来はボタンを見ずに左クリックと同じ経路（選択済み項目のプレス
                # 消費→リリースで単一選択化）に入り、メニューが出る前に複数選択が
                # 解けていた（Windows はリリース時に ContextMenu イベントが来る）。
                # 選択済み項目 → 何もしない（選択全体がメニュー対象）
                # 未選択項目   → その項目だけを選択（current は動かさない＝ナビしない）
                # 空白         → そのカラムの選択解除（従来通り）
                # プレス/リリースとも消費するが、ContextMenu イベントは
                # QWidgetWindow がマウスイベントの accept 状態に関係なく送るため
                # メニューは従来通り表示される。
                if event.button() == Qt.RightButton:
                    self._pending_multi_drag = None
                    self._swallow_release = True
                    if idx.isValid():
                        sm = view.selectionModel()
                        if sm is None or not sm.isSelected(idx):
                            view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
                            self._clear_multi_state()
                            self._prune_selection_to_single(idx)
                    else:
                        self._clear_column_selection(view)
                    return True
                # Ctrl/Shift+クリックは「ナビゲーションせずに複数選択」を自前で処理する。
                # QColumnView はクリックを単一ナビに横取りするため、ここで捌かないと
                # Shift/Ctrl の複数選択が効かない（マージ用の選択ができない）。
                if idx.isValid() and (mods & (Qt.ControlModifier | Qt.ShiftModifier)):
                    snap = self._selection_snapshot(
                        exclude_view=view,
                        exclude_ancestors_of=self._path_for_index(view.rootIndex()))
                    self._multi_select(view, idx, mods, preserve_snapshot=snap)
                    # 対応するリリースも必ず消費する。素通しすると
                    # ネイティブの clicked が発火してナビゲーション＋
                    # 再選択が走り、トグル解除が打ち消される
                    self._swallow_release = True
                    return True
                # 平坦トグルON: フォルダの単一クリックは «ナビせず» 平坦カラムに出す
                if idx.isValid() and getattr(view, "_mfm_flatten", False):
                    fp = self._path_of_index(idx)
                    if fp and os.path.isdir(fp):
                        QISM = _QtCore.QItemSelectionModel
                        sm = view.selectionModel()
                        if sm is not None:
                            sm.select(idx, QISM.ClearAndSelect | QISM.Rows)
                            sm.setCurrentIndex(idx, QISM.NoUpdate)
                        self._request_flat([fp])
                        return True
                # 選択済みの項目を «修飾キーなし» で押した → ドラッグ候補として
                # プレスを消費し、リリース＝クリック再現／Move閾値＝自前ドラッグ。
                # （QColumnViewは押下で単一ナビ＝選択クリアするため、自前でドラッグを
                #  起動しないと複数選択のD&Dができない。単一選択も同経路に統一：
                #  r21の「プレス素通し＋Moveだけ消費」方式はリリースがドラッグ
                #  ループに食われてビューが押下状態のまま残り、以後のクリックが
                #  効かない＝フリーズに見える不具合の原因だった）
                if idx.isValid():
                    sm = view.selectionModel()
                    if sm is not None and sm.isSelected(idx):
                        # r92: QColumnView は «全カラムで同じ選択モデル» を使うため、
                        # selectedIndexes() には左側カラムのパンくず（今いる階層の
                        # 祖先フォルダ）まで含まれる。そのままドラッグすると
                        # 「1つ選んだのに他のフォルダやファイルまで D&D される」
                        # （ユーザー報告 2026-09-23）。押した項目と «同じカラム»
                        # （＝同じ親）の選択だけを対象にする。
                        sel = self._same_column_selection(sm, idx)
                        _mfm_uilog("press(selected): %r → drag候補 %d件 [%s]"
                                   % (idx.data(), len(sel),
                                      ", ".join(str(i.data()) for i in sel)))
                        if len(sel) >= 1:
                            if not self._is_dir_index(idx):
                                self.note_file_click(idx.data())
                            # ここで即 drag.exec() すると、ただのクリックでもドラッグ扱いになり
                            # 操作が重い。Press では標準の単一選択化だけ止め、Move 閾値を
                            # 超えた時だけ MouseMove 側でD&Dを開始する。
                            view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
                            self._pending_multi_drag = {
                                "view": view,
                                "pos": pos,
                                "press_idx": _QtCore.QPersistentModelIndex(idx),
                                "indexes": [_QtCore.QPersistentModelIndex(i) for i in sel],
                                # r67: 単一選択済みの項目をもう一度クリック
                                # → «ゆっくり2回クリック» で名前変更（Explorer 準拠）
                                "reclick_single": len(sel) == 1 and sel[0] == idx,
                            }
                            return True
                # 何もない所クリック → そのカラムの選択を解除（標準挙動）
                if not idx.isValid():
                    self._clear_column_selection(view)
                    self._swallow_release = True
                    return True
                # ファイルの通常クリック: current は «動かさない»（親フォルダに
                # 留める）。QColumnView に current を渡すとプレビュー列が生成され
                # カラム全体が左へスクロールして使いづらい（指摘あり）。
                # 選択だけ全モデルで単一化し、リリースでクリック相当を再現する
                # （プレス消費＋_pending_multi_drag 経路＝ドラッグ開始も可能）。
                if idx.isValid() and not self._is_dir_index(idx):
                    self.note_file_click(idx.data())
                    _mfm_uilog("press(file): %r → drag候補 1件" % (idx.data(),))
                    view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
                    self._clear_multi_state()
                    self._prune_selection_to_single(idx)
                    self._pending_multi_drag = {
                        "view": view,
                        "pos": pos,
                        "press_idx": _QtCore.QPersistentModelIndex(idx),
                        "indexes": [_QtCore.QPersistentModelIndex(idx)],
                        "file_click": True,
                    }
                    return True
                # 通常クリック＝標準挙動: 複数選択は解除され単一選択になる。
                # r92: 未選択フォルダのドラッグもネイティブに任せない。
                # ネイティブの startDrag は «全カラム共有の選択モデル» を見るため、
                # パンくず（祖先フォルダ）まで一緒にドラッグされてしまう。
                # ファイルと同じく pending 経路に乗せ、_start_multi_drag で
                # «押した項目と同じカラムの選択だけ» をドラッグする。
                _mfm_uilog("press(unselected): %r → drag候補 1件" % (idx.data(),))
                view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
                self._clear_multi_state()
                self._prune_selection_to_single(idx)
                self._pending_multi_drag = {
                    "view": view,
                    "pos": pos,
                    "press_idx": _QtCore.QPersistentModelIndex(idx),
                    "indexes": [_QtCore.QPersistentModelIndex(idx)],
                }
                return True
        elif et == _QtCore.QEvent.MouseMove:
            pending = self._pending_multi_drag
            if pending:
                try:
                    view = pending.get("view")
                    if view is not None:
                        try:
                            pos = event.position().toPoint()
                        except AttributeError:
                            pos = event.pos()
                        start = pending.get("pos")
                        if start is not None:
                            delta = pos - start
                            if delta.manhattanLength() >= QApplication.startDragDistance():
                                indexes = [i for i in pending.get("indexes", []) if i.isValid()]
                                self._pending_multi_drag = None
                                _mfm_uilog("drag-threshold: 自前ドラッグ開始 %d件"
                                           % len(indexes))
                                self._start_multi_drag(view, indexes)
                                return True
                except Exception:
                    self._pending_multi_drag = None
                # r94: 閾値未満の MouseMove も «必ず» 消費する。
                # QAbstractItemView::mouseMoveEvent は «左ボタンが押されたまま
                # 動いた» だけで DragSelectingState に入り、自分が受け取って
                # いない押下位置（= 既定値 0,0 ＝ビュー左上）を矩形選択の起点に
                # するため、素通しするとカーソルまでの範囲＝«対象より上の全て»
                # が選択されてしまう（ユーザー報告 2026-09-23）。
                return True
            if event.buttons() & Qt.LeftButton:
                # r96: 押下を消費した後（右クリック・Ctrl/Shift選択・空白
                # クリック）も、自前ドラッグの最中／直後も、Qt へ渡すと
                # «押下位置 0,0» を起点にした矩形選択が走る（対象より上の
                # 全項目がハイライトされる）。このビューの選択は全て自前で
                # 行っているので、左ボタン中の MouseMove は常に消費する。
                return True
        elif et == _QtCore.QEvent.MouseButtonRelease:
            if getattr(self, "_swallow_release", False):
                self._swallow_release = False
                return True
            if self._pending_multi_drag:
                pending = self._pending_multi_drag
                self._pending_multi_drag = None
                # ドラッグに至らなかった＝ただのシングルクリック。標準挙動どおり
                # 複数選択を解除して単一選択に確定し、通常のナビゲーションを行う。
                # （旧実装はここで握り潰しており「クリックしても解除されない」
                #  壊れ方の原因だった）
                try:
                    view = pending.get("view")
                    p_idx = pending.get("press_idx")
                    _mfm_log("release-click: view=%s idx_valid=%s"
                             % (view is not None,
                                (p_idx is not None and p_idx.isValid())))
                    if view is not None and p_idx is not None and p_idx.isValid():
                        idx = view.model().index(p_idx.row(), 0, p_idx.parent())
                        if idx.isValid():
                            QISM = _QtCore.QItemSelectionModel
                            self._clear_multi_state()
                            # 全モデル（各カラム＋本体）で単一選択へ確定
                            self._prune_selection_to_single(idx)
                            # ファイルは current を «一切» 動かさない（プレビュー列の
                            # 生成＝カラム全体のスクロールを防ぐ）。複数選択後は
                            # カラムの選択モデルが本体と共有されている場合があり、
                            # カラム側の setCurrentIndex でも本体 current が動く。
                            # フォルダは本体 current の変更が子カラム展開を駆動する。
                            is_file = pending.get("file_click") or \
                                not self._is_dir_index(idx)
                            sm = view.selectionModel()
                            if sm is not None and not is_file:
                                sm.setCurrentIndex(idx, QISM.NoUpdate)
                            top = self.selectionModel()
                            if top is not None and not is_file:
                                top.setCurrentIndex(idx, QISM.NoUpdate)
                            _mfm_log("release-click: single-select %r file=%s"
                                     % (idx.data(), is_file))
                            self.clicked.emit(idx)
                            # r67: 既に単一選択されていた項目の再クリック →
                            # ダブルクリック間隔を過ぎても次の操作が無ければ名前変更
                            # （ダブルクリック／次のプレスで取り消す）
                            if pending.get("reclick_single"):
                                self._schedule_reclick_rename(self._path_of_index(idx))
                except Exception as e:
                    _mfm_log("release-click error: %r" % (e,))
                return True
        return super().eventFilter(obj, event)

    # ── r87: カラム間の D&D（移動／コピー） ───────────────────────────
    def _drop_dir_for(self, view, pos):
        """ドロップ先フォルダ。項目の上ならそのフォルダ、空白ならその
        カラムが表示しているフォルダ。ファイルの上に落ちた場合も «そのカラムの
        フォルダ» として扱う（Explorer と同じ）。"""
        try:
            idx = view.indexAt(pos)
            if idx.isValid():
                p = self._path_of_index(idx)
                if p and os.path.isdir(p) and not self._is_link_path(p):
                    return p
            return self._path_for_index(view.rootIndex())
        except Exception:
            return ""

    @staticmethod
    def _is_link_path(path: str) -> bool:
        try:
            return os.path.islink(path)
        except OSError:
            return False

    @staticmethod
    def _urls_to_paths(mime):
        out = []
        try:
            if not mime.hasUrls():
                return out
            for u in mime.urls():
                p = u.toLocalFile()
                if p:
                    out.append(os.path.normpath(p))
        except Exception:
            pass
        return out

    def _handle_view_drag(self, view, event, et):
        """カラムのビューポートで起きた D&D を自前で処理する（r87）。

        Qt 標準（QFileSystemModel の dropMimeData）に任せると、
        プロキシ越しのカラム表示では «ドロップしても何も起きない» ことがあり、
        さらに «同名がある時に選ばせる» ことができない（黙って連番になる）。
        そのため受け口をここに一本化する。戻り値 True でイベントを消費。"""
        try:
            mime = event.mimeData()
            paths = self._urls_to_paths(mime)
            if not paths:
                return False
            if et == _QtCore.QEvent.Drop:
                try:
                    pos = event.position().toPoint()
                except AttributeError:
                    pos = event.pos()
                dest = self._drop_dir_for(view, pos)
                # Ctrl 押下＝コピー、それ以外は移動（Explorer 準拠）
                move = not bool(event.keyboardModifiers() & Qt.ControlModifier)
                cb = self._file_drop_cb
                _mfm_log("drop: n=%d dest=%r move=%s cb=%s"
                         % (len(paths), dest, move, callable(cb)))
                if not dest or not callable(cb):
                    return False
                event.setDropAction(Qt.MoveAction if move else Qt.CopyAction)
                event.accept()
                # ドロップ処理中にビューを組み替えるとクラッシュするため、
                # イベントを抜けてから実行する
                QTimer.singleShot(0, lambda ps=list(paths), d=dest, mv=move:
                                  cb(ps, d, mv))
                return True
            # DragEnter / DragMove: ファイルなら受け入れる
            event.setDropAction(Qt.MoveAction if not (
                event.keyboardModifiers() & Qt.ControlModifier) else Qt.CopyAction)
            event.accept()
            return True
        except Exception as e:
            _mfm_log("drag/drop error: %r" % (e,))
            return False

    @staticmethod
    def _same_column_selection(sm, idx):
        """押した項目と同じカラム（同じ親）の選択だけを返す（r92）。
        押した項目は必ず含める。"""
        parent = idx.parent()
        sel = []
        try:
            for i in sm.selectedIndexes():
                if i.column() != 0 or i.parent() != parent:
                    continue
                sel.append(i)
        except Exception:
            sel = []
        if not any(i == idx for i in sel):
            sel.append(idx)
        return sel

    def _start_multi_drag(self, view, indexes):
        """複数選択した項目を一括ドラッグする。QFileSystemModel の mimeData を
        使い、Explorer 互換のファイルD&Dにする。"""
        self._mfm_in_drag = True
        try:
            m = view.model()
            fsm = m
            while hasattr(fsm, "sourceModel"):
                fsm = fsm.sourceModel()
            src_idxs = []
            for idx in indexes:
                s = idx
                mm = m
                while hasattr(mm, "mapToSource"):
                    s = mm.mapToSource(s)
                    mm = mm.sourceModel()
                src_idxs.append(s)
            paths = [_safe_file_path(fsm, s) for s in src_idxs
                     if hasattr(fsm, "filePath")]
            paths = [p for p in paths if p]
            # r92: 最終防衛。重複と «他の対象の祖先フォルダ»（パンくず）を落とす。
            # ここを通る全経路（カラム／平坦ビュー）で «選んだものだけ» を保証する。
            seen, uniq = set(), []
            for p in paths:
                k = os.path.normcase(os.path.abspath(p))
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(p)
            paths = self._drop_ancestor_dirs(uniq)
            _mfm_uilog("drag-start: %d件 [%s]"
                       % (len(paths), ", ".join(os.path.basename(x) for x in paths)))
            if not paths:
                return
            # r90: DCC への落下は Manager が引き受ける mime（OLE 同期処理で固まらない）
            # r96: ここで例外が出ると «ドラッグが全く始まらない» のに黙って
            # 終わっていた（実機で drag-exec前 が出ないまま終了）。段階ごとに
            # ログを残し、失敗しても素の URL mime で必ずドラッグを開始する。
            try:
                mime = self.make_drag_mime(paths)
            except Exception as e:
                _mfm_warn("make_drag_mime 失敗（素の mime で続行）: %r" % (e,))
                mime = None
            if mime is None:
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(x) for x in paths])
            _mfm_uilog("drag-mime: %s" % type(mime).__name__)
            drag = QDrag(view)
            drag.setMimeData(mime)
            # r94: ドラッグが «始まったかどうか分からない» との指摘。
            # 掴んでいる対象が見えるゴースト画像をカーソルに付ける。
            try:
                pm = self._drag_pixmap(view, indexes, paths)
                if pm is not None and not pm.isNull():
                    drag.setPixmap(pm)
                    drag.setHotSpot(QPoint(12, pm.height() // 2))
            except Exception:
                pass
            # r96: PySide2 の QDrag は exec_() しか持たない版がある。
            # exec を直接呼ぶと AttributeError でドラッグが «無かったこと» になる。
            _exec = getattr(drag, "exec", None) or getattr(drag, "exec_", None)
            mfm_blocking_begin("ドラッグ中（OLE DoDragDrop）")
            try:
                _exec(Qt.CopyAction | Qt.MoveAction, Qt.MoveAction)
            finally:
                mfm_blocking_end()
            # ドラッグ終了後: ドロップ先がMayaのウィンドウなら通知する
            self._notify_drag_finished(paths, mime)
        except Exception as e:
            # r96: ここを握り潰していたため «ドラッグが始まらない» 原因が
            # ログに残らなかった（実機で drag-exec前 が出ないまま終了）。
            import traceback as _tb
            _mfm_warn("drag-start FAILED: %r\n%s" % (e, _tb.format_exc()))
        finally:
            self._mfm_in_drag = False

    def _drag_pixmap(self, view, indexes, paths):
        """ドラッグ中のゴースト画像（r94）。先頭項目のアイコン＋名前、
        2件以上なら «+N» を添える。掴んでいる対象を視覚的に示すため。"""
        from core.theme_engine import qss_vars
        from core.compat import QPen
        try:
            tv = qss_vars()
        except Exception:
            tv = {}
        pal = view.palette()
        # 色はトークン（テーマ）から取り、取れない時だけパレットへ退避する
        # （ベタ書きするとテーマ切替に追従しない）
        def _c(role, fallback):
            v = tv.get(role)
            return QColor(v) if v else QColor(fallback)
        fg = _c("on_surface", pal.windowText().color())
        bg = _c("surface_container_high", pal.window().color())
        bd = _c("primary", pal.highlight().color())
        name = os.path.basename(paths[0]) if paths else ""
        extra = ("  +%d" % (len(paths) - 1)) if len(paths) > 1 else ""
        icon = None
        try:
            for i in indexes:
                if i.isValid():
                    icon = i.data(Qt.DecorationRole)
                    break
        except Exception:
            icon = None
        font = view.font()
        fm = QFontMetrics(font)
        text = name + extra
        tw = fm.horizontalAdvance(text) if hasattr(fm, "horizontalAdvance") else fm.width(text)
        h = max(24, fm.height() + 8)
        w = min(320, 12 + 16 + 6 + tw + 12)
        try:
            dpr = view.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        pt = QPainter(pm)
        pt.setRenderHint(QPainter.Antialiasing, True)
        bg.setAlpha(235)
        pt.setPen(QPen(bd, 1))
        pt.setBrush(bg)
        pt.drawRoundedRect(QRect(0, 0, int(w) - 1, int(h) - 1), 6, 6)
        x = 8
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(pt, QRect(x, (h - 16) // 2, 16, 16))
        x += 16 + 6
        pt.setPen(fg)
        pt.setFont(font)
        pt.drawText(QRect(x, 0, int(w) - x - 8, h),
                    Qt.AlignVCenter | Qt.AlignLeft, text)
        pt.end()
        return pm

    def _notify_drag_finished(self, paths, mime=None):
        """D&D終了時、カーソル直下がMaya（別プロセス）ならコールバックへ通知。
        自アプリ内へのドロップ（ファイル移動等）は対象外。

        r90: «Manager が引き受けた» 落下だけを実行する。DCC に実ファイルを渡して
        ネイティブに処理させた場合は、Manager からは送らない（二重実行防止）。"""
        try:
            cb = self._maya_drop_cb
            if not paths or not callable(cb):
                return
            if QApplication.widgetAt(QCursor.pos()) is not None:
                return   # 自アプリ内へのドロップ
            app = _cursor_over_dcc_window()
            if not app:
                return
            if mime is not None:
                handled = list(getattr(mime, "handled", []) or [])
                if not handled and not getattr(mime, "served_real_to_dcc", False):
                    # DCC がデータを取りに来なかった場合もここで判定する
                    decide = getattr(mime, "_mfm_decide", None)
                    if callable(decide):
                        handled = list(decide()[1] or [])
                if not handled:
                    _mfm_log("dcc-drop: %s は Manager の対象外（DCC に任せる）" % app)
                    return
                paths = handled          # r91: Manager が引き受けた分だけ実行
            _mfm_log("dcc-drop detected: app=%s %d paths" % (app, len(paths)))
            cb(list(paths), app)
        except Exception as e:
            _mfm_log("dcc-drop notify error: %r" % (e,))

    def _path_of_index(self, idx):
        """カラムビューのプロキシindex → ソースの実パス。"""
        try:
            m = self.model()
            src = idx
            sm = m
            while hasattr(sm, "mapToSource"):
                src = sm.mapToSource(src)
                sm = sm.sourceModel()
            return _safe_file_path(sm, src) if hasattr(sm, "filePath") else ""
        except Exception:
            return ""

    def _multi_select(self, view, idx, mods, preserve_snapshot=None):
        """Ctrl/Shift+クリックで、ナビゲーションせず列内の複数選択を行う。"""
        sm = view.selectionModel()
        if sm is None:
            return
        QISM = _QtCore.QItemSelectionModel
        model = view.model()
        if mods & Qt.ShiftModifier:
            anchor = getattr(view, "_mfm_sel_anchor", None)
            if anchor is not None and anchor.isValid():
                a = model.index(anchor.row(), 0, anchor.parent())
                _src = "anchor"
            else:
                a = sm.currentIndex()
                _src = "currentIndex(fallback)"
            if not a.isValid():
                a = idx
                _src += "+idx"
            parent = idx.parent()
            r1, r2 = sorted([a.row(), idx.row()])
            top = model.index(r1, 0, parent)
            bot = model.index(r2, 0, parent)
            _same_parent = (a.parent() == parent)
            _mfm_log("Shift-select: a_src=%s a_row=%s idx_row=%s range=[%d..%d] "
                     "same_parent=%s anchor_par_valid=%s idx_par_valid=%s"
                     % (_src, a.row(), idx.row(), r1, r2, _same_parent,
                        a.parent().isValid(), parent.isValid()))
            # 標準挙動: Shift範囲選択は既存選択を解除して選び直す。
            # ただし «パンくず»（クリック階層の祖先チェーン）は温存する
            # （「一つ上の階層の選択が外れる」不具合の修正）。
            # 全モデル（このカラム・他カラム・本体）へ一括適用する。
            self._select_exclusively(sm, top, bot, idx)
            _selrows = sorted({i.row() for i in sm.selectedIndexes() if i.column() == 0})
            _mfm_log("Shift-select result: selected_rows=%s (count=%d)"
                     % (_selrows, len(_selrows)))
        else:  # Ctrl
            # トグル判定は «全モデルのどれかで選択されているか» を基準にする。
            # QColumnView はカラムの選択モデルを内部で差し替えるため、
            # view 側モデルの Toggle だけだと「非選択→選択」に化けて
            # 解除できないケースがある（選択1件のCtrlトグル不具合の原因）
            _was = any(m.isSelected(idx) for m in self._all_selection_models())
            _state = QISM.Deselect if _was else QISM.Select
            sm.select(idx, _state | QISM.Rows)
            self._broadcast_select(idx, _state, exclude=sm)
            view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
            _selrows = sorted({i.row() for i in sm.selectedIndexes() if i.column() == 0})
            _mfm_log("Ctrl-toggle: idx_row=%s -> selected_rows=%s" % (idx.row(), _selrows))
        # 結果表示はツリー統合ではなく、常に右側の平坦ビューへ出す。
        # 順序が重要: 先に«操作したカラムの現状»で追跡を更新してから
        # スナップショットを統合する。逆順だと、Ctrlトグルで解除した項目が
        # 追跡の再選択で即復活する（「Ctrlで解除できない」不具合の原因）。
        self._sync_tracked_selection_for_view(view)
        # 冗長な子カラム抑制: current をクリック項目の«親»へ退避。
        # 順序が重要:
        #  - 選択同期(_sync)より後（先に行うとカラム再構築が選択読み取りと競合）
        #  - スナップショット復元より前（復元はcurrentのパンくずを焼き込むため、
        #    current がクリック項目のままだと、Ctrlで解除した直後の項目が
        #    パンくずとして即再選択される＝「解除できない」不具合）
        self._park_current_at_parent(sm, idx)
        self._restore_selection_snapshot(preserve_snapshot)
        dirs = self._deepest_selected_dirs()
        _mfm_log("multi_select flat: selected_dirs=%d %r flat_cb=%s"
                 % (len(dirs), [os.path.basename(d) for d in dirs], callable(self._flat_cb)))
        # レイアウト安定化: 操作中カラムの画面上の位置を固定する
        # （平坦カラム出現やカラム再構築で視点が飛ぶのを防ぐ）
        self._anchor_column_x(view)
        # 平坦ビューが自動で出るのは «複数選択(2件以上)» の時だけ。
        # 1件だけの選択で出すのは誤動作（単一は平坦ボタン/トグルで明示的に）
        self._request_flat(dirs if len(dirs) >= 2 else [])
        self._restore_selection_snapshot_later(preserve_snapshot)

    def _anchor_column_x(self, view):
        """操作中カラムのルートパスと画面上のx位置を記録し、
        レイアウト変更後に同じ位置へ戻す（視点の揺れ防止）。"""
        try:
            self._anchor_info = (self._path_for_index(view.rootIndex()), view.x())
        except Exception:
            self._anchor_info = None
        # QColumnView の scrollTo は current変更後も遅延して複数回走るため、
        # 落ち着くまで数回に分けて位置を戻す
        for ms in (60, 160, 300, 520):
            QTimer.singleShot(ms, self._restore_anchor_column_x)

    def _restore_anchor_column_x(self):
        info = getattr(self, "_anchor_info", None)
        if not info:
            return
        path, x0 = info
        try:
            target = None
            for v in self.findChildren(QListView):
                if (v.isVisible() and v.model() is not None
                        and self._path_for_index(v.rootIndex()) == path):
                    target = v
                    break
            if target is None:
                return
            hb = self.horizontalScrollBar()
            if hb is None:
                return
            # 元のx位置を基本にしつつ、ペイン縮小後も操作カラム全体が
            # 見えるようにクランプする（右端で見切れるのを防ぐ）
            vpw = self.viewport().width()
            desired = min(x0, max(0, vpw - target.width()))
            dx = target.x() - desired
            if dx:
                hb.setValue(max(0, min(hb.value() + dx, hb.maximum())))
        except Exception:
            pass

    def _all_selection_models(self):
        """全カラムの選択モデル＋本体の選択モデル（重複除去済み）。

        QColumnView は createColumn 時に本体の選択モデルを«複製»して各カラムへ
        渡すため、選択操作は全モデルへ同期しないと、カラム再構築時に
        どこかへ残った古い選択が復活する。"""
        models = []
        seen = set()
        try:
            for v in self.findChildren(QListView):
                sm = v.selectionModel()
                if sm is not None and id(sm) not in seen:
                    seen.add(id(sm))
                    models.append(sm)
            top = self.selectionModel()
            if top is not None and id(top) not in seen:
                models.append(top)
        except Exception:
            pass
        return models

    def _broadcast_select(self, idx, state, exclude=None):
        """単一項目の Select/Deselect を全モデルへ伝播する。"""
        QISM = _QtCore.QItemSelectionModel
        for m in self._all_selection_models():
            if m is exclude:
                continue
            try:
                m.select(idx, state | QISM.Rows)
            except Exception:
                pass

    def _select_exclusively(self, sm, top_idx, bot_idx, clicked_idx):
        """全モデルで «範囲のみ選択» にする（パンくず＝クリック階層の
        祖先チェーンの選択は温存）。標準の Shift 範囲選択の実装。"""
        QISM = _QtCore.QItemSelectionModel
        parent = top_idx.parent()
        r1, r2 = top_idx.row(), bot_idx.row()
        cp = self._path_of_index(clicked_idx)
        nc = os.path.normcase(os.path.normpath(cp)) if cp else ""
        rng = _QtCore.QItemSelection(top_idx, bot_idx)
        for m in self._all_selection_models():
            try:
                for i in list(m.selectedIndexes()):
                    if i.column() != 0:
                        continue
                    if i.parent() == parent and r1 <= i.row() <= r2:
                        continue      # 新しい範囲内 → 残す
                    fp = self._path_of_index(i)
                    nf = os.path.normcase(os.path.normpath(fp)) if fp else ""
                    if nf and nc.startswith(nf + os.sep):
                        continue      # パンくず（祖先）→ 温存
                    m.select(i, QISM.Deselect | QISM.Rows)
                m.select(rng, QISM.Select | QISM.Rows)
            except Exception:
                pass

    def _park_current_at_parent(self, sm, idx):
        """複数選択中は current を «クリック項目の親» に退避する。

        current を項目自身にすると QColumnView がその子カラムを開いてしまい
        «冗長な子カラム» が出る。以前の «幅0に畳む» 方式は、ナビゲーションで
        カラムが作り直されると内部幅テーブル（インデックス対応）が汚染され、
        無関係なカラムまで消える事故を起こした（動画の症状）ため全廃。
        current の退避なら QColumnView 自身が右側の子カラムを畳んでくれる。
        選択は NoUpdate で維持し、カラム再構築後にハイライトを再適用する。"""
        QISM = _QtCore.QItemSelectionModel
        try:
            # 視点固定: park による current 変更で QColumnView が横スクロール
            # （scrollTo）して操作対象を見失うのを防ぐため、一時的に自動
            # スクロールを止める（常時OFFは新規カラムの表示を壊すため不可）
            self.setAutoScroll(False)
        except Exception:
            pass
        try:
            parent = idx.parent()
            target = parent if parent.isValid() else idx
            sm.setCurrentIndex(target, QISM.NoUpdate)
            # 本体側の current も揃える（カラム構成は本体currentが決める）
            top = self.selectionModel()
            if top is not None and top is not sm:
                top.setCurrentIndex(target, QISM.NoUpdate)
        except Exception:
            pass
        QTimer.singleShot(250, lambda: self.setAutoScroll(True))
        # QColumnViewの非同期なカラム再構築の後に、選択ハイライトを描き直す
        QTimer.singleShot(0, self._restore_tracked_selection)
        QTimer.singleShot(80, self._restore_tracked_selection)

    def _is_dir_index(self, idx) -> bool:
        """プロキシindexがフォルダか（I/Oなし: QFileSystemModel のキャッシュ情報）。
        不明な場合はフォルダ扱い（従来のネイティブ経路に落とす）。"""
        try:
            m = self.model()
            i = idx
            while hasattr(m, "mapToSource"):
                i = m.mapToSource(i)
                m = m.sourceModel()
            if not i.isValid() or not i.parent().isValid():
                return True      # ドライブ階層は触らない（フォルダ扱い）
            return bool(m.isDir(i))
        except Exception:
            return True

    def _prune_after_native_click(self, p_idx):
        """ネイティブの通常クリック直後に、本体・他カラムの残存選択を
        クリック項目（＋祖先パンくず）だけに掃除する。"""
        try:
            if p_idx is None or not p_idx.isValid():
                return
            idx = self.model().index(p_idx.row(), 0, p_idx.parent())
            if idx.isValid():
                self._prune_selection_to_single(idx)
        except Exception:
            pass

    def _prune_selection_to_single(self, idx):
        """クリック項目とその祖先(パンくず)以外の選択を全モデルから外し、
        クリック項目を選択状態にする（標準のシングルクリック挙動）。
        各カラムの独立選択モデルと本体モデルの両方を掃除する。"""
        QISM = _QtCore.QItemSelectionModel
        path = self._path_of_index(idx)
        nc = os.path.normcase(os.path.normpath(path)) if path else ""
        models = {v.selectionModel() for v in self.findChildren(QListView)}
        models.add(self.selectionModel())
        models.discard(None)
        for m in models:
            try:
                for i in list(m.selectedIndexes()):
                    if i.column() != 0:
                        continue
                    fp = self._path_of_index(i)
                    nf = os.path.normcase(os.path.normpath(fp)) if fp else ""
                    keep = bool(nf) and (nf == nc or nc.startswith(nf + os.sep))
                    if not keep:
                        m.select(i, QISM.Deselect | QISM.Rows)
                m.select(idx, QISM.Select | QISM.Rows)
            except Exception:
                pass
        for v in self.findChildren(QListView):
            try:
                v.viewport().update()
            except Exception:
                pass

    def _clear_column_selection(self, view):
        """カラムの空白クリック: そのカラム内の選択をすべて解除する。"""
        QISM = _QtCore.QItemSelectionModel
        root = view.rootIndex()
        col_path = ""
        # r108: filePath() はネットワーク先で止まり得る。追跡中のフォルダが
        # 無ければそもそも不要なので、その時は呼ばない。
        if getattr(self, "_selected_dir_paths", None):
            try:
                col_path = self._path_for_index(root) or ""
            except Exception:
                pass
        for m in self._all_selection_models():
            try:
                for i in list(m.selectedIndexes()):
                    if i.column() == 0 and i.parent() == root:
                        m.select(i, QISM.Deselect | QISM.Rows)
            except Exception:
                pass
        # 追跡からこのカラム直下のフォルダを外す
        try:
            if col_path:
                nc = os.path.normcase(os.path.normpath(col_path))
                self._selected_dir_paths = {
                    p for p in self._selected_dir_paths
                    if self._norm_parent(p) != nc
                }
        except Exception:
            pass
        view._mfm_sel_anchor = None
        # 選択を解除したカラムより下（右）の階層カラムは削除する。
        # current をこのカラムのルート（＝このカラムのフォルダ）へ退避すると
        # QColumnView が右側の子カラムを畳んでくれる
        try:
            QISM = _QtCore.QItemSelectionModel
            if root.isValid():
                try:
                    self.setAutoScroll(False)
                except Exception:
                    pass
                smv = view.selectionModel()
                if smv is not None:
                    smv.setCurrentIndex(root, QISM.NoUpdate)
                top = self.selectionModel()
                if top is not None:
                    top.setCurrentIndex(root, QISM.NoUpdate)
                QTimer.singleShot(250, lambda: self.setAutoScroll(True))
                # 再構築後にパンくずを再適用
                QTimer.singleShot(0, self._restore_tracked_selection)
                QTimer.singleShot(80, self._restore_tracked_selection)
        except Exception:
            pass
        dirs = self._deepest_selected_dirs()
        _mfm_log("clear_column_selection: col=%r remain_dirs=%d"
                 % (os.path.basename(col_path), len(dirs)))
        # 解除後も «他所に2件以上の複数選択が残っている» 時だけ平坦を維持。
        # 1件以下なら平坦ビューは閉じる（解除で平坦が出る誤動作の修正）
        self._request_flat(dirs if len(dirs) >= 2 else [])

    def _clear_multi_state(self):
        """通常クリック時に複数選択の追跡状態をリセット（標準の操作感）。
        実際の選択解除は QColumnView 標準のクリック処理（ClearAndSelect）が行う。"""
        self._selected_dir_paths = set()

    def _deselect_paths(self, paths):
        """追跡から外れたフォルダの選択ハイライトも外す（見た目と結果の一致）。"""
        QISM = _QtCore.QItemSelectionModel
        for p in paths:
            try:
                idx = self._proxy_index_for_path(p)
                if not idx.isValid():
                    continue
                sms = [v.selectionModel() for v in self.findChildren(QListView)]
                sms.append(self.selectionModel())  # 本体側も忘れずに
                for sm in sms:
                    if sm is not None and sm.isSelected(idx):
                        sm.select(idx, QISM.Deselect | QISM.Rows)
            except Exception:
                pass

    def currentChanged(self, current: QModelIndex, previous: QModelIndex):
        super().currentChanged(current, previous)
        if self._max_depth <= 0 or not current.isValid():
            return
        # ルートから current までの祖先チェーンを構築
        root = self.rootIndex()
        chain = []
        idx = current
        while idx.isValid() and idx != root:
            chain.append(idx)
            idx = idx.parent()
        # 可視カラム数 = チェーン長（root直下=1カラム目）
        if len(chain) > self._max_depth:
            new_root = _QtCore.QPersistentModelIndex(chain[-1])
            QTimer.singleShot(0, lambda: self._apply_root_shift(new_root))

    def _apply_root_shift(self, persistent_root):
        if not persistent_root.isValid() or self.model() is None:
            return
        idx = self.model().index(persistent_root.row(),
                                 persistent_root.column(),
                                 persistent_root.parent())
        if idx.isValid():
            self.setRootIndex(idx)


# ---------------------------------------------------------------------------
# Browser Panel
# ---------------------------------------------------------------------------

class BrowserPanel(QWidget):
    """
    Full-featured file browser widget.

    Signals
    -------
    file_activated(path)      : user performed the primary action on a file
    directory_changed(path)   : user navigated to a new directory
    selection_changed(paths)  : current selection changed
    status_message(text)      : short status for the status bar
    """

    file_activated = Signal(str)
    directory_changed = Signal(str)
    selection_changed = Signal(list)
    status_message = Signal(str)
    bookmark_requested = Signal(list)   # paths to add to bookmarks
    # r101: 右クリック → バッチリネーム（起動時の選択をそのまま対象にする）
    batch_rename_requested = Signal(list)
    # r107: 現在地の消失判定（ワーカー → UI スレッド）(調べたパス, 移動先)
    _gone_result = Signal(str, str)

    def __init__(self, settings_manager, thumb_manager: ThumbnailManager, parent=None):
        super().__init__(parent)
        _mfm_log("=== BrowserPanel init (build: r108 watchdog 2026-10-01) ===")
        _mfm_timeline("BrowserPanel init (r108)")
        self._sm = settings_manager
        self._thumb_mgr = thumb_manager
        self._thumb_mgr.thumbnail_ready.connect(self._on_thumbnail_ready)

        self._current_path = str(Path.home())
        # 前回パス復元モードが有効なら前回終了時のパスから開始
        if self._sm.get("restore_last_path", True):   # 既定ON（編集メニューで切替）
            _last = self._sm.get("last_path", "")
            if _last:
                self._current_path = _last
        # フルパス保持（深度キャップ廃止）。保存設定に関係なく無制限。
        self._max_depth = 0
        self._click_action = self._sm.get("single_click_action", "open")
        self._dbl_click_action = self._sm.get("double_click_action", "open")

        # External callbacks
        self._on_open: Optional[Callable[[str], None]] = None
        self._on_import: Optional[Callable[[str], None]] = None
        self._on_reference: Optional[Callable[[str], None]] = None

        # N-1: 非同期パスプローブ＆ドライブ隔離（UIフリーズ対策）
        self._prober = PathProber(self)
        self._prober.probed.connect(self._on_probe_result)
        self._drive_scanner = DriveScanner(self)
        self._drive_scanner.drives_ready.connect(self._on_drives_ready)
        self._pending_nav = None
        # 深いパス(遅延ロード)へ setCurrentIndex を再適用するための保留先
        self._pending_current = None

        # N-2: Quick Look（遅延生成）
        self._quick_look = None

        self._build_ui()
        self._navigate(self._current_path)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # 視認性の階層（r56）: ブラウジング面は «一段明るい面＋強いヘアライン枠»
        # （theme_engine の #mfmBrowserPanel）。枠を描くため 4px の内側余白を取る。
        self.setObjectName("mfmBrowserPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        # ── Toolbar ──────────────────────────────────────────────────
        toolbar = QFrame()
        toolbar.setObjectName("mfmBrowserToolbar")
        toolbar.setFrameShape(QFrame.NoFrame)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(4, 4, 4, 4)
        tb_layout.setSpacing(4)

        # Up / back / forward
        # 「◀▶▲」だとカラム移動と誤読される、との指摘によりモチーフ変更:
        #   上の階層 = 左矢印(←) / 戻る = Undo(↶) / 進む = Redo(↷)
        # 並び順も [上へ][戻る][進む] に変更（ドライブセレクタはパス欄の左隣へ）。
        # Mercury: ピルボタン＋ヘアライン境界（ホバーは面をわずかに明るく）
        from core.theme_engine import qss_vars
        _nav_style = (
            "QToolButton{"
            "  background:%(fill_subtle)s; color:%(on_surface)s;"
            "  border:1px solid %(hairline)s; border-radius:15px;"
            "  font-size:19px; font-weight:%(w_strong)s; padding:0px 0px 3px 0px;"
            "}"
            "QToolButton:hover{ background:%(fill_subtle_hover)s;"
            "  border-color:%(hairline_strong)s; }"
            "QToolButton:pressed{ background:%(fill_subtle_pressed)s; }"
            "QToolButton:disabled{ color:%(on_surface_dim)s; background:%(overlay_fill)s;"
            "  border-color:%(hairline)s; }"
            % qss_vars()
        )
        from core.i18n import tr as _tr
        for icon_text, tip, slot in [
            ("←", _tr("上の階層へ", "Up one level"), self._go_up),
            ("↶", _tr("前のディレクトリへ戻る", "Back"), self._go_back),
            ("↷", _tr("先のディレクトリへ進む", "Forward"), self._go_forward),
        ]:
            btn = QToolButton()
            btn.setText(icon_text)
            btn.setToolTip(tip)
            btn.setFixedSize(36, 30)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_nav_style)
            btn.clicked.connect(slot)
            tb_layout.addWidget(btn)

        # Drive selector（パス欄の左隣）
        self._drive_combo = QComboBox()
        self._drive_combo.setFixedWidth(80)
        self._drive_combo.currentTextChanged.connect(self._on_drive_changed)
        tb_layout.addWidget(self._drive_combo)
        self._refresh_drives()

        # Address bar
        self._addr_bar = QLineEdit()
        self._addr_bar.setPlaceholderText("パスを入力...")
        self._addr_bar.returnPressed.connect(lambda: self._navigate(self._addr_bar.text()))
        tb_layout.addWidget(self._addr_bar)

        # 旧グローバルの「フィルター入力／カラム(ビューモード)／名前(ソート)」は
        # カラム別フィルタ・ソート・表示切替へ移行したため撤去。

        # ツールバー(アドレスバー行)は本来の高さに固定する。
        # これを怠ると縦方向にも伸びてビューの空間を奪う（上部に巨大な空白が出る）。
        toolbar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(toolbar, 0)

        # ── Main view stack ───────────────────────────────────────────
        self._view_stack = QSplitter(Qt.Horizontal)
        self._view_stack.setChildrenCollapsible(False)  # 平坦カラムが幅0に潰れないように
        self._view_stack.setHandleWidth(1)

        # Shared filesystem model
        self._fs_model = QFileSystemModel()
        # r77: ファイルアイコンは «拡張子ごと» にレジストリ既定のアイコンだけを
        # 使う（_SafeIconProvider）。Qt 標準はファイル毎に SHGetFileInfo（シェル）を
        # 描画時に遅延実行するため、P4EXP / TortoiseSVN 等のシェル拡張がこの
        # プロセス内で走り、mayapy で「描画中に 1.5 秒停止 → access violation」
        # で起動直後に落ちた（2026-09-16、mfm_freeze.log: data() 内で AV）。
        # r64 のネイティブダイアログ禁止と同じ理由。exe/lnk/url は個別の
        # アイコン抽出・リンク解決を伴うため汎用アイコンにする。
        try:
            self._fs_model.setIconProvider(_SafeIconProvider())
        except Exception as e:
            _mfm_log("icon provider install failed: %r" % (e,))
        # 注意: ここで setRootPath("") を呼んではならない。
        # "" は «全ドライブレターの情報収集» を QFileSystemModel の単一の
        # 収集スレッドへ即座に投入する。切断済み/不調のドライブマッピングが
        # 1つでもあると収集スレッドが数十秒ブロックし、ローカルフォルダの
        # directoryLoaded までもがその後ろに並んで「起動後1分ブラウズ不能」になる
        # （2026-09 実機で発生）。rootPath はナビゲーション時に対象ディレクトリへ
        # 設定する（_navigate_now 内の setRootPath(target_dir)）。
        # QDir.Hidden を含めることで、AppData 等の隠しフォルダもモデルに載せる。
        # 表示の可否は proxy 側で制御（既定は非表示、ナビ経路の祖先のみ強制表示）。
        self._fs_model.setFilter(
            QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Files | QDir.Hidden
        )
        # 読み取り専用を解除 → Qt標準のファイルD&D（Explorerとの相互コピー/移動）を有効化。
        # これにより各アイテムに Drag/Drop フラグが付与される。
        self._fs_model.setReadOnly(False)
        # F2 のインライン名前変更（EditRole → QFileSystemModel::setData）の反映
        try:
            self._fs_model.fileRenamed.connect(self._on_fs_file_renamed)
        except Exception:
            pass
        # フォルダ列挙の高速化: desktop.ini によるカスタムフォルダアイコンの
        # シェル照会を無効化（フォルダ毎のシェルI/Oが減り、大きなツリーや
        # ネットワーク先で初回列挙が大幅に速くなる。表示は標準アイコンになる）
        try:
            self._fs_model.setOption(
                QFileSystemModel.DontUseCustomDirectoryIcons, True)
        except Exception:
            pass
        # 【最重要】リンク解決の無効化。Qt の filePath()/列挙は、行が
        # symlink/ジャンクションだと canonicalFilePath でリンク先を «実際に»
        # 解決する。リンク先が到達不能なネットワークだと SMBタイムアウト
        # （約21秒）がUIスレッド/列挙スレッドで発生し、ナビ毎に21秒フリーズ・
        # 起動63秒停滞の直接原因になった（mfm_freeze.log で特定。2026-09）。
        # 本ツールは設計上リンクを実体へ解決しない（リンクのパスのまま扱う）
        # ため、この解決処理自体が不要。
        try:
            self._fs_model.setResolveSymlinks(False)
        except Exception:
            pass
        # 深いパス(遅延ロード)のカラム構築を確実にするためのリトライ
        self._fs_model.directoryLoaded.connect(self._on_fs_dir_loaded)

        # Proxy model
        self._proxy = FileFilterProxyModel()
        self._proxy.setSourceModel(self._fs_model)
        self._proxy.setSortRole(Qt.DisplayRole)
        # カラム別ソート(lessThan)を有効化（フォルダ優先＋name昇順が既定）
        self._proxy.sort(0, Qt.AscendingOrder)

        # Column view
        self._column_view = CappedColumnView(self._max_depth)
        self._column_view.set_go_up_callback(self._column_go_up)
        # ゆっくり2回クリック → インライン名前変更（r67）
        self._column_view._rename_cb = lambda p: self._rename_inline(p)
        self._column_view.set_thumb_mgr(self._thumb_mgr)
        self._column_view.set_merge_callback(self._on_flat_request)
        # Qt標準のリサイズグリップは「両スクロールバー表示時の角」にしか現れず
        # 実用不可のため無効化し、自前のハンドル（_ColumnResizeHandle）を使う。
        try:
            self._column_view.setResizeGripsVisible(False)
        except Exception:
            pass
        # 保存済みのカラム幅を適用（未保存なら既定幅300）
        self._column_view.set_settings(self._sm)
        self._column_view.setModel(self._proxy)
        self._column_view.activated.connect(self._on_item_activated)
        self._column_view.clicked.connect(self._on_item_clicked)
        self._column_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._column_view.customContextMenuRequested.connect(self._show_context_menu)
        self._configure_dnd(self._column_view)
        self._column_view.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # Thumbnail list view
        self._thumb_view = QListView()
        self._thumb_view.setModel(self._proxy)
        # 【重要】ビューのルートを «マイコンピュータ（全ドライブ）» のままに
        # しない。rootIndex が無効のまま表示されると Qt が全ドライブを列挙し、
        # 切断済みのドライブマッピング（W:, X: 等）ごとに列挙スレッドが約21秒
        # ブロック → その後ろに並ぶ通常フォルダの読み込みまで遅れ「起動時に
        # 数十秒Loading」の元凶になった（2026-09）。ローカルのホームを仮の
        # ルートにしておき、直後のパス復元/ナビゲーションで置き換える。
        try:
            _home_idx = self._proxy.mapFromSource(
                self._fs_model.index(os.path.expanduser("~")))
            if _home_idx.isValid():
                self._column_view.setRootIndex(_home_idx)
                self._thumb_view.setRootIndex(_home_idx)
        except Exception:
            pass
        # （Quick Look 用イベントフィルタは _build_ui 末尾で両ビューに設置）
        self._thumb_view.setViewMode(QListView.IconMode)
        self._thumb_view.setResizeMode(QListView.Adjust)
        self._thumb_view.setSpacing(4)
        self._thumb_delegate = ThumbnailDelegate(
            self._thumb_mgr,
            self._sm.get("thumbnail_size", 128)
        )
        self._thumb_view.setItemDelegate(self._thumb_delegate)
        self._thumb_view.setGridSize(QSize(
            self._sm.get("thumbnail_size", 128) + 16,
            self._sm.get("thumbnail_size", 128) + 32
        ))
        self._thumb_view.activated.connect(self._on_item_activated)
        self._thumb_view.clicked.connect(self._on_item_clicked)
        self._thumb_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._thumb_view.customContextMenuRequested.connect(self._show_context_menu)
        self._configure_dnd(self._thumb_view)
        self._thumb_view.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self._view_stack.addWidget(self._column_view)
        self._view_stack.addWidget(self._thumb_view)
        self._thumb_view.hide()

        # インライン平坦カラム（複数選択時に自動／単一は平坦トグル時に、
        # 通常のカラムビューの右隣に «次のカラム» として表示。ビューは切り替えない）
        from ui.flat_column import FlatColumn
        self._flat_col = FlatColumn(self)
        self._flat_col.set_recursive(bool(self._sm.get("flat_recursive", True)))
        self._flat_col.file_activated.connect(open_with_default_app)
        self._flat_col.file_selected.connect(self._sync_quick_look)
        # 平坦ビューで単一ファイル選択時もパス欄にファイル名まで表示
        self._flat_col.file_selected.connect(
            lambda p: self._addr_bar.setText(p) if p else None)
        self._flat_col.closed.connect(self._flat_col.hide)
        # r74: 平坦ビューからの D&D も落下先が DCC なら通常カラムと同じ通知経路へ
        # r90: mime も同じもの（DCC への落下は Manager が引き受ける）を使う
        self._flat_col._view._mime_factory = self._column_view.make_drag_mime
        self._flat_col.drag_finished.connect(
            lambda paths: self._column_view._notify_drag_finished(
                list(paths), getattr(self._flat_col._view, "last_mime", None)))
        # 平坦ビューにも通常カラムと同じ右クリックメニューを付ける
        self._flat_col._view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._flat_col._view.customContextMenuRequested.connect(
            self._show_flat_context_menu)
        self._view_stack.addWidget(self._flat_col)
        self._flat_col.hide()
        # カラムビューが主、平坦カラムは右に従。初期サイズ配分。
        self._view_stack.setStretchFactor(0, 1)
        self._column_view.set_flat_callback(self._on_flat_request)
        self._column_view.set_thumb_prefetch_callback(
            lambda folder: self._prefetch_thumbs_async(folder, force=True))
        self._column_view.set_file_drop_callback(self._on_files_dropped)
        # r102: 表示サイズのスライダーは «全体» に効かせる（カラム以外も）
        self._column_view.set_item_size_callback(self._apply_item_size_to_views)

        # 共通フォルダのドリルダウン・カラム群（複数選択時に平坦カラムの左へ挿入）
        self._common_cols = []
        self._common_srcs = []

        # r97: 圧縮ファイルの中身カラム（zip/tar）。面の色を琥珀寄りに振って
        # «通常のフォルダではない» ことを一目で分かるようにしている。
        from ui.archive_column import ArchiveColumn
        self._archive_col = ArchiveColumn(self)
        self._archive_col.closed.connect(lambda: self._on_archive_request(""))
        self._archive_col.status_message.connect(self.status_message.emit)
        self._archive_col.file_activated.connect(open_with_default_app)
        self._view_stack.addWidget(self._archive_col)
        self._archive_col.hide()

        # 平坦カラムの右に置くスペーサー。平坦カラムを«次のカラム»として
        # カラム幅で見せ、残りは通常の空き背景にする（巨大パネル化を防ぐ）
        self._flat_spacer = QWidget()
        self._flat_spacer.setObjectName("mfmFlatSpacer")
        self._view_stack.addWidget(self._flat_spacer)
        self._flat_spacer.hide()

        # 旧マージ専用パネル（互換のため保持。通常は不使用）
        from ui.merge_view import MergePanel
        self._merge_panel = MergePanel(self)
        # インライン表示なので、閉じる時はマージビューを隠すだけ（メインは常に表示）
        self._merge_panel.closed.connect(self._merge_panel.hide)
        self._merge_panel.file_selected.connect(self._sync_quick_look)
        self._view_stack.addWidget(self._merge_panel)
        self._merge_panel.hide()

        # stretch=1 で残りの縦空間をすべてビューに割り当てる
        layout.addWidget(self._view_stack, 1)

        # ── History navigation ────────────────────────────────────────
        # r98: 表示中のフォルダが（外部操作も含め）消えたら、実在する一番近い
        # 親へ自動で移動する。ネットワーク先を叩かないよう間隔は長めに取る。
        self._gone_watch = QTimer(self)
        self._gone_watch.setInterval(8000)
        # r107: **UI スレッドで os.path.isdir を呼ばない**。ネットワーク/
        # Perforce のワークスペースでは、同期中や応答待ちの時に stat が
        # 10 秒以上返らず、そのまま UI が固まる（mfm_freeze.log で確定。
        # 「p4 で最新取得中にフリーズする」の正体）。判定はワーカーで行う。
        self._gone_watch.timeout.connect(self._check_current_gone_async)
        self._gone_watch.start()
        self._gone_probe_busy = False
        self._gone_result.connect(self._on_gone_result)

        self._history: List[str] = []
        self._history_index: int = -1

        # サムネ先読みのファイル列挙結果をワーカー→UIへ運ぶ通知
        class _PrefetchNotifier(_QtCore.QObject):
            files_ready = Signal(list)
        self._prefetch_notifier = _PrefetchNotifier(self)
        self._prefetch_notifier.files_ready.connect(
            lambda files: self._thumb_mgr.prefetch(files))

        # ファイルのD&Dはビュー(モデル)側で一括処理するため、パネル自身は
        # ドロップを受け取らない（横取りして「移動だけ」になるのを防ぐ）。
        self.setAcceptDrops(False)

        # N-2: Space Quick Look 用イベントフィルタ
        self._column_view.installEventFilter(self)
        self._thumb_view.installEventFilter(self)

        # Ctrl+C / Ctrl+X / Ctrl+V / Delete（子ビューにフォーカスがあっても発火）
        self._install_clipboard_actions()

        # 外部サービス連携（Git/SVN/Perforce/クラウド）。検出はワーカーで1回。
        # 設定 integrations_enabled=False なら完全に無効（コードパスに入らない）。
        try:
            from core.integrations import get_manager
            _mgr = get_manager()
            _mgr.start(bool(self._sm.get("integrations_enabled", True)))
            # 操作結果の通知は «プロセスで1回だけ» 接続（エリアが複数あっても
            # ダイアログを重複表示しない。受け手はモジュール関数）
            global _INTEG_RESULT_CONNECTED
            if not _INTEG_RESULT_CONNECTED:
                _mgr.action_finished.connect(_on_integration_action_finished)
                _INTEG_RESULT_CONNECTED = True
        except Exception as _e:
            _mfm_log("integrations start error: %r" % (_e,))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Quick Look (N-2)
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if (event.type() == _QtCore.QEvent.KeyPress
                and event.key() == Qt.Key_Space
                and obj in (self._column_view, self._thumb_view)):
            self._toggle_quick_look()
            return True
        return super().eventFilter(obj, event)

    def _toggle_quick_look(self):
        paths = self._get_selected_paths()
        if not paths:
            return
        if self._quick_look is None:
            from ui.quick_look import QuickLookWindow
            self._quick_look = QuickLookWindow(self)
        self._quick_look.toggle_for(paths[0])

    def _sync_quick_look(self, path: str):
        """選択変更時、Quick Look が開いていれば内容を追従させる。"""
        if self._quick_look and self._quick_look.isVisible() and os.path.isfile(path):
            # 選択追従ではクラウドの実体をダウンロードさせない（r83）
            self._quick_look.show_for(path, allow_download=False)

    def _navigate(self, path: str, add_to_history: bool = True):
        """到達可能性を非同期確認してから移動する（N-1: UIを止めない）。

        重要: ここでは os.path.realpath による実体解決を行わない。
        - シンボリックリンク/ジャンクション: リンクのパスを維持して中身を表示する
          （実体側ドライブへ飛ばさない）。クリックは _follow_link 経由でこの関数に来る。
        - .lnk/.url ショートカット: 呼び出し元(_maybe_follow_shortcut)が既に
          参照先(実体パス)へ解決済みのため、ここで再解決する必要はない。
        以前ここに realpath を入れていたためシンボリックリンクが実体へ遷移していた（回帰）。"""
        path = os.path.normpath(path)
        # ナビゲーションは標準挙動どおり複数選択をリセットする
        # （ブックマーク・履歴・▲・ショートカット追従すべて共通）
        try:
            self._column_view._clear_multi_state()
            if self._flat_col.isVisible():
                self._on_flat_request([])
            if self._archive_col.isVisible():
                self._on_archive_request("")        # r97
        except Exception:
            pass
        self._pending_nav = (path, add_to_history)
        self.status_message.emit(f"確認中: {path}")
        self._prober.probe(path)

    def _on_probe_result(self, path: str, reachable: bool):
        if not self._pending_nav or self._pending_nav[0] != path:
            return  # 古いプローブ結果は無視（最後の要求のみ有効）
        _, add_to_history = self._pending_nav
        self._pending_nav = None
        if not reachable:
            self.status_message.emit(
                f"パスに到達できません（不存在または応答なし）: {path}")
            return
        self._navigate_now(path, add_to_history)

    def _set_current_path(self, path: str, add_to_history: bool = True):
        """ビューのルートを変えずに現在地の状態だけ更新する（カラム展開用）。"""
        self._current_path = path
        self._addr_bar.setText(path)
        self._sync_drive_combo(path)
        self.directory_changed.emit(path)
        self.status_message.emit(path)
        if add_to_history:
            self._history = self._history[:self._history_index + 1]
            self._history.append(path)
            self._history_index = len(self._history) - 1
            self._sm.add_to_history(path)
        self._prefetch_thumbs_async(path)

    def _navigate_now(self, path: str, add_to_history: bool = True):
        _mfm_timeline("navigate_now: %r" % path)
        # r83: 新しい移動なので «選択維持ガード» を解除し、自己修復を許可する
        try:
            self._column_view._mfm_user_selected = False
        except Exception:
            pass
        # 各段階の所要時間を計測（0.5秒超は mfm_freeze.log に SLOW 記録）。
        # PySideのC++呼び出しはGILを離さないためサンプラでは特定できない。
        _pc = _time_mod.perf_counter

        def _timed(label, fn):
            t0 = _pc()
            try:
                return fn()
            finally:
                dt = _pc() - t0
                if dt > 0.5:
                    _mfm_slow_note("navigate_now %s=%.2fs path=%r"
                                   % (label, dt, path))

        self._current_path = path
        self._addr_bar.setText(path)
        _timed("sync_drive_combo", lambda: self._sync_drive_combo(path))

        is_dir = _timed("isdir", lambda: os.path.isdir(path))
        target_dir = path if is_dir else str(Path(path).parent)

        # 経路上の隠しフォルダ(AppData等)を強制表示にしてカラムチェーンを構築可能にする
        _timed("apply_force_visible", lambda: self._apply_force_visible(path))

        # QFileSystemModel に対象ディレクトリまでのチェーンを能動的にロードさせる。
        # これが無いと、別ドライブ/深い/隠し経由のパスは index が無効のままで
        # カラムが構築されない（クリックしてもカラムが伸びない原因）。
        try:
            _timed("setRootPath", lambda: self._fs_model.setRootPath(target_dir))
        except Exception:
            pass

        # カラムのルート: パス途中(または自身)にリンクがあれば最上位リンク、無ければドライブ最上位
        col_root = _timed("column_root_for", lambda: self._column_root_for(path))
        _root_src = _timed("index(col_root)", lambda: self._fs_model.index(col_root))
        _timed("setRootIndex", lambda: self._column_view.setRootIndex(
            self._proxy.mapFromSource(_root_src)))
        _init_src = _timed("index(path)", lambda: self._fs_model.index(path))
        _init_idx = self._proxy.mapFromSource(_init_src)
        _timed("setCurrentIndex", lambda: self._column_view.setCurrentIndex(_init_idx))
        # 深い/別ドライブ/隠し経由のパスは遅延ロードのため、
        # 最上位から1段ずつ読み込みを起動してカラムを伸ばす
        self._pending_current = path
        _timed("prime_path_loading", lambda: self._prime_path_loading(path))
        try:
            _src = self._fs_model.index(path)
            _rc = self._fs_model.rowCount(self._fs_model.index(target_dir))
            _mfm_log("navigate_now: path=%r col_root=%r src_valid=%s target_dir_rowcount=%d"
                     % (path, col_root, _src.isValid(), _rc))
        except Exception as _e:
            _mfm_log("navigate_now: log error %r" % _e)

        # サムネ/リストビューは対象フォルダ単体を表示
        self._thumb_view.setRootIndex(
            self._proxy.mapFromSource(self._fs_model.index(target_dir)))

        if is_dir:
            self.directory_changed.emit(path)

        if add_to_history:
            # Prune forward history
            self._history = self._history[:self._history_index + 1]
            self._history.append(path)
            self._history_index = len(self._history) - 1
            self._sm.add_to_history(path)

        self.status_message.emit(path)  # 「確認中」表示を現在地に更新

        # Prefetch thumbnails for visible items（リストアップはワーカーで行う）
        self._prefetch_thumbs_async(path)

    def _any_thumb_column(self) -> bool:
        """サムネイル表示になっているカラムがあるか（既定はリスト表示）。"""
        try:
            for v in self._column_view.findChildren(QListView):
                if getattr(v, "_mfm_view_mode", "list") == "thumb":
                    return True
        except Exception:
            pass
        return False

    def _prefetch_thumbs_async(self, path: str, force: bool = False):
        """サムネ先読み用のファイル列挙をワーカースレッドで行い、結果だけを
        UIスレッドへ戻す。os.listdir/isfile はネットワーク先ではブロックする
        ため、UIスレッドで直接呼んではならない（path_guard の設計原則）。

        r83: **リスト表示のときは先読みしない**。リスト表示のデリゲートは
        サムネイルを使わないので完全な無駄であり、クラウド（オンラインのみ）
        では «フォルダを開いただけで 64 個ダウンロード» という実害が出る。
        サムネイル表示に切り替えたカラムからは force=True で呼ぶ。"""
        import threading
        if not force and not self._any_thumb_column():
            return

        def _run():
            try:
                files = self._list_visible_files(path)
            except OSError:
                files = []
            if files:
                # UIスレッドへ引き渡し（prefetch はキュー投入のみで軽い）
                notifier = self._prefetch_notifier
                if notifier is not None:
                    notifier.files_ready.emit(files)

        threading.Thread(target=_run, daemon=True,
                         name="mfm-thumb-list").start()

    def _column_go_up(self, folder_path=None):
        """◀: そのカラムを消して1つ上の階層へ（ルート不変のcurrentIndex移動）。"""
        base = folder_path or self._current_path
        if not base:
            return
        parent = str(Path(base).parent)
        if parent and os.path.normpath(parent) != os.path.normpath(base):
            self._select_in_columns(parent)

    def _column_root_for(self, path: str) -> str:
        """カラムビューのルートを決める。パス途中(または自身)に symlink/ジャンクションが
        あると、その配下はドライブ最上位ルートでは列挙できない環境があるため、
        最上位のリンク祖先をルートにする。リンクが無ければドライブ最上位。"""
        p = os.path.normpath(os.path.abspath(path))
        drive = os.path.splitdrive(p)[0]
        top = (drive + os.sep) if drive else os.sep
        comps = []
        cur = p
        while True:
            comps.append(cur)
            parent = os.path.dirname(cur)
            if not parent or parent == cur:
                break
            cur = parent
        comps.reverse()
        for comp in comps:
            try:
                # 注意: os.path.isdir はリンクを «辿る» ため、ネットワーク先の
                # ジャンクションが経路にあるとUIスレッドがブロックする
                # （戻る/進む毎にフリーズした実例あり）。判定は lstat ベース
                # （辿らない）に限定する。
                if self._is_symlink_or_junction(comp):
                    import stat as _stat
                    st = os.lstat(comp)
                    if _stat.S_ISDIR(st.st_mode) or \
                            _stat.S_ISLNK(st.st_mode):
                        return comp
            except OSError:
                pass
        return top

    def _select_in_columns(self, path: str):
        """ルートは _column_root_for で決め、currentIndex を path に移す。
        上位を選ぶと深いカラムが自動的に消える。"""
        if not path:
            return
        self._apply_force_visible(path)
        col_root = self._column_root_for(path)
        root_idx = self._column_view.rootIndex()
        cur_root = (_safe_file_path(self._fs_model, self._proxy.mapToSource(root_idx))
                    if root_idx.isValid() else "")
        if os.path.normcase(os.path.normpath(cur_root or "")) != \
                os.path.normcase(os.path.normpath(col_root)):
            self._column_view.setRootIndex(
                self._proxy.mapFromSource(self._fs_model.index(col_root)))
        src = self._fs_model.index(path)
        if src.isValid():
            self._column_view.setCurrentIndex(self._proxy.mapFromSource(src))
        self._pending_current = path
        self._prime_path_loading(path)
        self._current_path = path
        self._addr_bar.setText(path)
        self._sync_drive_combo(path)
        self.directory_changed.emit(path)
        self.status_message.emit(path)

    @staticmethod
    def _ancestors_of(path: str):
        """path 自身からドライブ最上位までの祖先パス一覧（normpath）。"""
        out = []
        cur = os.path.normpath(os.path.abspath(path))
        while True:
            out.append(cur)
            parent = os.path.dirname(cur)
            if not parent or parent == cur:
                break
            cur = parent
        return out

    def _apply_force_visible(self, path: str):
        """ナビ対象の経路上にある隠しフォルダ(AppData等)だけを強制表示にする。"""
        try:
            self._proxy.set_force_visible(self._ancestors_of(path))
        except Exception:
            pass

    def _prime_path_loading(self, target: str):
        """深い/別ドライブ/隠しフォルダ経由のパスでも確実にカラムを構築するため、
        ドライブ最上位から対象まで各階層の読み込みを起動する。
        QFileSystemModel は遅延ロードのため、最上位を fetchMore して
        directoryLoaded 連鎖（_on_fs_dir_loaded）で1段ずつ降りていく。"""
        try:
            ancestors = self._ancestors_of(target)  # deep→top
        except Exception:
            return
        if not ancestors:
            return
        top = ancestors[-1]
        # 全階層の読み込みを«一括で»収集キューへ投入する。
        # 従来は directoryLoaded 連鎖で1段ずつ降りており、深いパスでは
        # 階層数ぶんの直列待ちが起動時間に直結していた（10階層なら10往復）。
        # index(パス) は親の列挙前でもノードを明示生成するため一括投入できる。
        # 先頭=target（deep側）から入れ、目的地のカラムが最初に埋まるようにする。
        queued = 0
        for p in ancestors:
            try:
                idx = self._fs_model.index(p)
                if idx.isValid() and self._fs_model.canFetchMore(idx):
                    self._fs_model.fetchMore(idx)
                    queued += 1
            except Exception:
                pass
        _mfm_timeline("prime: %d/%d 階層を一括投入 target=%r"
                      % (queued, len(ancestors), target))
        # 既にロード済みの階層がある場合に備え、最初の再適用も試す
        self._advance_pending_load(top)
        # 保険: 対象が«既にロード済み»だと QFileSystemModel は directoryLoaded を
        # 再発火しない環境がある（pip版Qt。MayaのQtは再発火する）。その場合
        # advance が永遠に完了せず「クリックしても何も起きない」になるため、
        # シグナルに依存しない完了判定を遅延で数回試す。
        for _ms in (120, 400, 900):
            QTimer.singleShot(_ms, lambda p=target: self._maybe_finalize_navigation(p))

    def _maybe_finalize_navigation(self, target: str):
        """directoryLoaded 非発火時のフォールバック完了判定。
        対象 index が有効で子情報が読める状態なら到達扱いで仕上げる。
        （シンボリックリンク/ジャンクションを«2回目以降»に開く時、モデルが
        キャッシュ済みでシグナルが来ない事象への対策。EXE版で実際に発生）"""
        if not self._pending_current:
            return
        try:
            if os.path.normcase(os.path.normpath(self._pending_current)) != \
               os.path.normcase(os.path.normpath(target)):
                return
        except Exception:
            return
        tgt_idx = self._fs_model.index(target)
        if not tgt_idx.isValid():
            return
        try:
            ready = (self._fs_model.rowCount(tgt_idx) > 0
                     or not self._fs_model.canFetchMore(tgt_idx))
        except Exception:
            ready = False
        if not ready:
            return
        _mfm_log("finalize_nav(fallback): target=%r（directoryLoaded未発火対策）"
                 % target)
        _mfm_timeline("navigate完了(fallback): %r" % target)
        self._pending_current = None
        pidx = self._proxy.mapFromSource(tgt_idx)
        if pidx.isValid():
            self._column_view.setCurrentIndex(pidx)
        # 注意: r31の目的地優先ロードにより、祖先の列挙は完了«後»に届く。
        # 短いリトライだけだと祖先カラムが再アンカーされず「カラムがズレて
        # 見えない階層ができる」ため、遅めのリトライも入れる（rebuildは
        # already-correct スキップ付きなので余分な再構築コストは無い）。
        for _ms in (90, 320, 1200, 2600):
            QTimer.singleShot(_ms, lambda p=target: self._force_column_rebuild(p))

    def _advance_pending_load(self, loaded_path: str):
        """loaded_path（ロード完了済み階層）から対象へ向けて次の階層の
        読み込みを起動し、可能なら setCurrentIndex を再適用する。"""
        target = self._pending_current
        if not target:
            return
        try:
            t = os.path.normcase(os.path.normpath(target))
            ld = os.path.normcase(os.path.normpath(loaded_path))
        except Exception:
            return
        if not (t == ld or t.startswith(ld + os.sep)):
            return
        # 対象まで未達なら、対象へ向かう「次の1階層」をロード起動する
        if t != ld:
            rel = os.path.normpath(target)[len(os.path.normpath(loaded_path)):].lstrip("\\/")
            next_comp = rel.replace("\\", "/").split("/", 1)[0]
            if next_comp:
                next_path = os.path.join(loaded_path, next_comp)
                nidx = self._fs_model.index(next_path)
                if nidx.isValid() and self._fs_model.canFetchMore(nidx):
                    self._fs_model.fetchMore(nidx)
        # 現時点で対象インデックスが有効なら選択を再適用（カラムが伸びる）
        src = self._fs_model.index(target)
        proxy_idx = self._proxy.mapFromSource(src)
        _mfm_log("advance: loaded=%r reached=%s src_valid=%s proxy_valid=%s"
                 % (loaded_path, t == ld, src.isValid(), proxy_idx.isValid()))
        if t == ld and _MFM_DEBUG:
            # 到達時に祖先チェーンを診断（どこで proxy が切れるか）
            try:
                for anc in reversed(self._ancestors_of(target)):
                    si = self._fs_model.index(anc)
                    pi = self._proxy.mapFromSource(si)
                    hid = False
                    try:
                        hid = self._fs_model.fileInfo(si).isHidden()
                    except Exception:
                        pass
                    fv = os.path.normcase(os.path.normpath(anc)) in self._proxy._force_visible
                    _mfm_log("  chain anc=%r fs=%s proxy=%s hidden=%s force_visible=%s"
                             % (anc, si.isValid(), pi.isValid(), hid, fv))
            except Exception as _e:
                _mfm_log("  chain diag error %r" % _e)
        if proxy_idx.isValid():
            self._column_view.setCurrentIndex(proxy_idx)
        if t == ld:
            cur = self._column_view.currentIndex()
            curpath = ""
            if cur.isValid():
                curpath = _safe_file_path(self._fs_model, self._proxy.mapToSource(cur))
            _mfm_log("advance(after setCurrent): colview_current=%r" % curpath)
            _mfm_timeline("navigate完了: %r" % target)
            self._pending_current = None
            # 内部状態は正しいが表示カラムが古いブランチのまま残る QColumnView の
            # 描画バグ対策。ロード完全後に「ルート＋現在地」を遅延再適用して
            # カラムスタックを作り直す（シグナル内での setRootIndex はクラッシュ
            # するため QTimer で次のイベントループへ逃がす）。
            tgt = target
            # ロード/カラム除去が落ち着いた後にチェーンを強制再展開し、
            # 深いカラムの欠落とスクロール範囲の空白の両方を解消する。
            # （遅めのリトライは、目的地優先ロードで後から届く祖先の
            #  列挙に対する再アンカー用。r31のカラムズレ対策）
            for _ms in (90, 320, 1200, 2600):
                QTimer.singleShot(_ms, lambda p=tgt: self._force_column_rebuild(p))

    def _force_column_rebuild(self, target: str):
        """ルートと現在地を再適用して QColumnView のカラムを末端まで作り直す。
        既に current==target だと setCurrentIndex が no-op になり深い階層が
        展開されないため、一旦 current を無効化してから target を設定し直す。"""
        # ── r83: «ユーザーの選択を絶対に壊さない» ガード ─────────────────
        # この再構築は最大2.6秒後まで遅延実行される «見た目の自己修復» に過ぎない。
        # クラウドストレージのように列挙が遅いフォルダでは、ユーザーが
        # Ctrl/Shift でファイルを選んでいる最中にこれが発火し、
        # setCurrentIndex(無効)→再設定で選択が全消去される
        # （＝「クラウドストレージで複数選択ができない」の原因。2026-09-17 報告）。
        # 選択が2件以上ある、またはナビゲーション後にユーザーが選択操作を
        # 始めているなら、自己修復より «選択の維持» を優先して何もしない。
        try:
            if len(self._get_selected_paths()) > 1:
                _mfm_log("force_rebuild skip: 複数選択中のため中止 target=%r" % target)
                return
            if getattr(self._column_view, "_mfm_user_selected", False):
                _mfm_log("force_rebuild skip: ユーザー選択操作後のため中止 target=%r"
                         % target)
                return
        except Exception:
            pass
        # 既に別パスへ移動済みなら、古い再構築でカラムを壊さないようスキップ（高速クリック対策）。
        try:
            if os.path.normcase(os.path.normpath(target)) != \
               os.path.normcase(os.path.normpath(self._current_path or "")):
                return
        except Exception:
            return
        # 既に正しく表示できているなら何もしない。
        # force_rebuild は current 無効化→再設定でカラムを全て組み直すため、
        # 複数経路（advance / finalize_nav / 二段タイマー）から重複実行されると
        # カラムがガクガク動く。root・current・対象カラムの可視性まで一致して
        # いれば再構築は不要（リンククリック時の見た目を通常フォルダと同等にする）。
        try:
            cv = self._column_view
            nt = os.path.normcase(os.path.normpath(target))
            cur = cv.currentIndex()
            root = cv.rootIndex()
            cur_path = (_safe_file_path(self._fs_model, self._proxy.mapToSource(cur))
                        if cur.isValid() else "")
            root_path = (_safe_file_path(self._fs_model, self._proxy.mapToSource(root))
                         if root.isValid() else "")
            want_root = self._column_root_for(target)
            same = (cur_path and
                    os.path.normcase(os.path.normpath(cur_path)) == nt and
                    os.path.normcase(os.path.normpath(root_path or "")) ==
                    os.path.normcase(os.path.normpath(want_root)))
            if same:
                for v in cv.findChildren(QListView):
                    if v.isHidden() or v.model() is None:
                        continue
                    fp = cv._path_for_index(v.rootIndex())
                    if fp and os.path.normcase(os.path.normpath(fp)) == nt:
                        _mfm_log("force_rebuild skip: already correct target=%r"
                                 % target)
                        return
        except Exception:
            pass
        try:
            col_root = self._column_root_for(target)
            ridx = self._proxy.mapFromSource(self._fs_model.index(col_root))
            cidx = self._proxy.mapFromSource(self._fs_model.index(target))
            if ridx.isValid():
                self._column_view.setRootIndex(ridx)
            if cidx.isValid():
                # 無効→target で current 変更を強制し、深い階層まで再展開させる
                self._column_view.setCurrentIndex(QModelIndex())
                self._column_view.setCurrentIndex(cidx)
            try:
                self._column_view.updateGeometries()
            except Exception:
                pass
            # ── 診断＋自己修復 ─────────────────────────────────────
            # Windows実機のpip版Qtで «リンクをルートにすると直下がproxy越しに
            # 見えない／カラムが構築されない» 事象への対策。
            rrows = self._proxy.rowCount(ridx) if ridx.isValid() else -1
            n_cols = 0
            try:
                n_cols = sum(1 for v in self._column_view.findChildren(QListView)
                             if not v.isHidden() and v.model() is not None)
            except Exception:
                pass
            _mfm_log("force_rebuild: target=%r root_valid=%s cur_valid=%s "
                     "hbar_max=%d proxy_root_rows=%s visible_cols=%d"
                     % (target, ridx.isValid(), cidx.isValid(),
                        self._column_view.horizontalScrollBar().maximum(),
                        rrows, n_cols))
            if ridx.isValid() and rrows == 0:
                # 1) プロキシのフィルタ再評価で子のマッピングを作り直す
                try:
                    self._proxy.invalidateFilter()
                except Exception:
                    pass
                rrows2 = self._proxy.rowCount(ridx)
                _mfm_log("force_rebuild retry(invalidateFilter): rows=%s" % rrows2)
                if rrows2 == 0:
                    # 2) ドライブ最上位ルートへフォールバック（リンクルートを諦め、
                    #    最上位からのチェーン表示で target まで開く）
                    drive = os.path.splitdrive(os.path.normpath(target))[0]
                    top = (drive + os.sep) if drive else os.sep
                    tidx = self._proxy.mapFromSource(self._fs_model.index(top))
                    ci = self._proxy.mapFromSource(self._fs_model.index(target))
                    if tidx.isValid():
                        self._column_view.setRootIndex(tidx)
                        if ci.isValid():
                            self._column_view.setCurrentIndex(QModelIndex())
                            self._column_view.setCurrentIndex(ci)
                    _mfm_log("force_rebuild fallback(drive-top): root=%r cur_valid=%s"
                             % (top, ci.isValid()))
        except Exception as _e:
            _mfm_log("force_rebuild error %r" % _e)

    def _on_fs_dir_loaded(self, loaded_path: str):
        """遅延ロード完了時、保留中の対象へ向けて1段ずつ降りる。"""
        # 列挙完了のタイムライン（起動直後の停滞調査用）
        _mfm_log("directoryLoaded: %r" % loaded_path)
        try:
            _rc = self._fs_model.rowCount(self._fs_model.index(loaded_path))
        except Exception:
            _rc = -1
        _mfm_timeline("directoryLoaded: %r (%d件)" % (loaded_path, _rc))
        # スピナー消灯の根拠として記録（「空フォルダ」と「列挙中」の見分け用）
        self._column_view.note_dir_loaded(loaded_path)
        self._advance_pending_load(loaded_path)

    def _go_back(self):
        if self._history_index > 0:
            self._history_index -= 1
            self._navigate(self._history[self._history_index], add_to_history=False)

    def _go_forward(self):
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self._navigate(self._history[self._history_index], add_to_history=False)

    def _go_up(self):
        # 表示中の最下層(current_path)を1つ上へ。ルート不変で深いカラムが消える。
        parent = str(Path(self._current_path).parent)
        if parent and parent != self._current_path:
            self._select_in_columns(parent)
            # 履歴に積む（↶で元の階層へ戻れるように。従来は積み漏れていた）
            self._history = self._history[:self._history_index + 1]
            self._history.append(parent)
            self._history_index = len(self._history) - 1
            self._sm.add_to_history(parent)

    def navigate_to(self, path: str):
        """Public API – called by bookmark/history panels."""
        self._navigate(path)

    # ------------------------------------------------------------------
    # Drive selector
    # ------------------------------------------------------------------

    def _refresh_drives(self):
        """ドライブ走査を別スレッドで実行（N-1: 死んだNASマッピングで固まらない）。"""
        invalidate_cache()
        self._drive_scanner.scan()

    def _on_drives_ready(self, results):
        current = self._drive_combo.currentData()
        self._drive_combo.blockSignals(True)
        self._drive_combo.clear()
        for root, ok in results:
            self._drive_combo.addItem(root if ok else f"{root} (応答なし)", root)
            if not ok:
                # 応答しないドライブは選択不可にして隔離
                model_item = self._drive_combo.model().item(
                    self._drive_combo.count() - 1)
                if model_item is not None:
                    model_item.setEnabled(False)
        if current:
            for i in range(self._drive_combo.count()):
                if self._drive_combo.itemData(i) == current:
                    self._drive_combo.setCurrentIndex(i)
                    break
        self._drive_combo.blockSignals(False)

    def _on_drive_changed(self, drive: str):
        root = self._drive_combo.currentData() or drive
        if root:
            self._navigate(root)  # 到達確認は _navigate 側で非同期に行う

    def _sync_drive_combo(self, path: str):
        """アドレスのドライブレターに合わせてドライブセレクタの選択を同期する。"""
        drive = os.path.splitdrive(path)[0]
        if not drive:
            return
        root = drive + os.sep
        combo = self._drive_combo
        combo.blockSignals(True)
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and os.path.normcase(os.path.normpath(str(data))) == \
                    os.path.normcase(os.path.normpath(root)):
                combo.setCurrentIndex(i)
                break
        combo.blockSignals(False)

    # ------------------------------------------------------------------
    # View mode / sort / filter
    # ------------------------------------------------------------------

    def _on_view_mode_changed(self, idx: int):
        self._column_view.setVisible(idx == 0)
        self._thumb_view.setVisible(idx in (1, 2))
        if idx == 1:  # List
            self._thumb_view.setViewMode(QListView.ListMode)
        elif idx == 2:  # Thumbnail
            self._thumb_view.setViewMode(QListView.IconMode)

    def _on_sort_changed(self, idx: int):
        col_map = {0: 0, 1: 2, 2: 3}  # name, type (kind), lastModified
        col = col_map.get(idx, 0)
        self._proxy.sort(col, Qt.AscendingOrder)
        self._sm.set("sort_by", ["name", "type", "timestamp"][idx])

    def _on_filter_changed(self, text: str):
        self._proxy.set_filter_string(text)
        self._sm.set("filter_string", text, save=False)

    # ------------------------------------------------------------------
    # Item interaction
    # ------------------------------------------------------------------

    def _resolve_path(self, proxy_index: QModelIndex) -> str:
        source_index = self._proxy.mapToSource(proxy_index)
        return _safe_file_path(self._fs_model, source_index)

    @staticmethod
    def _is_symlink_or_junction(path: str) -> bool:
        """
        「最終コンポーネント自身がリンクか」だけを判定する。
        symlink(mklink /D)とWindowsジャンクション(mklink /J)の両方に対応。

        注意: realpath比較で判定すると、祖先にリンクがある場合に配下の
        通常フォルダまで常にTrueになり、リンク配下へ入る度にビューが
        折りたたまれる。islink / readlink は最終要素のみを見るため安全。
        """
        try:
            if os.path.islink(path):      # symlink（最終要素のみ・ターゲット未接続でもTrue）
                return True
        except OSError:
            pass
        try:
            os.readlink(path)             # ジャンクションも検出。リンクでなければOSError
            return True
        except OSError:
            return False

    def _follow_link(self, path: str):
        """
        リンク(symlink/ジャンクション)を実体ドライブへ飛ばさず、
        リンクのパス(例: C:\\...\\MM-SA)のまま中身を表示する。
        通常フォルダのネイティブ列展開ではプロキシが子を返さないため、
        リンクは setRootIndex 方式の _navigate で開く（パスは維持される）。
        リンク先が未接続で到達不能な場合は _navigate 側がステータスに通知する。
        """
        self._navigate(path)

    def _on_item_clicked(self, proxy_index: QModelIndex):
        # 通常クリックで下階層へ進んでも、複数選択の平坦結果は維持する。
        # 下位カラムで Ctrl/Shift 選択された時だけ _multi_select から対象を絞り直す。
        self._merge_panel.hide()
        path = self._resolve_path(proxy_index)
        # リンク(symlink/ジャンクション)を最優先で処理。
        # リンク先が未接続ドライブ等だと os.path.isdir が False になり、
        # ファイル扱いで「開く」が誤発火するため isdir 判定より前に捌く。
        if self._is_symlink_or_junction(path):
            self._follow_link(path)
            return
        # Windows .lnk / .url ショートカット → 参照先をドライブ最上位から全カラム再表示
        if self._maybe_follow_shortcut(path):
            return
        if os.path.isdir(path):
            if self._column_view.isVisible() and self._is_native_expandable(proxy_index, path):
                # 通常フォルダ(クリックしたカラムの子): ネイティブ列展開に任せルート不変
                _mfm_log("click dir: native expand path=%r" % path)
                self._set_current_path(path)
            else:
                # 別ブランチへのジャンプ(ショートカット先など) → トップから全カラム再構築
                _mfm_log("click dir: cross-branch -> _navigate path=%r current=%r"
                         % (path, self._current_path))
                self._navigate(path)
            if self._flat_col.isVisible():
                fv = self._column_view._flatten_view
                if fv is not None and getattr(fv, "_mfm_flatten", False):
                    # 平坦トグルON中は閉じずに、クリックしたフォルダへ追従
                    # （単一選択でも配下の階層を平坦で見続けたい、の仕様）
                    self._on_flat_request([path])
                else:
                    # 通常クリック＝単一選択（標準挙動）。複数選択の平坦カラムは
                    # 閉じて、通常のカラム展開に戻す。
                    self._on_flat_request([])
            return
        # 単一ファイル選択: パス欄にファイル名まで表示する
        try:
            self._addr_bar.setText(path)
        except Exception:
            pass
        # r97: 圧縮ファイルは «フォルダのように» 中身カラムを開く
        # （中身の操作範囲はエクスプローラーと同じ＝読み取り専用）
        from core.archive_browse import is_archive as _is_archive
        if _is_archive(path):
            self._on_archive_request(path)
            self._sync_quick_look(path)
            self.selection_changed.emit([path])
            return
        self._on_archive_request("")
        action = self._sm.get("single_click_action", "open")
        if action == "preview":
            action = "open"   # 「プレビュー」は「開く」へ統合（旧設定の移行）
        self._dispatch_action(action, path)
        self._sync_quick_look(path)
        self.selection_changed.emit([path])

    def _on_item_activated(self, proxy_index: QModelIndex):
        path = self._resolve_path(proxy_index)
        if self._is_symlink_or_junction(path):
            self._follow_link(path)
            return
        # Windows .lnk / .url ショートカット → 参照先をドライブ最上位から全カラム再表示
        if self._maybe_follow_shortcut(path):
            return
        if os.path.isdir(path):
            if self._column_view.isVisible() and self._is_native_expandable(proxy_index, path):
                self._set_current_path(path)  # カラム展開を維持（ルート不変）
                # r72: 子カラムの生成は本体 current に依存する。実機の
                # ダブルクリックは直前のプレスで current が動くが、QTest 実装
                # （Qt6 の QTest::mouseDClick は press を伴わない）や経路によって
                # は動かないため、ここで確実に current を合わせる（フォルダのみ。
                # ファイルは列スライド防止のため触らない）。
                try:
                    if self._column_view.currentIndex() != proxy_index:
                        self._column_view.setCurrentIndex(proxy_index)
                except Exception:
                    pass
            else:
                self._navigate(path)  # 別ブランチへのジャンプはトップから全カラム再構築
            return
        # ダブルクリックは関連付けアプリ（OS既定）で開く
        open_with_default_app(path)

    def _is_native_expandable(self, proxy_index, path: str) -> bool:
        """クリックした項目の «実際の親カラム» のフォルダが、解決後パスの親と
        一致すればネイティブ列展開でそのまま表示できる（ルート不変・リセット無し）。
        不一致＝ジャンクション/ショートカットで別ブランチに解決された場合のみ
        トップから再構築する。

        従来は self._current_path の祖先で判定していたが、複数選択時は
        current_path が表示中カラムとズレてしまい、通常クリックまで誤って
        cross-branch 扱い＝全カラムリセットになっていた（その回帰修正）。"""
        try:
            pidx = proxy_index.parent() if proxy_index is not None else None
            if pidx is not None and pidx.isValid():
                parent_path = self._resolve_path(pidx)
                if parent_path:
                    return (os.path.normcase(os.path.normpath(parent_path))
                            == os.path.normcase(os.path.normpath(os.path.dirname(path))))
        except Exception:
            pass
        # 親インデックスが取れない（ルート直下等）→ 通常クリック扱いでネイティブ
        return True

    def _maybe_follow_shortcut(self, path: str) -> bool:
        """Windowsショートカット(.lnk/.url)なら参照先を解決して移動する。
        解決先がフォルダ/ファイルどちらでも _navigate がドライブ最上位から
        全カラムで再表示する（ファイルは親カラム上で選択表示）。"""
        low = (path or "").lower()
        if not (low.endswith(".lnk") or low.endswith(".url")):
            _mfm_log("follow_shortcut: 非ショートカット path=%r" % path)
            return False
        target = resolve_windows_shortcut(path)
        _mfm_log("follow_shortcut: path=%r -> target=%r exists=%s"
                 % (path, target, (os.path.exists(target) if target else None)))
        if target and os.path.exists(target):
            self.status_message.emit(f"ショートカット解決: {path} → {target}")
            self._navigate(target)
            return True
        if target:
            self.status_message.emit(f"ショートカット参照先が見つかりません: {target}")
        return False

    def _dispatch_action(self, action: str, path: str):
        if action == "none":
            return   # クリック動作「None」= 何もしない
        if action == "open" and self._on_open:
            self._on_open(path)
        elif action == "import" and self._on_import:
            self._on_import(path)
        elif action == "reference" and self._on_reference:
            self._on_reference(path)
        else:
            self.file_activated.emit(path)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _get_selected_paths(self) -> List[str]:
        active_view = self._column_view if self._column_view.isVisible() else self._thumb_view
        return [self._resolve_path(idx) for idx in active_view.selectedIndexes()
                if idx.column() == 0]

    # ------------------------------------------------------------------
    # 複数選択＋マージ表示
    # ------------------------------------------------------------------
    def _inline_column_base_width(self, total: int, exclude_norm=None) -> int:
        """右隣ペインを «次のカラム» に見せるため、既存カラム実幅に寄せる。

        QColumnView は広い表示領域を持つと末尾に空白を残す。平坦/統合ビューを
        Splitter の右側に置く場合でも、左側を実カラム幅へ詰めれば視覚的には
        選択列の直後に続く。
        """
        main_w = 0
        try:
            # 内部の columnWidths() は幻の予約エントリを含むことがあるため、
            # 実際に見えているカラムウィジェットの幅を合計する。
            # exclude_norm（間もなく畳まれる冗長子カラムのルート群）は除外し、
            # 後からの再リサイズ（揺れの原因）を不要にする
            for v in self._column_view.findChildren(QListView):
                if not (v.isVisible() and v.model() is not None and v.width() > 1):
                    continue
                if exclude_norm:
                    fp = self._column_view._path_for_index(v.rootIndex())
                    if fp and os.path.normcase(os.path.normpath(fp)) in exclude_norm:
                        continue
                main_w += v.width()
        except Exception:
            main_w = 0
        if main_w <= 0:
            try:
                widths = list(self._column_view.columnWidths() or [])
                main_w = sum(int(w) for w in widths if int(w) > 1)
            except Exception:
                main_w = 0
        if main_w <= 0:
            main_w = int(total * 0.60)
        # 実カラム幅ちょうどへ寄せて隙間を作らない（下限の強制は廃止。
        # 広いモニターで total*0.42 を強制すると巨大な空白が出る＝動画の症状）。
        return min(main_w + 4, int(total * 0.72))

    def _set_inline_panel_sizes(self, inline_index: int, preferred_width: int,
                                exclude_norm=None):
        """Splitter上の補助ビューを、カラムビュー直後へ詰めて表示する。"""
        try:
            total = max(self._view_stack.width(), 700)
            main_w = self._inline_column_base_width(total, exclude_norm)
            inline_w = min(max(int(preferred_width), 220), total - main_w)
            if inline_w < 220:
                inline_w = min(260, int(total * 0.35))
                main_w = max(240, total - inline_w)
            # 平坦カラムは «次のカラム» らしくカラム幅程度にし、
            # 余りは末尾スペーサー（空き背景）へ渡す
            inline_w = max(240, min(int(preferred_width), max(240, total - main_w)))
            sizes = [0] * self._view_stack.count()
            sizes[0] = main_w
            used = main_w
            # 共通フォルダのドリルダウン・カラムにも1本ずつ幅を配る
            for c in getattr(self, "_common_cols", []):
                try:
                    ci = self._view_stack.indexOf(c)
                    if ci >= 0 and c.isVisible():
                        sizes[ci] = 190
                        used += 190
                except Exception:
                    pass
            sizes[inline_index] = inline_w
            used += inline_w
            try:
                sp = self._view_stack.indexOf(self._flat_spacer)
                rest = max(0, total - used)
                if sp >= 0:
                    self._flat_spacer.setVisible(rest > 0)
                    sizes[sp] = rest
            except Exception:
                pass
            self._view_stack.setSizes(sizes)
            # 注意: ここで hbar を右端へ送る処理は行わない。視点が飛んで
            # 「元々のターゲットを見失う」ため、スクロール位置の調整は
            # _anchor_column_x / _restore_anchor_column_x（操作カラム固定）に任せる
        except Exception:
            pass

    def _align_columns_right_edge(self):
        """最後の«実幅»カラムの右端をビューポート右端へ合わせる。

        単純に hbar を最大へ送ると、幅0に畳んだ抑制カラムや QColumnView が
        内部予約する余白まで見えてしまい、選択列と平坦カラムの間に
        «大きな隙間» が出る（動画指摘の症状）。実幅カラムの右端で止める。"""
        try:
            cv = self._column_view
            vp = cv.viewport()
            hb = cv.horizontalScrollBar()
            if hb is None:
                return
            right = None
            for v in cv.findChildren(QListView):
                if not v.isVisible() or v.model() is None or v.width() <= 1:
                    continue
                edge = v.x() + v.width()   # viewport 座標系
                right = edge if right is None else max(right, edge)
            if right is None:
                hb.setValue(hb.maximum())
                return
            target = hb.value() + (right - vp.width())
            hb.setValue(max(0, min(int(target), hb.maximum())))
        except Exception:
            try:
                hb = self._column_view.horizontalScrollBar()
                if hb is not None:
                    hb.setValue(hb.maximum())
            except Exception:
                pass

    def _request_flat_integration_status(self, cap: int = 300):
        """平坦ビューに並ぶファイルのフォルダの連携状態をワーカーへ要求（r72）。
        従来は createColumn（カラム表示）でしか要求しておらず、平坦ビューの
        右クリックに Perforce の Revert 等が出なかった。フォルダ数は cap で
        打ち切る（判定は manager のワーカー直列、UI はキュー投入のみ）。"""
        try:
            fc = self._flat_col
            dirs = []
            seen = set()
            for p in fc.all_paths():
                d = os.path.dirname(p)
                k = os.path.normcase(d)
                if k not in seen:
                    seen.add(k)
                    dirs.append(d)
                    if len(dirs) >= cap:
                        break
            mgr = self._integrations()
            for d in dirs:
                mgr.request_status(d)
        except Exception as e:
            _mfm_log("flat integration status error: %r" % (e,))

    def _apply_item_size_to_views(self, px: int, mode: str = None):
        """カラム以外のビュー（独立サムネビュー・平坦カラム）へも表示サイズを配る。

        スライダーは «全体的な表示サイズ» を変えるもの、というユーザー指示（r102）。"""
        px = max(8, int(px))
        if mode in (None, "list"):
            # 平坦カラムは常にリスト表示なので «リストの寸法» だけを反映する
            try:
                self._flat_col._view.setIconSize(QSize(px, px))
                self._flat_col._view.viewport().update()
            except Exception:
                pass
        if mode not in (None, "thumb"):
            return
        try:
            self._thumb_view.setIconSize(QSize(px, px))
            self._thumb_view.setGridSize(
                QSize(px + 18, px + ThumbnailDelegate._TEXT_H + 8))
            if self._thumb_mgr is not None:
                self._thumb_delegate = ThumbnailDelegate(
                    self._thumb_mgr, px, self._thumb_view,
                    is_expanded_index=self._column_view._is_expanded_index)
                self._thumb_view.setItemDelegate(self._thumb_delegate)
            self._thumb_view.doItemsLayout()
            self._thumb_view.viewport().update()
        except Exception as e:
            _mfm_log("apply item size to views error: %r" % (e,))

    def _on_archive_request(self, path):
        """圧縮ファイルの中身カラムの表示／非表示（r97）。

        path が書庫ならその中身を «次のカラム» として出す。空文字・書庫でない・
        読めない場合は閉じる。書庫の中身は読み取り専用（エクスプローラー同様）。"""
        from core.archive_browse import is_archive as _is_archive
        try:
            if not path or not _is_archive(path):
                self._archive_col.hide()
                return
            if self._archive_col.archive_path() == path and \
                    self._archive_col.isVisible():
                return                      # 同じ書庫を開き直さない
            if not self._archive_col.set_archive(path):
                self._archive_col.hide()
                return
            self._merge_panel.hide()
            self._archive_col.show()
            col_w = 320
            for v in self._column_view.findChildren(QListView):
                if v.isVisible() and v.model() is not None and v.width() > 1:
                    col_w = max(260, v.width() + 20)
            self._set_inline_panel_sizes(
                self._view_stack.indexOf(self._archive_col), col_w)
            _mfm_log("archive: %s visible=%s"
                     % (os.path.basename(path), self._archive_col.isVisible()))
        except Exception as e:
            _mfm_log("archive request error: %r" % (e,))
            try:
                self._archive_col.hide()
            except Exception:
                pass

    def _on_flat_request(self, dirs):
        """平坦カラムの表示要求。dirs があれば選択カラムの直後へ詰めて表示する。"""
        dirs = [d for d in (dirs or []) if d and os.path.isdir(d)]
        if dirs:
            self._merge_panel.hide()
            self._on_archive_request("")        # r97: 書庫カラムとは排他
            self._flat_col.set_sources(dirs)
            self._request_flat_integration_status()
            self._flat_col.show()
            # «次のカラム» らしく、実カラムに近い幅で出す
            col_w = 320
            try:
                for v in self._column_view.findChildren(QListView):
                    if v.isVisible() and v.model() is not None and v.width() > 1:
                        col_w = max(260, v.width() + 20)
            except Exception:
                pass
            # リサイズは1回だけ行う（多段リサイズは視点が飛ぶ＝揺れの原因）。
            # 間もなく畳まれる冗長子カラム（選択フォルダ自身のカラム）は
            # 幅計算から除外して、後からの再詰めを不要にする
            excl = {os.path.normcase(os.path.normpath(d)) for d in dirs}
            # 共通フォルダのドリルダウン・カラムを（あれば）構築
            self._rebuild_common_columns(0, dirs)
            self._set_inline_panel_sizes(
                self._view_stack.indexOf(self._flat_col), col_w, exclude_norm=excl)
            _mfm_log("flat_request: dirs=%d visible=%s rows=%s w=%s"
                     % (len(dirs), self._flat_col.isVisible(),
                        self._flat_col._proxy.rowCount(), self._flat_col.width()))
            # 結果をステータスバーに必ず出す（動作したかどうかを画面上で判断できる）
            self.status_message.emit(
                "平坦表示: %d フォルダ → %d ファイル"
                % (len(dirs), self._flat_col._proxy.rowCount()))
            # 複数選択時: パス欄は «選択の直前のディレクトリ» まで表示
            if len(dirs) >= 2:
                try:
                    self._addr_bar.setText(os.path.dirname(dirs[0]))
                except Exception:
                    pass
        else:
            self._flat_col.hide()
            self._rebuild_common_columns(0, [])
            try:
                self._flat_spacer.hide()
            except Exception:
                pass
            # 平坦トグルの状態を表示と同期（ONのまま非表示を防ぐ）
            try:
                self._column_view._reset_flatten_toggle()
            except Exception:
                pass
            # 選択が無くなった時: パス欄を現在のディレクトリへ戻す
            try:
                self._addr_bar.setText(self._current_path or "")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 共通フォルダのドリルダウン（複数選択のフィルタリング）
    # ------------------------------------------------------------------

    def _rebuild_common_columns(self, level, sources):
        """level 番目以降の共通フォルダカラムを sources から再構築する。
        共通名（2ソース以上に存在する同名フォルダ）が無ければ打ち切り＝再帰終端。"""
        for w in self._common_cols[level:]:
            try:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            except Exception:
                pass
        del self._common_cols[level:]
        del self._common_srcs[level:]
        sources = [s for s in (sources or []) if os.path.isdir(s)]
        # レベル0（最初のカラム）は複数選択時のみ。深い階層は単一ソースでも掘れる
        if not sources or (level == 0 and len(sources) < 2):
            return
        try:
            from core.merge_browse import merge_children
            merged, _files = merge_children(sources)
        except Exception:
            merged = []
        # 重複していない（1ソースにしか無い）フォルダも含めて全てリストする。
        # 同名フォルダは1項目に統合（sources に各実パスを保持）
        common = list(merged)
        if not common:
            return
        from ui.common_columns import CommonFolderColumn
        col = CommonFolderColumn(level, self)
        col.set_entries(common)
        col.selection_changed.connect(
            lambda lv=level: self._on_common_selection(lv))
        # 平坦化の深さ（全階層/直下のみ）: 設定値で初期化し、切替は全カラム連動
        col.set_recursive(bool(self._sm.get("flat_recursive", True)))
        col.depth_mode_changed.connect(self._on_flat_depth_changed)
        idx = self._view_stack.indexOf(self._flat_col)
        self._view_stack.insertWidget(idx, col)
        col.show()
        self._common_cols.append(col)
        self._common_srcs.append(list(sources))

    def _on_flat_depth_changed(self, recursive: bool):
        """緑バーのスイッチ: 平坦表示を「全階層」⇔「選択フォルダ直下のみ」で切替。
        全ての子フォルダカラムのスイッチを同期し、平坦一覧を作り直す。"""
        recursive = bool(recursive)
        self._sm.set("flat_recursive", recursive)
        for c in self._common_cols:
            try:
                c.set_recursive(recursive)
            except Exception:
                pass
        try:
            self._flat_col.set_recursive(recursive)
        except Exception as e:
            _mfm_log("flat depth change error: %r" % (e,))

    def _on_common_selection(self, level):
        """共通フォルダカラムの選択変更 → 平坦ビューを絞り込み、次の階層を再帰構築。"""
        if level >= len(self._common_cols):
            return
        col = self._common_cols[level]
        sel = col.selected_sources()
        eff = sel if sel else list(self._common_srcs[level])
        # 平坦ビューを絞り込み（選択なし＝この階層の元ソース全体へ戻す）
        try:
            self._flat_col.set_sources(eff)
            self._request_flat_integration_status()
        except Exception:
            pass
        # 選択がある時のみ、さらに深い共通階層を掘る
        self._rebuild_common_columns(level + 1, sel if sel else [])
        col_w = 320
        excl = {os.path.normcase(os.path.normpath(d)) for d in eff}
        self._set_inline_panel_sizes(
            self._view_stack.indexOf(self._flat_col), col_w, exclude_norm=excl)

    def _show_inline_merge(self, dirs):
        """旧マージ入口。結果表示はツリーにせず、必ず平坦結果へ送る。"""
        self._on_flat_request(dirs)

    def _start_merge(self, dirs, mode="tree"):
        """旧マージ専用ビュー入口。現在は結果を常に平坦表示へ送る。"""
        self._on_flat_request(dirs)

    def _exit_merge(self):
        """マージ表示を閉じ、通常のカラムビューへ戻る。"""
        self._merge_panel.hide()
        self._thumb_view.hide()
        self._column_view.show()
        self.status_message.emit(self._current_path or "")

    def _show_context_menu(self, pos: QPoint):
        paths = self._get_selected_paths()
        sender = self.sender()
        # Explorer準拠の対象決定。各カラムの選択モデルは本体と別物のため、
        # 右クリック時点で本体側の選択が同期済みとは限らない。従来は
        # 「本体の選択リスト」をそのまま使っており、.ma を右クリックしても
        # 対象が古い選択やパンくず（祖先フォルダ）になり、Mayaメニューが
        # 出たり出なかったりした。
        # カーソル直下の項目は «カラム（子QListView）の座標系» で解決する。
        # QColumnView.indexAt() は各カラムのヘッダ分のビューポートマージン
        # （_COL_HEADER_H=52px）を考慮せず、1〜2行ズレた項目/無効を返すため、
        # 「.ma を右クリックしてもMayaメニューが出ない（再選択で出る）」の
        # 原因になっていた（r34の実装ミス、2026-09-07）。
        cursor_path = ""
        column_dir = ""          # r70: 空白を右クリックした時のカラムのフォルダ
        gpos = (sender.viewport().mapToGlobal(pos)
                if hasattr(sender, "viewport") else self.mapToGlobal(pos))
        try:
            # グローバル座標を含むビューポートを持つカラムを探し、そのカラムの
            # 座標系で indexAt する（カーソル位置APIに依存しない決定的な方法）
            lv_hit = None
            for lv in self._column_view.findChildren(QListView):
                if not lv.isVisible() or lv.model() is None:
                    continue
                vp = lv.viewport()
                top_left = vp.mapToGlobal(QPoint(0, 0))
                if (top_left.x() <= gpos.x() < top_left.x() + vp.width()
                        and top_left.y() <= gpos.y() < top_left.y() + vp.height()):
                    lv_hit = lv
                    break
            if lv_hit is not None:
                idx = lv_hit.indexAt(lv_hit.viewport().mapFromGlobal(gpos))
                if idx.isValid():
                    cursor_path = self._resolve_path(idx)
                else:
                    root = lv_hit.rootIndex()
                    if root.isValid():
                        column_dir = self._resolve_path(root)
            elif hasattr(sender, "indexAt"):
                idx = sender.indexAt(pos)
                if idx.isValid():
                    cursor_path = self._resolve_path(idx)
        except Exception:
            cursor_path = ""
        if cursor_path:
            if cursor_path not in paths:
                # 未選択の項目を右クリック → その項目だけを対象にする
                paths = [cursor_path]
            else:
                # 選択済み項目を右クリック → 選択全体を対象にするが、
                # パンくず（他の選択の祖先フォルダ）は対象から外す
                # （_drop_ancestor_dirs は CappedColumnView 側の静的メソッド）
                paths = CappedColumnView._drop_ancestor_dirs(paths)
        elif column_dir:
            # r70: カラムの空白を右クリック → そのフォルダのメニュー
            # （DCC からの保存／書き出し・貼り付け）。右ボタンのプレスで
            # カラムの選択は解除済み（r55）なので本体の選択は使わない。
            self._popup_folder_context_menu(column_dir, gpos)
            return
        if not paths:
            return
        self._popup_context_menu(paths, gpos)

    # ── r70: DCC からの保存／書き出し ─────────────────────────────────
    def _add_dcc_save_actions(self, menu, target: str, exts_hint=None):
        """«シーンを保存»«選択を書き出し» を «1つの DCC 分だけ» メニューへ
        （ユーザー指示: 4つに分けず、ヘッダで選択中の DCC を出す）。
        DCC の決定は dcc_for_path: .ma/.mb → Maya、.blend → Blender、
        フォルダ／共通形式 → 選択中の DCC（_dcc_target_provider）。
        target はフォルダ（空白右クリック）または単一ファイル（ダイアログの
        ファイル名欄に入る）。実行は _dcc_callback(app, action, [target])
        → MainWindow 側でダイアログ→DCC へ送信。"""
        from core.i18n import tr
        from core import dcc_save
        from core.file_operations import dcc_for_path
        dcc_cb = getattr(self, "_dcc_callback", None)
        if not callable(dcc_cb) or not target:
            return False
        ext = "" if os.path.isdir(target) else Path(target).suffix.lower()
        app = self.current_dcc_target()
        if ext:
            app = dcc_for_path(target, app)
            if ext not in dcc_save.exts_for(app):
                return False
        label = "Blender" if app == "blender" else "Maya"
        a = menu.addAction(tr("💾  %s: シーンをここに保存...", "💾  %s: Save Scene here...") % label)
        a.triggered.connect(lambda _c=False, ap=app: dcc_cb(ap, "save_scene", [target]))
        a = menu.addAction(tr("📤  %s: 選択を書き出し...", "📤  %s: Export Selection...") % label)
        a.triggered.connect(lambda _c=False, ap=app: dcc_cb(ap, "export_selection", [target]))
        return True

    def current_dcc_target(self) -> str:
        """ヘッダのスイッチで選択中の DCC（MainWindow が provider を登録。既定 maya）。"""
        fn = getattr(self, "_dcc_target_provider", None)
        try:
            v = fn() if callable(fn) else None
        except Exception:
            v = None
        return "blender" if v == "blender" else "maya"

    def set_dcc_target_provider(self, fn):
        self._dcc_target_provider = fn

    def _popup_folder_context_menu(self, folder: str, global_pos):
        """カラムの空白（項目の無い所）の右クリックメニュー（r70）。"""
        from core.i18n import tr
        menu = QMenu(self)
        # r73: 新規作成（作成後はそのまま名前変更モード）
        nf = menu.addAction(tr("📁  新規フォルダ", "📁  New folder") + "\tCtrl+Shift+N")
        nf.triggered.connect(lambda _c=False: self._create_new_item("folder", folder))
        nt = menu.addAction(tr("📄  新規テキスト ドキュメント", "📄  New text document") + "\tCtrl+Shift+T")
        nt.triggered.connect(lambda _c=False: self._create_new_item("text", folder))
        menu.addSeparator()
        if self._add_dcc_save_actions(menu, folder):
            menu.addSeparator()
        paste_act = menu.addAction(tr("📋  貼り付け", "📋  Paste") + "\tCtrl+V")
        mime = QApplication.clipboard().mimeData()
        paste_act.setEnabled(bool(mime and mime.hasUrls()))
        paste_act.triggered.connect(lambda _c=False: self._clipboard_paste_into(folder))
        menu.addSeparator()
        reveal_act = menu.addAction(tr("📁  エクスプローラーで表示", "📁  Show in Explorer"))
        reveal_act.triggered.connect(lambda _c=False: reveal_in_explorer(folder))
        copy_path_act = menu.addAction(tr("📋  フォルダパスをコピー", "📋  Copy folder path"))
        copy_path_act.triggered.connect(lambda _c=False: self._copy_paths_to_clipboard([folder]))
        try:
            menu.exec_(global_pos)
        except AttributeError:
            menu.exec(global_pos)

    def _clipboard_paste_into(self, folder: str):
        """右クリックしたカラムのフォルダへ貼り付け（_clipboard_paste の貼り付け先を差し替え）。"""
        self._paste_override_dir = folder
        try:
            self._clipboard_paste()
        finally:
            self._paste_override_dir = None

    def _run_integration_action(self, cb, extra=None, label="", paths=None):
        """連携操作の実行（外部GUIの起動は即返る／CLIは内部でワーカー化済み）。
        r58: extra["confirm"] があれば実行前に Yes/No 確認。結果は manager の
        action_finished（→ _on_integration_action_finished）で通知される。
        数秒後に状態を再取得して表示へ反映する。"""
        from core.i18n import tr
        extra = extra or {}
        if extra.get("confirm"):
            ret = QMessageBox.question(
                self, "⎇ " + (label or tr("連携", "Integration")), extra["confirm"],
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        try:
            cb()
        except Exception as e:
            _mfm_log("integration action error: %r" % (e,))
        try:
            # r72: 対象ファイルのフォルダ全部（平坦ビューは複数階層）を再取得
            targets = list(paths or []) + [self._current_path]
            QTimer.singleShot(3000, lambda: self._refresh_integration_status(targets))
        except Exception:
            pass


    def _refresh_integration_status(self, paths):
        try:
            from core.integrations import get_manager
            mgr = get_manager()
            dirs = set()
            for p in paths or []:
                if p:
                    dirs.add(os.path.dirname(p))
                    dirs.add(p)
            for d in dirs:
                mgr.refresh(d)
        except Exception as e:
            _mfm_log("integration refresh error: %r" % (e,))

    def _show_flat_context_menu(self, pos: QPoint):
        """平坦ビューの右クリックメニュー（通常カラムと同仕様）。"""
        try:
            paths = self._flat_col.selected_paths()
        except Exception:
            paths = []
        if not paths:
            return
        gpos = self._flat_col._view.viewport().mapToGlobal(pos)
        self._popup_context_menu(paths, gpos)

    def _popup_context_menu(self, paths, global_pos):
        from core.i18n import tr
        menu = QMenu(self)
        is_maya = all(Path(p).suffix.lower() in MAYA_EXTENSIONS for p in paths)
        is_single = len(paths) == 1
        is_dir = is_single and os.path.isdir(paths[0])

        # ── 複数フォルダの平坦結果表示（2つ以上フォルダ選択時） ─────────
        sel_dirs = [p for p in paths if os.path.isdir(p)]
        if len(sel_dirs) >= 2:
            flat_act = menu.addAction(tr("▤  選択フォルダを平坦結果表示",
                                         "▤  Show selection as flat list"))
            flat_act.triggered.connect(lambda: self._on_flat_request(sel_dirs))
            menu.addSeparator()

        # ── DCC actions（Maya / Blender、r65） ─────────────────────────
        # 送り先を明示するため _dcc_callback(app, action, paths) を使う。
        # 「開く」はネイティブ形式かつ単一のみ。インポート/リファレンス(リンク)は
        # 複数対応（リファレンスはデフォルトNamespaceでダイアログ無し）。
        from core.file_operations import (BLENDER_EXTENSIONS)
        exts = {Path(p).suffix.lower() for p in paths}
        dcc_cb = getattr(self, "_dcc_callback", None)
        n = len(paths)
        cnt = ("（%d 件）" % n) if n > 1 else ""
        cnt_en = (" (%d files)" % n) if n > 1 else ""
        added_dcc = False
        # r80: 交換形式（.fbx/.obj/.abc/.usd…）はヘッダで選択中の DCC 分だけ出す。
        # ネイティブ形式（.ma/.mb → Maya、.blend → Blender）は選択中 DCC に関係なく出す。
        # 以前は交換形式で Maya と Blender の両方が並んでいた（仕様外）。
        dcc_target = self.current_dcc_target()
        show_maya = bool(exts & MAYA_EXTENSIONS) or dcc_target == "maya"
        show_blender = bool(exts & BLENDER_EXTENSIONS) or dcc_target == "blender"
        # r91: 各項目は «そのコマンドの対象形式» のときだけ出す（core/dcc_caps.py）。
        # 送信側（MainWindow._dcc_accepts）と同じ表なので «見えるのに送れない» は無い。
        from core import dcc_caps
        if callable(dcc_cb) and exts and show_maya:
            m_added = False
            if is_single and exts <= dcc_caps.exts("maya", "open"):
                a = menu.addAction(tr("🗂  Maya で開く", "🗂  Open in Maya"))
                a.triggered.connect(lambda _c=False: dcc_cb("maya", "open", list(paths)))
                m_added = True
            if exts <= dcc_caps.exts("maya", "import"):
                a = menu.addAction(tr("⬇  Maya にインポート" + cnt, "⬇  Import into Maya" + cnt_en))
                a.triggered.connect(lambda _c=False: dcc_cb("maya", "import", list(paths)))
                m_added = True
            if exts <= dcc_caps.exts("maya", "reference"):
                a = menu.addAction(tr("🔗  Maya にリファレンス" + cnt,
                                      "🔗  Reference into Maya" + cnt_en))
                # 単一は Namespace を確認（従来どおり）。複数は一括（ダイアログ無し）
                ref_action = "reference_ask" if is_single else "reference"
                a.triggered.connect(lambda _c=False: dcc_cb("maya", ref_action, list(paths)))
                m_added = True
            added_dcc = added_dcc or m_added
        if callable(dcc_cb) and exts and show_blender:
            b_items = []
            if is_single and exts <= dcc_caps.exts("blender", "open"):
                b_items.append((tr("🗂  Blender で開く", "🗂  Open in Blender"), "open"))
            if exts <= dcc_caps.exts("blender", "import"):
                b_items.append((tr("⬇  Blender にインポート（Append）" + cnt,
                                   "⬇  Import into Blender (Append)" + cnt_en), "import"))
            if exts <= dcc_caps.exts("blender", "reference"):
                b_items.append((tr("🔗  Blender にリンク（Link）" + cnt,
                                   "🔗  Link into Blender" + cnt_en), "reference"))
            if b_items and added_dcc:
                menu.addSeparator()
            for text, act in b_items:
                a = menu.addAction(text)
                a.triggered.connect(lambda _c=False, ac=act: dcc_cb("blender", ac, list(paths)))
            added_dcc = added_dcc or bool(b_items)
        if added_dcc:
            menu.addSeparator()
        # r70: 単一のシーン/書き出し形式ファイル → その名前で保存／書き出し
        if is_single and not is_dir and self._add_dcc_save_actions(menu, paths[0]):
            menu.addSeparator()

        # ── General actions ───────────────────────────────────────────
        if is_single:
            open_ext_act = menu.addAction(tr("🖥  関連付けアプリで開く",
                                             "🖥  Open with default app"))
            open_ext_act.triggered.connect(lambda: open_with_default_app(paths[0]))

            reveal_act = menu.addAction(tr("📁  エクスプローラーで表示",
                                           "📁  Show in Explorer"))
            reveal_act.triggered.connect(lambda: reveal_in_explorer(paths[0]))
            menu.addSeparator()

        # ── 外部サービス連携（該当ワークスペースのプロバイダだけ表示） ────
        try:
            from core.integrations import get_manager
            mgr = get_manager()
            groups = mgr.actions_for(paths) if mgr.enabled else []
        except Exception:
            groups = []
        if groups:
            for label, acts in groups:
                sub = menu.addMenu("⎇  " + label)
                sub.setToolTipsVisible(True)
                for item in acts:
                    text, cb = item[0], item[1]
                    extra = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
                    if text is None:
                        sub.addSeparator()
                        continue
                    a = sub.addAction(text)
                    # r58: 事前チェック（実行不可＝灰色＋理由 / 確認文）
                    if extra.get("enabled") is False:
                        a.setEnabled(False)
                        if extra.get("tooltip"):
                            a.setToolTip(extra["tooltip"])
                            a.setText(text + tr("（不可）", " (unavailable)"))
                        continue
                    a.triggered.connect(
                        lambda _c=False, f=cb, e=extra, l=label, ps=list(paths):
                        self._run_integration_action(f, e, l, ps))
            refresh_act = menu.addAction(tr("🔄  連携状態を更新",
                                            "🔄  Refresh integration status"))
            refresh_act.triggered.connect(
                lambda: self._refresh_integration_status(paths))
            menu.addSeparator()

        # ── パスをコピー（単数/複数対応） ─────────────────────────────
        copy_path_act = menu.addAction(tr("📋  ファイルパスをコピー",
                                          "📋  Copy file path"))
        copy_path_act.triggered.connect(lambda: self._copy_paths_to_clipboard(paths))
        menu.addSeparator()

        # ── Bookmark ─────────────────────────────────────────────────
        bm_act = menu.addAction(tr("⭐  ブックマークに追加", "⭐  Add to bookmarks"))
        bm_act.triggered.connect(lambda: self._add_to_bookmarks(paths))
        menu.addSeparator()

        # ── File ops ─────────────────────────────────────────────────
        copy_act = menu.addAction(tr("📋  コピー...", "📋  Copy..."))
        copy_act.triggered.connect(lambda: self._copy_dialog(paths))

        move_act = menu.addAction(tr("✂  移動...", "✂  Move..."))
        move_act.triggered.connect(lambda: self._move_dialog(paths))

        dup_act = menu.addAction(tr("⧉  複製...", "⧉  Duplicate...") + "\tCtrl+D")
        dup_act.triggered.connect(lambda _c=False: self._duplicate_selected(list(paths)))

        rename_act = menu.addAction(tr("✏  名前変更", "✏  Rename") + "\tF2")
        rename_act.triggered.connect(
            lambda _c=False: self._rename_inline(paths[0]) if paths else None)
        rename_act.setEnabled(is_single)

        # r101: 選択したものだけをバッチリネームの対象にする
        # （ツールメニューからだと «現在の選択» を拾い直すため、右クリックした
        #  対象と食い違うことがあった）
        batch_act = menu.addAction(tr("✏✏  バッチリネーム...", "✏✏  Batch Rename..."))
        batch_act.triggered.connect(
            lambda _c=False, ps=list(paths): self.batch_rename_requested.emit(ps))

        menu.addSeparator()

        del_act = menu.addAction(tr("🗑  削除", "🗑  Delete"))
        del_act.triggered.connect(lambda: self._delete_confirm(paths))
        del_act.setShortcut("Delete")

        menu.addSeparator()

        # ── Properties ───────────────────────────────────────────────
        if is_single:
            prop_act = menu.addAction(tr("ℹ  プロパティ", "ℹ  Properties"))
            prop_act.triggered.connect(lambda: self._show_properties(paths[0]))

        try:
            menu.exec_(global_pos)
        except AttributeError:
            menu.exec(global_pos)

    # ------------------------------------------------------------------
    # File operations (UI wrappers)
    # ------------------------------------------------------------------

    def _add_to_bookmarks(self, paths: List[str]):
        # 実際の登録は MainWindow 側の BookmarkManager で行う（signalで依頼）
        if paths:
            self.bookmark_requested.emit(list(paths))
            self.status_message.emit(f"ブックマークに追加: {len(paths)} 件")

    def _copy_paths_to_clipboard(self, paths: List[str]):
        """選択中のフルパスをクリップボードへコピー（複数は改行区切り）。"""
        from core.compat import QApplication
        if not paths:
            return
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText("\n".join(paths))
        self.status_message.emit(f"パスをコピー: {len(paths)} 件")

    _DIR_DLG_OPTS = QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog

    def _copy_dialog(self, paths: List[str]):
        dst = QFileDialog.getExistingDirectory(self, "コピー先を選択", self._current_path,
                                               self._DIR_DLG_OPTS)
        if dst:
            self._transfer_with_progress(paths, dst, False, "コピー")

    def _move_dialog(self, paths: List[str]):
        dst = QFileDialog.getExistingDirectory(self, "移動先を選択", self._current_path,
                                               self._DIR_DLG_OPTS)
        if dst:
            self._transfer_with_progress(paths, dst, True, "移動")

    # ── r73: 新規フォルダ / 新規テキスト（Ctrl+Shift+N / Ctrl+Shift+T、空白右クリック） ──
    def _active_column_folder(self) -> Optional[str]:
        """«アクティブなカラム» のフォルダ: フォーカスのあるカラム（子 QListView）の
        rootIndex → 無ければ単一選択フォルダ／現在地（_paste_target_dir と同じ規則）。"""
        try:
            fw = QApplication.focusWidget()
            for lv in self._column_view.findChildren(QListView):
                if fw is None:
                    break
                if lv.isVisible() and lv.rootIndex().isValid() and \
                        (lv is fw or lv.isAncestorOf(fw)):
                    p = self._resolve_path(lv.rootIndex())
                    if p and os.path.isdir(p):
                        return p
        except Exception:
            pass
        return self._paste_target_dir()

    @staticmethod
    def _unique_new_name(folder: str, base: str, ext: str = "") -> str:
        """Explorer 風: «新しいフォルダ» → «新しいフォルダ (2)» …"""
        name = base + ext
        n = 2
        while os.path.lexists(os.path.join(folder, name)):
            name = "%s (%d)%s" % (base, n, ext)
            n += 1
        return name

    def _create_new_item(self, kind: str, folder: str = None):
        """kind: "folder" / "text"。作成後はそのままインライン名前変更モードへ。
        QFileSystemModel が新項目を表示するまで（ウォッチャ経由・非同期）少し
        待ってから _rename_inline を開く。"""
        folder = folder or self._active_column_folder()
        if not folder or not os.path.isdir(folder):
            self.status_message.emit("作成先フォルダを特定できません")
            return
        try:
            if kind == "folder":
                name = self._unique_new_name(folder, "新しいフォルダ")
                path = os.path.join(folder, name)
                os.mkdir(path)
            else:
                name = self._unique_new_name(folder, "新しいテキスト ドキュメント", ".txt")
                path = os.path.join(folder, name)
                with open(path, "x", encoding="utf-8"):
                    pass
        except Exception as e:
            QMessageBox.critical(self, "新規作成", str(e))
            return
        self.status_message.emit("作成: %s" % name)
        self._last_created_path = path
        self._refresh_flat_view()
        self._rename_when_visible(path)

    def _rename_when_visible(self, path: str, tries: int = 25):
        """新規項目がカラムに現れたらインライン名前変更を開く（最大 ~2.5 秒待つ）。"""
        def attempt(left):
            try:
                idx = self._column_view._proxy_index_for_path(path)
                if idx.isValid():
                    views = [v for v in self._column_view.findChildren(QListView)
                             if getattr(v, "_mfm_header", None) is not None and v.isVisible()
                             and v.rootIndex() == idx.parent()
                             and v.visualRect(idx).isValid() and not v.visualRect(idx).isEmpty()]
                    if views:
                        # 見えるように選択してから編集（列はスライドさせない）
                        try:
                            self._column_view._prune_selection_to_single(idx)
                            views[0].scrollTo(idx)
                        except Exception:
                            pass
                        self._rename_inline(path)
                        return
            except Exception as e:
                _mfm_log("rename_when_visible error: %r" % (e,))
            if left > 0:
                QTimer.singleShot(100, lambda: attempt(left - 1))
            else:
                # カラムに出てこない（フィルタ等）→ ダイアログで名前変更
                self._rename_dialog([path])
        attempt(tries)

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
                        except Exception:
                            pass
                        break
            if level <= 1:
                if parent and os.path.isdir(parent):
                    self._fs_model.setRootPath(parent)
                QApplication.processEvents()
                return
            self._recreate_fs_model(parent)
        except Exception as e:
            _mfm_log("release_fs_handles error: %r" % (e,))

    def _recreate_fs_model(self, root_hint: str = ""):
        """QFileSystemModel を作り直して «監視スレッドのハンドル» を完全に手放す。

        Qt には «このディレクトリの監視だけ外す» API が無いため、掴みっぱなしを
        確実に解くにはモデルごと捨てるしかない（r98）。重い操作なので
        リネーム／移動が権限エラーで弾かれた時だけ通る道。"""
        old = self._fs_model
        new = QFileSystemModel()
        try:
            new.setIconProvider(_SafeIconProvider())
        except Exception:
            pass
        new.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Files | QDir.Hidden)
        new.setReadOnly(False)
        for setter, arg in ((new.setResolveSymlinks, False),):
            try:
                setter(arg)
            except Exception:
                pass
        try:
            new.setOption(QFileSystemModel.DontUseCustomDirectoryIcons, True)
        except Exception:
            pass
        try:
            new.fileRenamed.connect(self._on_fs_file_renamed)
            new.directoryLoaded.connect(self._on_fs_dir_loaded)
        except Exception:
            pass
        self._fs_model = new
        try:
            self._proxy.setSourceModel(new)
        except Exception as e:
            _mfm_log("recreate model: setSourceModel failed %r" % (e,))
        try:
            old.directoryLoaded.disconnect()
        except Exception:
            pass
        try:
            old.fileRenamed.disconnect()
        except Exception:
            pass
        try:
            old.setParent(None)
            old.deleteLater()
        except Exception:
            pass
        QApplication.processEvents()
        _mfm_log("fs model recreated (root_hint=%s)" % (root_hint,))

    @staticmethod
    def nearest_existing_dir(path: str) -> str:
        """path から «実在する一番近い親» を返す（r98）。

        Manager で開いているフォルダがリネーム／移動／削除で消えた時の
        行き先。どこまで遡っても無ければ空文字。"""
        p = (path or "").rstrip("/\\")
        seen = set()
        while p and p not in seen:
            seen.add(p)
            if os.path.isdir(p):
                return p
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        return ""

    def _check_current_gone_async(self):
        """現在地が消えていないかを **ワーカースレッドで** 確かめる（r107）。

        stat が返らないパス（切断された共有・同期中の Perforce ワークスペース）
        でも UI を止めない。消えていた時だけ UI スレッドへ移動先を渡す。"""
        cur = self._current_path or ""
        if not cur or self._gone_probe_busy:
            return
        # 進行中のファイル操作・統合コマンドがある時は触らない（無駄な I/O）
        if getattr(self, "_file_op_running", False):
            return
        self._gone_probe_busy = True

        def _work():
            dest = ""
            try:
                if not os.path.isdir(cur):
                    dest = self.nearest_existing_dir(
                        os.path.dirname(cur.rstrip("/\\")) or cur)
            except Exception:
                dest = ""
            try:
                self._gone_result.emit(cur, dest)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True,
                         name="mfm-gone-probe").start()

    def _on_gone_result(self, checked_path: str, dest: str):
        """ワーカーの判定結果を UI スレッドで受ける（r107）。"""
        self._gone_probe_busy = False
        if not dest:
            return
        if (self._current_path or "") != checked_path:
            return          # 判定中に別の場所へ移動していた
        _mfm_log("current path gone(async): %s -> %s" % (checked_path, dest))
        self.status_message.emit(
            "表示中のフォルダが無くなったため、%s へ移動しました" % dest)
        self.navigate_to(dest)

    def _fallback_to_existing_ancestor(self) -> bool:
        """現在地が消えていたら、実在する一番近い親フォルダへ移動する（r98）。"""
        cur = self._current_path or ""
        if not cur or os.path.isdir(cur):
            return False
        dest = self.nearest_existing_dir(os.path.dirname(cur.rstrip("/\\")) or cur)
        if not dest:
            return False
        _mfm_log("current path gone: %s -> %s" % (cur, dest))
        self.status_message.emit(
            "表示中のフォルダが無くなったため、%s へ移動しました" % dest)
        self.navigate_to(dest)
        return True

    def _rename_path(self, src: str, dst: str):
        """リネーム本体（r98）。Windows の «アクセスが拒否されました» は
        監視ハンドル／OneDrive 同期が原因のことが多いので、手放し＋再試行＋
        シェル経由フォールバックまで面倒を見る。"""
        from core.file_operations import rename_path as _rn
        ok, err = _rn(src, dst,
                      release_cb=lambda lv=1: self._release_fs_handles(src, lv))
        if not ok:
            _mfm_warn("rename failed: %s -> %s : %r" % (src, dst, err))
        return ok, err

    @staticmethod
    def _rename_error_text(src: str, err) -> str:
        werr = getattr(err, "winerror", None)
        if werr in (5, 32):
            return ("名前を変更できませんでした（アクセスが拒否されました）。\n\n"
                    "%s\n\n"
                    "このフォルダ／ファイルを他のプログラムが使用中の可能性が"
                    "あります。よくある原因:\n"
                    "・OneDrive が同期中（同期の完了を待つ／一時停止する）\n"
                    "・中のファイルをアプリ（Maya 等）が開いている\n"
                    "・エクスプローラーや端末が同じフォルダを開いている"
                    % (err,))
        return str(err)

    def _select_when_visible(self, path: str, tries: int = 25):
        """path がカラム（または平坦／サムネビュー）に現れたら選択する（r98）。

        QFileSystemModel の反映はウォッチャ経由で非同期なので、出てくるまで
        最大 ~2.5 秒リトライする。インライン編集中は選択を奪わない
        （Tab 送りの連続リネームを壊さないため）。"""
        def attempt(left):
            try:
                if getattr(self, "_inline_editor", None) is not None:
                    return          # 編集中＝別の項目を編集している。触らない
                if self._select_path_now(path):
                    return
            except Exception as e:
                _mfm_log("select_when_visible error: %r" % (e,))
            if left > 0:
                QTimer.singleShot(100, lambda: attempt(left - 1))
        attempt(tries)

    def _select_path_now(self, path: str) -> bool:
        """path を «今» 選択できるなら選択して True。まだ出ていなければ False。"""
        if not path:
            return False
        idx = self._column_view._proxy_index_for_path(path)
        if idx.isValid():
            self._column_view._prune_selection_to_single(idx)
            for v in self._column_view.findChildren(QListView):
                try:
                    if v.isVisible() and v.rootIndex() == idx.parent():
                        v.scrollTo(idx)
                        break
                except Exception:
                    continue
            self.selection_changed.emit([path])
            return True
        # 平坦カラム／サムネビューで見ている場合
        for view in (self._thumb_view, getattr(self._flat_col, "_view", None)):
            if view is None or not view.isVisible():
                continue
            m = view.model()
            if m is None:
                continue
            for row in range(m.rowCount()):
                i = m.index(row, 0)
                p = self._resolve_path(i) if view is self._thumb_view \
                    else i.data(_QtCore.Qt.UserRole + 1)
                if p and os.path.normcase(os.path.abspath(p)) == \
                        os.path.normcase(os.path.abspath(path)):
                    sm = view.selectionModel()
                    if sm is not None:
                        QISM = _QtCore.QItemSelectionModel
                        sm.select(i, QISM.ClearAndSelect | QISM.Rows)
                        sm.setCurrentIndex(i, QISM.NoUpdate)
                    view.scrollTo(i)
                    self.selection_changed.emit([path])
                    return True
        return False

    def _rename_inline(self, path: str = None) -> bool:
        """F2 / 右クリック「名前変更」: カラムの項目をその場で編集する（r61）。
        QFileSystemModel は readOnly=False なので EditRole の setData がそのまま
        リネームになる（カラムは NoEditTriggers のためクリックでは開かない）。
        カラム内に見つからない時（平坦ビュー等）はダイアログへ退避。"""
        try:
            if path is None:
                targets = self._operation_targets()
                if len(targets) != 1:
                    self.status_message.emit("名前変更は1件だけ選択してください")
                    return False
                path = targets[0]
            idx = self._column_view._proxy_index_for_path(path)
            if idx.isValid():
                views = [v for v in self._column_view.findChildren(QListView)
                         if getattr(v, "_mfm_header", None) is not None and v.isVisible()
                         and v.rootIndex() == idx.parent()]
                if not views and self._thumb_view.isVisible():
                    views = [self._thumb_view]
                if views:
                    # current は動かさない（ファイルを current にすると
                    # プレビュー列生成＝カラムのスライドを誘発する）。
                    # Qt の view.edit() は使わない: QFileSystemModel::flags は
                    # 書き込み権限の無いファイル（Perforce 同期の read-only）に
                    # ItemIsEditable を付けず、edit() が黙って失敗する（実機で
                    # 「F2 が効かない」原因）。自前の QLineEdit をその場に重ねる。
                    self._open_inline_editor(views[0], idx, path)
                    return True
        except Exception as e:
            _mfm_log("rename inline error: %r" % (e,))
        self._rename_dialog([path] if path else [])
        return False

    def _open_inline_editor(self, view, idx, path: str):
        """項目の矩形に QLineEdit を重ねて名前を編集する。Enter=確定 / Esc=取消 /
        フォーカス喪失=確定（Explorer 準拠）。確定は os.rename（モデルの flags に
        依存しない）。"""
        old_editor = getattr(self, "_inline_editor", None)
        if old_editor is not None:
            try:
                old_editor.close()
            except Exception:
                pass
        rect = view.visualRect(idx)
        if not rect.isValid():
            self._rename_dialog([path])
            return
        vp = view.viewport()
        ed = QLineEdit(vp)
        ed.setObjectName("mfmInlineRename")
        name = os.path.basename(path.rstrip("/\\"))
        ed.setText(name)
        # アイコン分を空けて項目の上に重ねる。エディタは «不透明»（QSS
        # #mfmInlineRename）にして下の項目名が透けないようにする。
        try:
            isz = view.iconSize().width()
        except Exception:
            isz = -1
        dec = (isz if isz > 0 else 16) + 6
        ed.setAutoFillBackground(True)
        ed.setGeometry(rect.left() + dec, rect.top(), max(80, rect.width() - dec), rect.height())
        ed.show()
        ed.raise_()
        ed.setFocus(Qt.OtherFocusReason)
        # Explorer 同様、拡張子を除いた部分を選択
        stem_len = len(name) if os.path.isdir(path) else len(os.path.splitext(name)[0])
        ed.setSelection(0, stem_len if stem_len > 0 else len(name))
        self._inline_editor = ed
        state = {"done": False}

        def finish(commit: bool):
            if state["done"]:
                return
            state["done"] = True
            new_name = ed.text().strip()
            try:
                ed.hide()
                ed.deleteLater()
            except Exception:
                pass
            self._inline_editor = None
            if not commit or not new_name or new_name == name:
                return
            if any(c in new_name for c in '\\/:*?"<>|'):
                QMessageBox.warning(self, "名前変更", "ファイル名に使えない文字が含まれています。")
                return
            new_path = os.path.join(os.path.dirname(path), new_name)
            if os.path.lexists(new_path):
                QMessageBox.warning(self, "名前変更", "同名の項目が既にあります:\n%s" % new_name)
                return
            from core.undo_stack import RenameOp
            ok, err = self._rename_path(path, new_path)
            if not ok:
                QMessageBox.critical(self, "名前変更",
                                     self._rename_error_text(path, err))
                return
            try:
                self._undo_stack().push(RenameOp(path, new_path))
                self._on_fs_file_renamed(os.path.dirname(path), name, new_name, record=False)
            except Exception as e:
                _mfm_log("rename post error: %r" % (e,))

        ed.returnPressed.connect(lambda: finish(True))
        ed.editingFinished.connect(lambda: finish(True))   # フォーカス喪失=確定（Explorer準拠）

        # Tab / Shift+Tab: 確定して同じカラムの次／前の項目の名前変更へ（r67）
        def _neighbor_path(step: int):
            try:
                m = view.model()
                row = idx.row() + step
                if row < 0 or row >= m.rowCount(idx.parent()):
                    return None
                return self._resolve_path(m.index(row, 0, idx.parent()))
            except Exception:
                return None

        def _commit_and_move(step: int):
            nxt = _neighbor_path(step)
            finish(True)
            if nxt:
                QTimer.singleShot(60, lambda: self._rename_inline(nxt))

        class _KeyFilter(_QtCore.QObject):
            def eventFilter(_s, obj, ev):
                if ev.type() == _QtCore.QEvent.KeyPress:
                    k = ev.key()
                    if k == Qt.Key_Escape:
                        finish(False)
                        return True
                    if k in (Qt.Key_Return, Qt.Key_Enter):
                        # r72: ここで消費する。QLineEdit の returnPressed に任せると
                        # キーが親の QListView へ伝播して activated が発火し、
                        # _on_item_activated → _set_current_path でパス欄が
                        # フォルダへ戻る（テストで確定）。実機ではさらに
                        # 「Enter でファイルが開く／フォルダに入る」事故になる。
                        finish(True)
                        return True
                    if k == Qt.Key_Backtab or (k == Qt.Key_Tab and
                                               ev.modifiers() & Qt.ShiftModifier):
                        _commit_and_move(-1)
                        return True
                    if k == Qt.Key_Tab:
                        _commit_and_move(+1)
                        return True
                return False
        ed._mfm_keys = _KeyFilter(ed)
        ed.installEventFilter(ed._mfm_keys)

    def _on_fs_file_renamed(self, folder: str, old_name: str, new_name: str,
                            record: bool = True):
        """リネームの反映: パス欄を追従し、必要なら Undo 記録を積む。
        QFileSystemModel.fileRenamed（setData 経由）からは record=True、
        自前のインライン編集（os.rename 済み・記録済み）からは record=False。"""
        try:
            old_p = os.path.join(folder, old_name).replace("\\", "/")
            new_p = os.path.join(folder, new_name).replace("\\", "/")
            if self._addr_bar.text().replace("\\", "/") == old_p:
                self._addr_bar.setText(new_p)
            if record:
                from core.undo_stack import RenameOp
                self._undo_stack().push(RenameOp(old_p, new_p))
            self.status_message.emit(f"名前変更: {old_name} → {new_name}")
            # r98: 表示中のフォルダ自身／その祖先を変更した場合は新しい場所へ追従
            cur = (self._current_path or "").replace("\\", "/").rstrip("/")
            o = old_p.rstrip("/")
            if cur == o or cur.startswith(o + "/"):
                self.navigate_to(new_p + cur[len(o):])
                return
            # r98: リネーム後は «新しい名前の項目» を選択状態にする
            self._select_when_visible(os.path.join(folder, new_name))
        except Exception:
            pass

    def _rename_dialog(self, paths: List[str]):
        if len(paths) != 1:
            return
        old = Path(paths[0])
        new_name, ok = QInputDialog.getText(
            self, "名前変更", "新しい名前:", text=old.name
        )
        if ok and new_name:
            new_path = old.parent / new_name
            from core.undo_stack import RenameOp
            ok, err = self._rename_path(str(old), str(new_path))
            if not ok:
                QMessageBox.critical(self, "名前変更",
                                     self._rename_error_text(str(old), err))
                return
            try:
                self._undo_stack().push(RenameOp(str(old), str(new_path)))
                # r98: 選択・パス欄追従・ステータス表示は共通の後処理へ
                self._on_fs_file_renamed(str(old.parent), old.name, new_name,
                                         record=False)
            except Exception as e:
                _mfm_log("rename post error: %r" % (e,))

    def _delete_confirm(self, paths: List[str]):
        # 何を消すかを必ず見せる（誤対象＝パンくず等の事故防止）
        names = [os.path.basename(p.rstrip("/\\")) or p for p in paths]
        shown = "\n".join("  " + n for n in names[:10])
        if len(names) > 10:
            shown += "\n  …他 %d 件" % (len(names) - 10)
        msg = f"{len(paths)} 件を削除しますか？\n\n{shown}"
        ret = QMessageBox.warning(self, "削除の確認", msg,
                                  QMessageBox.Yes | QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return
        # r62: 削除は «同一ボリュームの作業ごみ箱» へ移動して Undo 可能にする
        # （作業ごみ箱へ移せないものは OS のごみ箱へ直接＝Undo 不可）。
        # ワーカー＋進捗ダイアログ。キャンセル時も完了分は Undo 記録に残す。
        from core.undo_stack import delete_to_work_trash, make_delete_op
        pairs = []

        def work(cb):
            return delete_to_work_trash(paths, progress_cb=cb, pairs=pairs)

        def done(res, cancelled, err):
            failed, direct = [], []
            if res is not None:
                _op, failed, direct = res
            op = make_delete_op(pairs)
            if op is not None:
                self._undo_stack().push(op)
            if err:
                QMessageBox.critical(self, "削除", err)
            elif failed:
                QMessageBox.warning(self, "削除エラー",
                                    f"{len(failed)} 件の削除に失敗しました:\n" +
                                    "\n".join(failed))
            elif cancelled:
                self.status_message.emit(f"削除をキャンセルしました（完了 {len(pairs) + len(direct)} 件）")
            else:
                hint = "（Ctrl+Z で元に戻せます）" if op is not None else ""
                self.status_message.emit(f"{len(paths)} 件を削除しました{hint}")
        self._run_file_op("削除", work, done, total=len(paths))

    def _show_properties(self, path: str):
        info = Path(path)
        stat = info.stat()
        import datetime
        msg = (
            f"名前: {info.name}\n"
            f"パス: {path}\n"
            f"サイズ: {format_size(stat.st_size)}\n"
            f"更新日時: {datetime.datetime.fromtimestamp(stat.st_mtime)}\n"
            f"種類: {get_file_type_category(path)}"
        )
        QMessageBox.information(self, "プロパティ", msg)

    # ------------------------------------------------------------------
    # Drag & Drop / Clipboard （Explorer 互換）
    # ------------------------------------------------------------------

    @staticmethod
    def _configure_dnd(view):
        """ビューに Explorer 互換のドラッグ&ドロップ設定を適用する。"""
        view.setDragEnabled(True)             # アプリ → Explorer 等へドラッグ可
        view.setAcceptDrops(True)             # Explorer 等 → アプリへドロップ可
        view.setDropIndicatorShown(True)
        view.setDragDropMode(QAbstractItemView.DragDrop)
        view.setDefaultDropAction(Qt.MoveAction)   # 既定は移動（Ctrlでコピー）
        view.setDragDropOverwriteMode(False)
        view.setEditTriggers(QAbstractItemView.NoEditTriggers)  # 誤リネーム防止

    # ---- クリップボード (Ctrl+C / Ctrl+X / Ctrl+V / Delete) ----

    # Windows の "Preferred DropEffect" 値（CF_PREFERREDDROPEFFECT）
    _DROPEFFECT_COPY = 5   # コピー（Explorerが受理する慣用値）
    _DROPEFFECT_MOVE = 2   # 移動（切り取り）

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

    def _set_clipboard(self, paths: List[str], move: bool):
        """選択ファイルをクリップボードへ。Explorer と相互にペースト可能な形式で格納。"""
        paths = [p for p in paths if p and os.path.exists(p)]
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

    # ── 長時間になり得るファイル操作の共通経路（r62） ─────────────────────
    # 方針: コピー/移動/削除/Undo/Redo は必ずワーカースレッドで実行し、
    # 0.4 秒を超えたら進捗ダイアログ（キャンセル付き）を出す。UI スレッドで
    # shutil を回してフリーズさせない。キャンセル時も «完了した分» は
    # Undo 記録に残す（部分完了を黙って捨てない）。
    class _OpCancelled(Exception):
        pass

    def _run_file_op(self, title: str, work, on_done=None, total: int = 0):
        """work(progress_cb) をワーカーで実行。progress_cb(i, n) はワーカーから
        呼ばれ、キャンセル済みなら _OpCancelled を投げて中断させる。
        on_done(result, cancelled: bool, error: str) は UI スレッドで呼ばれる。"""
        from core.compat import QProgressDialog
        notifier = _FileOpNotifier(self)
        dlg = QProgressDialog(title, "キャンセル", 0, max(0, int(total)), self)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(400)      # 短い操作ではダイアログを出さない
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        state = {"cancel": False, "done": False}
        dlg.canceled.connect(lambda: state.__setitem__("cancel", True))
        t0 = _time_mod.monotonic()

        def cb(i, n):
            notifier.progress.emit(int(i), int(n))
            if state["cancel"]:
                raise BrowserPanel._OpCancelled()

        def run():
            try:
                res = work(cb)
                notifier.finished.emit(res, "")
            except BrowserPanel._OpCancelled:
                notifier.finished.emit(None, "__cancel__")
            except Exception as e:
                notifier.finished.emit(None, str(e) or e.__class__.__name__)

        def on_progress(i, n):
            if n > 0 and dlg.maximum() != n:
                dlg.setMaximum(n)
            dlg.setValue(i)
            dlg.setLabelText("%s  (%d / %d)" % (title, i, n) if n > 0 else title)

        # 項目数が少なく1件が重い（巨大ファイル）時も、0.4秒経ったら必ず出す
        tick = QTimer(self)
        tick.setInterval(200)

        def on_tick():
            if state["done"]:
                return
            if _time_mod.monotonic() - t0 > 0.4 and not dlg.isVisible():
                dlg.show()
        tick.timeout.connect(on_tick)
        tick.start()

        def done(res, err):
            state["done"] = True
            tick.stop()
            try:
                dlg.close()
                dlg.deleteLater()
                notifier.deleteLater()
            except Exception:
                pass
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
        notifier.progress.connect(on_progress)
        notifier.finished.connect(done)
        threading.Thread(target=run, daemon=True, name="mfm-file-op").start()

    def _refresh_flat_view(self):
        try:
            fc = self._flat_col
            if fc.isVisible():
                fc.set_sources(list(fc._sources))
                self._request_flat_integration_status()
        except Exception as e:
            _mfm_log("flat refresh error: %r" % (e,))

    # ── Undo / Redo（r62、Explorer 相当: 名前変更・移動・コピー・削除） ─────
    def _undo_stack(self):
        from core.undo_stack import get_undo_stack
        return get_undo_stack()

    def _undo_op(self):
        st = self._undo_stack()
        if not st.can_undo():
            self.status_message.emit("元に戻す操作はありません")
            return
        label = st.undo_label()

        def done(res, cancelled, err):
            if err:
                QMessageBox.warning(self, "元に戻す", "元に戻せませんでした:\n%s" % err)
            else:
                self.status_message.emit("元に戻しました: " + label)
        self._run_file_op("元に戻す: " + label, lambda cb: st.undo(), done)

    def _redo_op(self):
        st = self._undo_stack()
        if not st.can_redo():
            self.status_message.emit("やり直す操作はありません")
            return
        label = st.redo_label()

        def done(res, cancelled, err):
            if err:
                QMessageBox.warning(self, "やり直す", "やり直せませんでした:\n%s" % err)
            else:
                self.status_message.emit("やり直しました: " + label)
        self._run_file_op("やり直す: " + label, lambda cb: st.redo(), done)

    # ------------------------------------------------------------------
    # カラムへのファイルドロップ（r87）
    # ------------------------------------------------------------------

    def _on_files_dropped(self, paths: List[str], dest_dir: str, move: bool):
        """カラム間 D&D の実行。移動（既定）／コピー（Ctrl）。
        同名が既にある時は «上書き / 名前を変えて / スキップ» を選ばせる。"""
        from core.i18n import tr
        from core.file_operations import (move_items, copy_items,
                                          CONFLICT_RENAME, CONFLICT_SKIP)
        from core.undo_stack import MoveOp, CopyOp

        dest_dir = os.path.normpath(dest_dir)
        if not os.path.isdir(dest_dir):
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
            if os.path.isdir(p) and dn.startswith(pn + os.sep):
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

        self._run_file_op(label, work, done, total=len(srcs))

    def _duplicate_selected(self, paths: List[str] = None):
        """Ctrl+D / 右クリック「複製」: 同じ場所へ «元名_Copy» で複製（r67）。
        ダイアログで名前 or Search/Replace（＋サブフォルダ以下へ適用）を指定。
        ワーカー＋進捗、Undo は CopyOp（複製先を消す）。"""
        paths = list(paths) if paths else self._operation_targets()
        paths = [p for p in paths if p and os.path.lexists(p)]
        if not paths:
            self.status_message.emit("複製する項目を選択してください")
            return
        from ui.duplicate_dialog import DuplicateDialog
        from core.file_operations import duplicate_items
        from core.undo_stack import CopyOp
        dlg = DuplicateDialog(paths, parent=self.window())
        if (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()) != QDialog.Accepted:
            return
        specs = dlg.specs()
        inside = dlg.rename_inside()
        results: List[str] = []

        def work(cb):
            duplicate_items(specs, progress_cb=cb, results=results, rename_inside=inside)
            return results

        def done(res, cancelled, err):
            pairs = [(s, d) for (s, d) in specs if d in results]
            if pairs:
                self._undo_stack().push(CopyOp(pairs))
            if err:
                QMessageBox.critical(self, "複製", err)
            elif cancelled:
                self.status_message.emit("複製をキャンセルしました（完了 %d 件）" % len(results))
            else:
                self.status_message.emit("複製: %d 件（Ctrl+Z で元に戻せます）" % len(results))
        self._run_file_op("複製", work, done, total=len(specs))

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
        except Exception:
            pass
        return None

    def _clipboard_copy(self):
        self._set_clipboard(self._operation_targets(), move=False)

    def _clipboard_cut(self):
        self._set_clipboard(self._operation_targets(), move=True)

    def _paste_target_dir(self) -> Optional[str]:
        """貼り付け先: 単一フォルダ選択中ならそのフォルダ、無ければ現在地。
        r70: 空白右クリックの「貼り付け」はそのカラムのフォルダ。"""
        ov = getattr(self, "_paste_override_dir", None)
        if ov and os.path.isdir(ov):
            return ov
        sel = self._operation_targets()
        if len(sel) == 1 and os.path.isdir(sel[0]):
            return sel[0]
        return self._current_path if os.path.isdir(self._current_path) else None

    def _clipboard_paste(self):
        mime = QApplication.clipboard().mimeData()
        if not mime or not mime.hasUrls():
            return
        paths = [u.toLocalFile() for u in mime.urls() if u.toLocalFile()]
        paths = [p for p in paths if os.path.exists(p)]
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
            QMessageBox.warning(self, "貼り付け", "貼り付け先フォルダを特定できません。")
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
        results: List[str] = []
        fn = move_items if move else copy_items
        # r98: «Manager で開いているフォルダ» は監視ハンドルのせいで Windows では
        # 移動できない。フォルダが対象に含まれる移動の前に手放しておく。
        if move:
            try:
                for pth in paths:
                    if os.path.isdir(pth):
                        self._release_fs_handles(pth, level=1)
                        break
            except Exception:
                pass

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
                self.status_message.emit(f"{verb}をキャンセルしました（完了 {len(results)} 件）")
            else:
                self.status_message.emit(f"{verb}完了: {len(results)} 件")
            self._check_current_gone_async()          # r98/r107
        self._run_file_op(verb, work, done, total=len(paths))

    def _clipboard_delete(self):
        paths = self._operation_targets()
        if paths:
            self._delete_confirm(paths)

    # ------------------------------------------------------------------
    # Thumbnail refresh
    # ------------------------------------------------------------------

    def _on_thumbnail_ready(self, path: str, pixmap: "QPixmap"):
        """Force repaint when a thumbnail arrives.
        r84: カラムをサムネイル表示にした場合も再描画する（従来は
        _thumb_view だけで、カラム側は差し替わらなかった）。"""
        if self._thumb_view.isVisible():
            self._thumb_view.viewport().update()
        try:
            for v in self._column_view.findChildren(QListView):
                if getattr(v, "_mfm_view_mode", "list") == "thumb":
                    v.viewport().update()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _list_visible_files(self, directory: str) -> List[str]:
        """先読み対象のファイル一覧。クラウドの «オンラインのみ» は
        除外する（読むとダウンロードが走るため。r83）。"""
        from core.cloud_state import is_online_only
        try:
            out = []
            for f in os.listdir(directory):
                p = os.path.join(directory, f)
                if not os.path.isfile(p):
                    continue
                if is_online_only(p):
                    continue
                out.append(p)
                if len(out) >= 64:      # Prefetch at most 64 files
                    break
            return out
        except OSError:
            return []

    # ------------------------------------------------------------------
    # Public setters (called by main window)
    # ------------------------------------------------------------------

    def set_open_callback(self, cb: Callable[[str], None]):
        self._on_open = cb

    def set_import_callback(self, cb: Callable[[str], None]):
        self._on_import = cb

    def set_reference_callback(self, cb: Callable[[str], None]):
        self._on_reference = cb

    def set_dnd_callback(self, cb):
        """DCC（Maya/Blender）へのD&D検出時コールバック cb(action, paths, app) を
        登録し、カラムビューのドロップ検出を有効化する。"""
        self._dnd_callback = cb
        self._column_view._maya_drop_cb = self._handle_maya_drop

    def set_dcc_callback(self, cb):
        """右クリックの「Maya へ…」「Blender へ…」用 cb(app, action, paths)（r65）。"""
        self._dcc_callback = cb

    def _handle_maya_drop(self, paths, app: str = "maya"):
        """Maya/Blender のウィンドウへD&Dされた → 設定されたD&D動作を実行する。
        r90: Maya へ落とした .py / .mel は «Maya と同じくスクリプトとして実行»
        （install.py 等。ブリッジ経由なので Manager は固まらない）。"""
        action = self._sm.get("dnd_action", "none")
        _mfm_log("dcc-drop: app=%s action=%s paths=%d" % (app, action, len(paths)))
        cb = getattr(self, "_dnd_callback", None)
        if not callable(cb):
            return
        scripts, others = [], []
        for p in paths:
            (scripts if (app == "maya" and os.path.splitext(p)[1].lower()
                         in (".py", ".mel")) else others).append(p)

        def _call(act, ps):
            try:
                cb(act, ps, app)
            except TypeError:
                cb(act, ps)
        if scripts:
            _call("run_script", scripts)
        if others and action != "none":
            _call(action, others)

    def set_dcc_takeover_callback(self, cb):
        """DCC への落下を Manager が引き受けるかの判定 cb(paths, app)（r90）。"""
        self._column_view.set_dcc_takeover_callback(cb)

    def set_max_depth(self, depth: int):
        """深度キャップ（ルート自動シフト）は廃止済み（init参照）。
        設定ダイアログ適用時に旧設定値で再有効化されると、setRootIndex による
        全カラム再構築がクリック毎に走り「カラムが1本だけになる／半端な位置で
        切れる」原因になった（r54 で確定）。値は保存するが動作には反映しない。"""
        self._sm.set("column_max_depth", depth)

    def set_thumb_size(self, size: int):
        self._thumb_delegate._thumb_size = size
        self._thumb_view.setGridSize(QSize(size + 16, size + 32))
        self._thumb_view.setItemDelegate(self._thumb_delegate)
        self._thumb_mgr.set_thumb_size(size)
        self._sm.set("thumbnail_size", size)

    def current_path(self) -> str:
        return self._current_path


# ファイル末尾センチネル: 起動ログにこの行が出れば、このファイルは
# 末尾まで欠損なく読み込まれている（ファイル同期の切り詰め検出用）。
_mfm_log("browser_panel.py loaded to EOF (r108 complete)")
