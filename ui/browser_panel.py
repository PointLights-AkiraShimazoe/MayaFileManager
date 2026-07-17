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
from pathlib import Path
from typing import List, Optional, Callable

from core.compat import (
    Qt, Signal, QObject,
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QToolButton,
    QSplitter, QTreeView, QColumnView, QListView,
    QSizePolicy, QFrame, QAbstractItemView, QHeaderView,
    QFileSystemModel, QSortFilterProxyModel,
    QMenu, QAction, QMessageBox, QFileDialog, QInputDialog,
    QStyledItemDelegate, QStyle, QStyleOption,
    QModelIndex, QSize, QPixmap, QPainter, QColor, QFont,
    QDir, QFileInfo, QUrl, QMimeData, QPoint,
    QFontMetrics, QTimer, QKeySequence, QDrag,
)
from core.compat import QtCore as _QtCore
from core.path_guard import PathProber, DriveScanner, invalidate_cache
from core.file_operations import (
    open_with_default_app, reveal_in_explorer,
    copy_items, move_items, delete_items,
    get_file_type_category, list_drives, format_size,
    resolve_windows_shortcut,
    FileOperationError, MAYA_EXTENSIONS, SCENE_EXTENSIONS
)
from core.thumbnail_generator import ThumbnailManager


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
# Thumbnail Delegate
# ---------------------------------------------------------------------------

