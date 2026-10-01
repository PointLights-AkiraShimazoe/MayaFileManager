# -*- coding: utf-8 -*-
"""カラム／グリッドの描画デリゲート（r110 で browser_panel から分離）。

ThumbnailDelegate: サムネイルと代替アイコンの描画
StatusBadgeDelegate: Git/SVN/Perforce/クラウドの状態バッジ"""

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

from ui.browser_util import (  # noqa: F401  （再エクスポート）
    _cursor_over_maya_window, _cursor_over_dcc_window, _time_mod, _MFM_T0,
    _MFM_STARTUP_LOG, _MFM_FREEZE_LOG, _re_mod, _DRIVE_IN_LABEL_RE,
    _safe_file_path, _SafeIconProvider, _mfm_slow_note, _MFM_UI_LOG,
    _MFM_UILOG_ON, _mfm_warn, _mfm_uilog, _MFM_BLOCKING,
    mfm_blocking_begin, mfm_blocking_end, mfm_blocking_reason,
    _mfm_timeline, _MFM_LOG_PATH, _MFM_DEBUG, _mfm_log,
)





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