class ThumbnailDelegate(QStyledItemDelegate):
    """
    Paints a thumbnail to the left of the file name in list/icon views.
    Falls back to system icon when thumbnail is not yet ready.
    """

    def __init__(self, thumb_mgr: ThumbnailManager, thumb_size: int = 64, parent=None):
        super().__init__(parent)
        self._mgr = thumb_mgr
        self._thumb_size = thumb_size

    def sizeHint(self, option, index) -> QSize:
        return QSize(self._thumb_size + 8, self._thumb_size + 8)

    def paint(self, painter: QPainter, option, index: QModelIndex):
        self.initStyleOption(option, index)

        # Draw selection background
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor("#2A5080"))

        # Thumbnail area
        thumb_rect = option.rect.adjusted(4, 4, -4, -4)
        thumb_rect.setWidth(self._thumb_size)
        thumb_rect.setHeight(self._thumb_size)

        model = index.model()
        source_model = model
        source_index = index
        # Unwrap proxy
        while hasattr(source_model, "sourceModel"):
            source_index = source_model.mapToSource(source_index)
            source_model = source_model.sourceModel()

        file_path = source_model.filePath(source_index) if hasattr(source_model, "filePath") else ""

        if file_path:
            pixmap = self._mgr.get(file_path)
            if pixmap and not pixmap.isNull():
                scaled = pixmap.scaled(self._thumb_size, self._thumb_size,
                                       Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = thumb_rect.x() + (self._thumb_size - scaled.width()) // 2
                y = thumb_rect.y() + (self._thumb_size - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)

        # File name
        text_rect = option.rect.adjusted(self._thumb_size + 8, 4, -4, -4)
        painter.setPen(QColor("#FFFFFF") if option.state & QStyle.State_Selected else QColor("#CCCCCC"))
        fm = QFontMetrics(option.font)
        display = index.data(Qt.DisplayRole) or ""
        elided = fm.elidedText(display, Qt.ElideMiddle, text_rect.width())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)


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
        self.invalidateFilter()

    def set_force_visible(self, paths):
        """隠し属性でも表示する祖先パス集合を設定する。"""
        self._force_visible = set(self._norm(p) for p in paths)
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
        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)
        try:
            fp = self._norm(source_model.filePath(index))
        except Exception:
            fp = ""
        protected = fp in self._force_visible  # 現在ナビ中の経路は常に表示
        is_dir = source_model.isDir(index)
        name_l = source_model.fileName(index).lower()

        # Hidden files（隠し属性 or ドット名）
        if not self._show_hidden and not protected:
            name = source_model.fileName(index)
            is_hidden = name.startswith(".")
            if not is_hidden:
                try:
                    is_hidden = source_model.fileInfo(index).isHidden()
                except Exception:
                    is_hidden = False
            if is_hidden:
                return False

        # 全体フィルタ（ディレクトリはナビ維持のため常に通す）
        if self._filter and not is_dir:
            if not self._fuzzy_match(self._filter, name_l):
                return False

        # カラム別フィルタ／排他フィルタ（その親=カラムにのみ適用。現在ナビ中の
        # 経路の祖先は保護してチェーンを壊さない）。
        # 一致は «部分一致(substring)»。例: "c00" は "c010" にはヒットしない。
        if source_parent.isValid() and not protected:
            pkey = self._norm(source_model.filePath(source_parent))
            cf = self._col_filters.get(pkey)
            if cf and cf not in name_l:
                return False
            ex = self._col_excludes.get(pkey)
            if ex and ex in name_l:
                return False   # 排他フィルタに一致 → 除外

        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """カラム別ソート。フォルダ優先＋親パス別のキー(name/type/date/size)。
        QFileInfo(stat)は再ソート中に不安定なため、モデルのキャッシュ値/DisplayRoleを使う。"""
        try:
            sm = self.sourceModel()
            parent = left.parent()
            ppath = self._norm(sm.filePath(parent)) if parent.isValid() else ""
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
        self._flatten_view = None   # 現在「平坦」ONのカラムビュー（排他制御用）
        self._pending_multi_drag = None
        self._selected_dir_paths = set()

    def set_max_depth(self, depth: int):
        self._max_depth = depth

    def set_go_up_callback(self, cb):
        """◀ボタン押下時に呼ぶコールバック（1階層上げる）を登録。"""
        self._go_up_cb = cb

    def createColumn(self, index):
        """各カラム生成時に、左部中央へ『◀ 上の階層へ』ボタンを重ねる。"""
        view = super().createColumn(index)
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
        # このカラムが表示しているフォルダ（◀でこの階層を1つ上へ）
        folder_path = self._path_for_index(index)
        # カラム上部に「このカラムだけに効く」フィルタ／ソートのヘッダを設置
        self._build_column_header(view, folder_path)
        btn = QToolButton(view.viewport())   # 項目の上に重ねて確実に見せる
        btn.setText("◀")
        btn.setToolTip("上の階層へ")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(24, 40)             # 幅を広げて見やすく
        btn.setStyleSheet(
            "QToolButton{background:rgba(40,40,40,215);"
            "border:1px solid rgba(130,130,130,190);border-radius:4px;"
            "color:#ffffff;font-size:13px;font-weight:bold;}"
            "QToolButton:hover{background:rgba(105,105,105,235);}"
        )
        btn.clicked.connect(lambda checked=False, p=folder_path: self._emit_go_up(p))
        view._mfm_up_btn = btn
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)
        self._reposition_up_button(view)
        btn.show()
        btn.raise_()
        return view

    def _emit_go_up(self, folder_path=None):
        if callable(self._go_up_cb):
            self._go_up_cb(folder_path)

    # ------------------------------------------------------------------
    # カラム別フィルタ／ソート ヘッダ
    # ------------------------------------------------------------------
    _COL_HEADER_H = 52   # 2行（フィルタ行＋ソート/平坦/表示 行）
    _SORT_KEYS = [("name", "名前"), ("type", "種類"),
                  ("date", "日付"), ("size", "サイズ")]

    def set_thumb_mgr(self, mgr):
        self._thumb_mgr = mgr

    def set_merge_callback(self, cb):
        self._merge_cb = cb

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
        hdr.setStyleSheet(
            "#mfmColHeader{background:rgba(32,32,32,238);"
            "border-bottom:1px solid rgba(120,120,120,150);}"
            "QLineEdit{background:rgba(20,20,20,235);color:#ddd;"
            "border:1px solid rgba(110,110,110,150);border-radius:3px;padding:0 4px;}"
            "QComboBox{background:rgba(45,45,45,235);color:#ddd;"
            "border:1px solid rgba(110,110,110,150);border-radius:3px;padding:0 4px;}"
            "QToolButton{background:rgba(55,55,55,235);color:#ddd;"
            "border:1px solid rgba(110,110,110,150);border-radius:3px;}"
            "QToolButton:hover{background:rgba(90,90,90,235);}"
            "QToolButton:checked{background:rgba(60,120,200,245);"
            "border-color:rgba(120,170,240,220);}"
        )
        vlay = QVBoxLayout(hdr)
        vlay.setContentsMargins(3, 3, 3, 3)
        vlay.setSpacing(3)
        # --- 1行目: フィルタ ＋ 排他フィルタ ---
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(3)
        edit = QLineEdit(hdr)
        edit.setPlaceholderText("フィルタ")
        edit.setClearButtonEnabled(True)
        edit.setFixedHeight(20)
        edit.setText(proxy.get_column_filter(folder_path))
        edit.textChanged.connect(
            lambda t, p=folder_path: proxy.set_column_filter(p, t))
        excl = QLineEdit(hdr)
        excl.setPlaceholderText("排他")
        excl.setClearButtonEnabled(True)
        excl.setFixedHeight(20)
        excl.setToolTip("入力に一致するファイルを一覧から除外")
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
        flat_btn.setText("平坦")
        flat_btn.setCheckable(True)
        flat_btn.setChecked(getattr(view, "_mfm_flatten", False))
        flat_btn.setFixedHeight(20)
        flat_btn.setToolTip("選択中フォルダ以下の全ファイルを平坦表示"
                            "（選択が無ければこのカラムのフォルダ全体）")

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
        view_btn.setToolTip("リスト⇄サムネイル切替")
        view_btn.clicked.connect(lambda _c=False, v=view: self._toggle_view_mode(v))

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
                size = 96
                view._mfm_view_mode = "thumb"
                view.setViewMode(QListView.IconMode)
                view.setResizeMode(QListView.Adjust)
                view.setWrapping(True)
                view.setSpacing(6)
                view.setGridSize(QSize(size + 18, size + 30))
                if self._thumb_mgr is not None:
                    view.setItemDelegate(ThumbnailDelegate(self._thumb_mgr, size, view))
            else:
                view._mfm_view_mode = "list"
                view.setViewMode(QListView.ListMode)
                view.setWrapping(False)
                view.setSpacing(0)
                view.setGridSize(QSize())
                view.setItemDelegate(QStyledItemDelegate(view))
            self._reposition_up_button(view)
            self._reposition_column_header(view)
        except Exception:
            pass

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
                fp = sm.filePath(src) if hasattr(sm, "filePath") else ""
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
        return src.filePath(idx) if hasattr(src, "filePath") else ""

    def _reposition_up_button(self, view):
        btn = getattr(view, "_mfm_up_btn", None)
        if btn is None:
            return
        vp = view.viewport()
        y = max(0, (vp.height() - btn.height()) // 2)
        btn.move(2, y)
        btn.raise_()

    def eventFilter(self, obj, event):
        et = event.type()
        if et in (_QtCore.QEvent.Resize, _QtCore.QEvent.Show):
            view = obj if getattr(obj, "_mfm_up_btn", None) is not None else obj.parent()
            if view is not None and getattr(view, "_mfm_up_btn", None) is not None:
                self._reposition_up_button(view)
            if view is not None and getattr(view, "_mfm_header", None) is not None:
                self._reposition_column_header(view)
        elif et == _QtCore.QEvent.Wheel and (event.modifiers() & Qt.ShiftModifier):
            # Shift+ホイールでブラウジングエリアを横スクロール（カラム間移動）
            hbar = self.horizontalScrollBar()
            if hbar is not None:
                d = event.angleDelta().y() or event.angleDelta().x()
                hbar.setValue(hbar.value() - d)
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
                _anchor_dbg = getattr(view, "_mfm_sel_anchor", None)
                _mfm_log("MousePress: valid=%s row=%s ctrl=%s shift=%s anchor_valid=%s view=%s name=%r"
                         % (idx.isValid(), (idx.row() if idx.isValid() else -1),
                            bool(mods & Qt.ControlModifier), bool(mods & Qt.ShiftModifier),
                            (_anchor_dbg is not None and _anchor_dbg.isValid()), id(view),
                            (idx.data() if idx.isValid() else None)))
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
                # 複数選択中の項目を «修飾キーなし» で押した → 全選択項目をドラッグ。
                # （QColumnViewは押下で単一ナビ＝選択クリアするため、自前でドラッグを
                #  起動しないと複数選択のD&Dができない）
                if idx.isValid():
                    sm = view.selectionModel()
                    if sm is not None and sm.isSelected(idx):
                        sel = [i for i in sm.selectedIndexes() if i.column() == 0]
                        if len(sel) >= 2:
                            # ここで即 drag.exec() すると、ただのクリックでもドラッグ扱いになり
                            # 操作が重い。Press では標準の単一選択化だけ止め、Move 閾値を
                            # 超えた時だけ MouseMove 側で複数D&Dを開始する。
                            view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
                            self._pending_multi_drag = {
                                "view": view,
                                "pos": pos,
                                "press_idx": _QtCore.QPersistentModelIndex(idx),
                                "indexes": [_QtCore.QPersistentModelIndex(i) for i in sel],
                            }
                            return True
                # 何もない所クリック → そのカラムの選択を解除（標準挙動）
                if not idx.isValid():
                    self._clear_column_selection(view)
                    self._swallow_release = True
                    return True
                # 通常クリック＝標準挙動: 複数選択は解除され単一選択になる。
                # （選択の温存はしない。Ctrl/Shift を使った時だけ選択が増える）
                view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
                self._clear_multi_state()
                # ネイティブ処理はこのカラムのモデルしか選択し直さないため、
                # 本体・他カラムに残った複数選択を直後に掃除する
                _pp = _QtCore.QPersistentModelIndex(idx)
                QTimer.singleShot(0, lambda: self._prune_after_native_click(_pp))
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
                                self._start_multi_drag(view, indexes)
                                return True
                except Exception:
                    self._pending_multi_drag = None
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
                            sm = view.selectionModel()
                            if sm is not None:
                                sm.setCurrentIndex(idx, QISM.NoUpdate)
                            top = self.selectionModel()
                            if top is not None:
                                # 本体 current の変更が子カラム展開を駆動する
                                top.setCurrentIndex(idx, QISM.NoUpdate)
                            _mfm_log("release-click: single-select %r"
                                     % (idx.data(),))
                            self.clicked.emit(idx)
                except Exception as e:
                    _mfm_log("release-click error: %r" % (e,))
                return True
        return super().eventFilter(obj, event)

    def _start_multi_drag(self, view, indexes):
        """複数選択した項目を一括ドラッグする。QFileSystemModel の mimeData を
        使い、Explorer 互換のファイルD&Dにする。"""
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
            mime = None
            if hasattr(fsm, "mimeData"):
                mime = fsm.mimeData(src_idxs)
            if mime is None:
                paths = [fsm.filePath(s) for s in src_idxs if hasattr(fsm, "filePath")]
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(p) for p in paths if p])
            drag = QDrag(view)
            drag.setMimeData(mime)
            drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)
        except Exception:
            pass

    def _path_of_index(self, idx):
        """カラムビューのプロキシindex → ソースの実パス。"""
        try:
            m = self.model()
            src = idx
            sm = m
            while hasattr(sm, "mapToSource"):
                src = sm.mapToSource(src)
                sm = sm.sourceModel()
            return sm.filePath(src) if hasattr(sm, "filePath") else ""
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

    def __init__(self, settings_manager, thumb_manager: ThumbnailManager, parent=None):
        super().__init__(parent)
        _mfm_log("=== BrowserPanel init (build: r18 rebuild-skip-smooth 2026-07-07) ===")
        self._sm = settings_manager
        self._thumb_mgr = thumb_manager
        self._thumb_mgr.thumbnail_ready.connect(self._on_thumbnail_ready)

        self._current_path = str(Path.home())
        # 前回パス復元モードが有効なら前回終了時のパスから開始
        if self._sm.get("restore_last_path", False):
            _last = self._sm.get("last_path", "")
            if _last:
                self._current_path = _last
        # フルパス保持（深度キャップ廃止）。保存設定に関係なく無制限。
        self._max_depth = 0
        self._click_action = self._sm.get("single_click_action", "preview")
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Toolbar ──────────────────────────────────────────────────
        toolbar = QFrame()
        toolbar.setFrameShape(QFrame.StyledPanel)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(4, 4, 4, 4)
        tb_layout.setSpacing(4)

        # Drive selector
        self._drive_combo = QComboBox()
        self._drive_combo.setFixedWidth(80)
        self._drive_combo.currentTextChanged.connect(self._on_drive_changed)
        tb_layout.addWidget(self._drive_combo)
        self._refresh_drives()

        # Back / forward / up
        # 視認性向上: 大きめ・太字・はっきりした矢印グリフ＋ホバー強調
        _nav_style = (
            "QToolButton{"
            "  background:#3a3a3a; color:#f0f0f0;"
            "  border:1px solid #555; border-radius:4px;"
            "  font-size:18px; font-weight:bold; padding:0px;"
            "}"
            "QToolButton:hover{ background:#4A90D9; color:#ffffff; border-color:#4A90D9; }"
            "QToolButton:pressed{ background:#2A5080; }"
            "QToolButton:disabled{ color:#777; background:#2c2c2c; border-color:#3a3a3a; }"
        )
        for icon_text, tip, slot in [
            ("◀", "戻る", self._go_back),    # ◀ 黒塗り三角（小さな←より視認性が高い）
            ("▶", "進む", self._go_forward),  # ▶
            ("▲", "上へ", self._go_up),       # ▲
        ]:
            btn = QToolButton()
            btn.setText(icon_text)
            btn.setToolTip(tip)
            btn.setFixedSize(36, 30)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_nav_style)
            btn.clicked.connect(slot)
            tb_layout.addWidget(btn)

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
        self._fs_model.setRootPath("")
        # QDir.Hidden を含めることで、AppData 等の隠しフォルダもモデルに載せる。
        # 表示の可否は proxy 側で制御（既定は非表示、ナビ経路の祖先のみ強制表示）。
        self._fs_model.setFilter(
            QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Files | QDir.Hidden
        )
        # 読み取り専用を解除 → Qt標準のファイルD&D（Explorerとの相互コピー/移動）を有効化。
        # これにより各アイテムに Drag/Drop フラグが付与される。
        self._fs_model.setReadOnly(False)
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
        self._column_view.set_thumb_mgr(self._thumb_mgr)
        self._column_view.set_merge_callback(self._on_flat_request)
        # カラム幅をユーザーが任意に変えられるよう、各カラムにリサイズグリップを表示
        try:
            self._column_view.setResizeGripsVisible(True)
        except Exception:
            pass
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
        self._flat_col.file_activated.connect(open_with_default_app)
        self._flat_col.file_selected.connect(self._sync_quick_look)
        # 平坦ビューで単一ファイル選択時もパス欄にファイル名まで表示
        self._flat_col.file_selected.connect(
            lambda p: self._addr_bar.setText(p) if p else None)
        self._flat_col.closed.connect(self._flat_col.hide)
        # 平坦ビューにも通常カラムと同じ右クリックメニューを付ける
        self._flat_col._view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._flat_col._view.customContextMenuRequested.connect(
            self._show_flat_context_menu)
        self._view_stack.addWidget(self._flat_col)
        self._flat_col.hide()
        # カラムビューが主、平坦カラムは右に従。初期サイズ配分。
        self._view_stack.setStretchFactor(0, 1)
        self._column_view.set_flat_callback(self._on_flat_request)

        # 共通フォルダのドリルダウン・カラム群（複数選択時に平坦カラムの左へ挿入）
        self._common_cols = []
        self._common_srcs = []

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
        self._history: List[str] = []
        self._history_index: int = -1

        # ファイルのD&Dはビュー(モデル)側で一括処理するため、パネル自身は
        # ドロップを受け取らない（横取りして「移動だけ」になるのを防ぐ）。
        self.setAcceptDrops(False)

        # N-2: Space Quick Look 用イベントフィルタ
        self._column_view.installEventFilter(self)
        self._thumb_view.installEventFilter(self)

        # Ctrl+C / Ctrl+X / Ctrl+V / Delete（子ビューにフォーカスがあっても発火）
        self._install_clipboard_actions()

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
            self._quick_look.show_for(path)

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
        try:
            self._thumb_mgr.prefetch(self._list_visible_files(path))
        except OSError:
            pass

    def _navigate_now(self, path: str, add_to_history: bool = True):
        self._current_path = path
        self._addr_bar.setText(path)
        self._sync_drive_combo(path)

        is_dir = os.path.isdir(path)
        target_dir = path if is_dir else str(Path(path).parent)

        # 経路上の隠しフォルダ(AppData等)を強制表示にしてカラムチェーンを構築可能にする
        self._apply_force_visible(path)

        # QFileSystemModel に対象ディレクトリまでのチェーンを能動的にロードさせる。
        # これが無いと、別ドライブ/深い/隠し経由のパスは index が無効のままで
        # カラムが構築されない（クリックしてもカラムが伸びない原因）。
        try:
            self._fs_model.setRootPath(target_dir)
        except Exception:
            pass

        # カラムのルート: パス途中(または自身)にリンクがあれば最上位リンク、無ければドライブ最上位
        col_root = self._column_root_for(path)
        self._column_view.setRootIndex(
            self._proxy.mapFromSource(self._fs_model.index(col_root)))
        _init_idx = self._proxy.mapFromSource(self._fs_model.index(path))
        self._column_view.setCurrentIndex(_init_idx)
        # 深い/別ドライブ/隠し経由のパスは遅延ロードのため、
        # 最上位から1段ずつ読み込みを起動してカラムを伸ばす
        self._pending_current = path
        self._prime_path_loading(path)
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

        # Prefetch thumbnails for visible items（到達確認済みだが念のため保護）
        try:
            self._thumb_mgr.prefetch(self._list_visible_files(path))
        except OSError:
            pass

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
                if os.path.isdir(comp) and self._is_symlink_or_junction(comp):
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
        cur_root = (self._fs_model.filePath(self._proxy.mapToSource(root_idx))
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
        idx = self._fs_model.index(top)
        if idx.isValid() and self._fs_model.canFetchMore(idx):
            self._fs_model.fetchMore(idx)
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
        self._pending_current = None
        pidx = self._proxy.mapFromSource(tgt_idx)
        if pidx.isValid():
            self._column_view.setCurrentIndex(pidx)
        for _ms in (90, 320):
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
                curpath = self._fs_model.filePath(self._proxy.mapToSource(cur))
            _mfm_log("advance(after setCurrent): colview_current=%r" % curpath)
            self._pending_current = None
            # 内部状態は正しいが表示カラムが古いブランチのまま残る QColumnView の
            # 描画バグ対策。ロード完全後に「ルート＋現在地」を遅延再適用して
            # カラムスタックを作り直す（シグナル内での setRootIndex はクラッシュ
            # するため QTimer で次のイベントループへ逃がす）。
            tgt = target
            # ロード/カラム除去が落ち着いた後にチェーンを強制再展開し、
            # 深いカラムの欠落とスクロール範囲の空白の両方を解消する。
            for _ms in (90, 320):
                QTimer.singleShot(_ms, lambda p=tgt: self._force_column_rebuild(p))

    def _force_column_rebuild(self, target: str):
        """ルートと現在地を再適用して QColumnView のカラムを末端まで作り直す。
        既に current==target だと setCurrentIndex が no-op になり深い階層が
        展開されないため、一旦 current を無効化してから target を設定し直す。"""
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
            cur_path = (self._fs_model.filePath(self._proxy.mapToSource(cur))
                        if cur.isValid() else "")
            root_path = (self._fs_model.filePath(self._proxy.mapToSource(root))
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
        return self._fs_model.filePath(source_index)

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
        action = self._sm.get("single_click_action", "preview")
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

    def _on_flat_request(self, dirs):
        """平坦カラムの表示要求。dirs があれば選択カラムの直後へ詰めて表示する。"""
        dirs = [d for d in (dirs or []) if d and os.path.isdir(d)]
        if dirs:
            self._merge_panel.hide()
            self._flat_col.set_sources(dirs)
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
        idx = self._view_stack.indexOf(self._flat_col)
        self._view_stack.insertWidget(idx, col)
        col.show()
        self._common_cols.append(col)
        self._common_srcs.append(list(sources))

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
        if not paths:
            return
        sender = self.sender()
        gpos = (sender.viewport().mapToGlobal(pos)
                if hasattr(sender, "viewport") else self.mapToGlobal(pos))
        self._popup_context_menu(paths, gpos)

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
        menu = QMenu(self)
        is_maya = all(Path(p).suffix.lower() in MAYA_EXTENSIONS for p in paths)
        is_single = len(paths) == 1
        is_dir = is_single and os.path.isdir(paths[0])

        # ── 複数フォルダの平坦結果表示（2つ以上フォルダ選択時） ─────────
        sel_dirs = [p for p in paths if os.path.isdir(p)]
        if len(sel_dirs) >= 2:
            flat_act = menu.addAction("▤  選択フォルダを平坦結果表示")
            flat_act.triggered.connect(lambda: self._on_flat_request(sel_dirs))
            menu.addSeparator()

        # ── Maya actions ──────────────────────────────────────────────
        if is_maya and is_single:
            open_act = menu.addAction("🗂  Maya で開く")
            open_act.triggered.connect(lambda: self._dispatch_action("open", paths[0]))
            import_act = menu.addAction("⬇  Maya にインポート")
            import_act.triggered.connect(lambda: self._dispatch_action("import", paths[0]))
            ref_act = menu.addAction("🔗  Maya にリファレンス")
            ref_act.triggered.connect(lambda: self._dispatch_action("reference", paths[0]))
            menu.addSeparator()

        # ── General actions ───────────────────────────────────────────
        if is_single:
            open_ext_act = menu.addAction("🖥  関連付けアプリで開く")
            open_ext_act.triggered.connect(lambda: open_with_default_app(paths[0]))

            reveal_act = menu.addAction("📁  エクスプローラーで表示")
            reveal_act.triggered.connect(lambda: reveal_in_explorer(paths[0]))
            menu.addSeparator()

        # ── パスをコピー（単数/複数対応） ─────────────────────────────
        copy_path_act = menu.addAction("📋  ファイルパスをコピー")
        copy_path_act.triggered.connect(lambda: self._copy_paths_to_clipboard(paths))
        menu.addSeparator()

        # ── Bookmark ─────────────────────────────────────────────────
        bm_act = menu.addAction("⭐  ブックマークに追加")
        bm_act.triggered.connect(lambda: self._add_to_bookmarks(paths))
        menu.addSeparator()

        # ── File ops ─────────────────────────────────────────────────
        copy_act = menu.addAction("📋  コピー...")
        copy_act.triggered.connect(lambda: self._copy_dialog(paths))

        move_act = menu.addAction("✂  移動...")
        move_act.triggered.connect(lambda: self._move_dialog(paths))

        rename_act = menu.addAction("✏  名前変更...")
        rename_act.triggered.connect(lambda: self._rename_dialog(paths))
        rename_act.setEnabled(is_single)

        menu.addSeparator()

        del_act = menu.addAction("🗑  削除")
        del_act.triggered.connect(lambda: self._delete_confirm(paths))
        del_act.setShortcut("Delete")

        menu.addSeparator()

        # ── Properties ───────────────────────────────────────────────
        if is_single:
            prop_act = menu.addAction("ℹ  プロパティ")
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

    def _copy_dialog(self, paths: List[str]):
        dst = QFileDialog.getExistingDirectory(self, "コピー先を選択")
        if dst:
            try:
                results = copy_items(paths, dst)
                self.status_message.emit(f"コピー完了: {len(results)} 件")
            except FileOperationError as e:
                QMessageBox.critical(self, "エラー", str(e))

    def _move_dialog(self, paths: List[str]):
        dst = QFileDialog.getExistingDirectory(self, "移動先を選択")
        if dst:
            try:
                results = move_items(paths, dst)
                self.status_message.emit(f"移動完了: {len(results)} 件")
            except FileOperationError as e:
                QMessageBox.critical(self, "エラー", str(e))

    def _rename_dialog(self, paths: List[str]):
        if len(paths) != 1:
            return
        old = Path(paths[0])
        new_name, ok = QInputDialog.getText(
            self, "名前変更", "新しい名前:", text=old.name
        )
        if ok and new_name:
            new_path = old.parent / new_name
            try:
                old.rename(new_path)
                self.status_message.emit(f"名前変更: {old.name} → {new_name}")
            except Exception as e:
                QMessageBox.critical(self, "エラー", str(e))

    def _delete_confirm(self, paths: List[str]):
        msg = f"{len(paths)} 件を削除しますか？"
        ret = QMessageBox.warning(self, "削除の確認", msg,
                                  QMessageBox.Yes | QMessageBox.Cancel)
        if ret == QMessageBox.Yes:
            failed = delete_items(paths)
            if failed:
                QMessageBox.warning(self, "削除エラー",
                                    f"{len(failed)} 件の削除に失敗しました:\n" +
                                    "\n".join(failed))
            else:
                self.status_message.emit(f"{len(paths)} 件を削除しました")

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

    def _clipboard_copy(self):
        self._set_clipboard(self._get_selected_paths(), move=False)

    def _clipboard_cut(self):
        self._set_clipboard(self._get_selected_paths(), move=True)

    def _paste_target_dir(self) -> Optional[str]:
        """貼り付け先: 単一フォルダ選択中ならそのフォルダ、無ければ現在地。"""
        sel = self._get_selected_paths()
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

        try:
            if move:
                results = move_items(paths, target)
                QApplication.clipboard().clear()  # 切り取りは1回限り
            else:
                results = copy_items(paths, target)
            self.status_message.emit(
                ("移動" if move else "貼り付け") + f"完了: {len(results)} 件")
        except FileOperationError as e:
            QMessageBox.critical(self, "エラー", str(e))

    def _clipboard_delete(self):
        paths = self._get_selected_paths()
        if paths:
            self._delete_confirm(paths)

    # ------------------------------------------------------------------
    # Thumbnail refresh
    # ------------------------------------------------------------------

    def _on_thumbnail_ready(self, path: str, pixmap: "QPixmap"):
        """Force repaint when a thumbnail arrives."""
        if self._thumb_view.isVisible():
            self._thumb_view.viewport().update()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _list_visible_files(self, directory: str) -> List[str]:
        try:
            return [
                os.path.join(directory, f)
                for f in os.listdir(directory)
                if os.path.isfile(os.path.join(directory, f))
            ][:64]  # Prefetch at most 64 files
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

    def set_max_depth(self, depth: int):
        self._max_depth = depth
        self._column_view.set_max_depth(depth)
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
_mfm_log("browser_panel.py loaded to EOF (r18 complete)")
