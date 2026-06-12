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
from pathlib import Path
from typing import List, Optional, Callable

from core.compat import (
    Qt, Signal, QObject,
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QToolButton,
    QSplitter, QTreeView, QColumnView, QListView,
    QSizePolicy, QFrame, QAbstractItemView, QHeaderView,
    QFileSystemModel, QSortFilterProxyModel,
    QMenu, QAction, QMessageBox, QFileDialog, QInputDialog,
    QStyledItemDelegate, QStyle, QStyleOption,
    QModelIndex, QSize, QPixmap, QPainter, QColor, QFont,
    QDir, QFileInfo, QUrl, QMimeData, QPoint,
    QFontMetrics
)
from core.compat import QtCore as _QtCore
from core.path_guard import PathProber, DriveScanner, invalidate_cache
from core.file_operations import (
    open_with_default_app, reveal_in_explorer,
    copy_items, move_items, delete_items,
    get_file_type_category, list_drives, format_size,
    FileOperationError, MAYA_EXTENSIONS, SCENE_EXTENSIONS
)
from core.thumbnail_generator import ThumbnailManager


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
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)

    def set_filter_string(self, text: str):
        self._filter = text.lower()
        self.invalidateFilter()

    def set_show_hidden(self, show: bool):
        self._show_hidden = show
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)

        # Hidden files
        if not self._show_hidden:
            name = source_model.fileName(index)
            if name.startswith("."):
                return False

        # Text filter
        if self._filter:
            name = source_model.fileName(index).lower()
            if source_model.isDir(index):
                return True  # Always show dirs so tree stays navigable
            return self._fuzzy_match(self._filter, name)

        return True

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

    def set_max_depth(self, depth: int):
        self._max_depth = depth

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

    def __init__(self, settings_manager, thumb_manager: ThumbnailManager, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        self._thumb_mgr = thumb_manager
        self._thumb_mgr.thumbnail_ready.connect(self._on_thumbnail_ready)

        self._current_path = str(Path.home())
        self._max_depth = self._sm.get("column_max_depth", 4)
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
        for icon_text, tip, slot in [
            ("←", "戻る", self._go_back),
            ("→", "進む", self._go_forward),
            ("↑", "上へ", self._go_up),
        ]:
            btn = QToolButton()
            btn.setText(icon_text)
            btn.setToolTip(tip)
            btn.setFixedSize(28, 28)
            btn.clicked.connect(slot)
            tb_layout.addWidget(btn)

        # Address bar
        self._addr_bar = QLineEdit()
        self._addr_bar.setPlaceholderText("パスを入力...")
        self._addr_bar.returnPressed.connect(lambda: self._navigate(self._addr_bar.text()))
        tb_layout.addWidget(self._addr_bar)

        # Filter
        tb_layout.addWidget(QLabel("🔍"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("フィルター...")
        self._filter_edit.setFixedWidth(140)
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        tb_layout.addWidget(self._filter_edit)

        # View mode
        self._view_mode_combo = QComboBox()
        self._view_mode_combo.addItems(["カラム", "リスト", "サムネイル"])
        self._view_mode_combo.currentIndexChanged.connect(self._on_view_mode_changed)
        tb_layout.addWidget(self._view_mode_combo)

        # Sort
        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["名前", "種類", "更新日時"])
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        tb_layout.addWidget(self._sort_combo)

        layout.addWidget(toolbar)

        # ── Main view stack ───────────────────────────────────────────
        self._view_stack = QSplitter(Qt.Horizontal)

        # Shared filesystem model
        self._fs_model = QFileSystemModel()
        self._fs_model.setRootPath("")
        self._fs_model.setFilter(
            QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Files
        )

        # Proxy model
        self._proxy = FileFilterProxyModel()
        self._proxy.setSourceModel(self._fs_model)
        self._proxy.setSortRole(Qt.DisplayRole)

        # Column view
        self._column_view = CappedColumnView(self._max_depth)
        self._column_view.setModel(self._proxy)
        self._column_view.activated.connect(self._on_item_activated)
        self._column_view.clicked.connect(self._on_item_clicked)
        self._column_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._column_view.customContextMenuRequested.connect(self._show_context_menu)
        self._column_view.setDragEnabled(True)
        self._column_view.setAcceptDrops(True)
        self._column_view.setDropIndicatorShown(True)
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
        self._thumb_view.setDragEnabled(True)
        self._thumb_view.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self._view_stack.addWidget(self._column_view)
        self._view_stack.addWidget(self._thumb_view)
        self._thumb_view.hide()

        layout.addWidget(self._view_stack)

        # ── History navigation ────────────────────────────────────────
        self._history: List[str] = []
        self._history_index: int = -1

        # Drag-drop accept onto panel itself
        self.setAcceptDrops(True)

        # N-2: Space Quick Look 用イベントフィルタ
        self._column_view.installEventFilter(self)
        self._thumb_view.installEventFilter(self)

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
        """到達可能性を非同期確認してから移動する（N-1: UIを止めない）。"""
        path = os.path.normpath(path)
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

        source_index = self._fs_model.index(path)
        proxy_index = self._proxy.mapFromSource(source_index)

        if os.path.isdir(path):
            self._column_view.setRootIndex(proxy_index)
            self._thumb_view.setRootIndex(proxy_index)
            self.directory_changed.emit(path)
        else:
            parent = str(Path(path).parent)
            source_parent = self._fs_model.index(parent)
            proxy_parent = self._proxy.mapFromSource(source_parent)
            self._column_view.setRootIndex(proxy_parent)
            self._thumb_view.setRootIndex(proxy_parent)
            # Select the file
            self._column_view.setCurrentIndex(proxy_index)

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

    def _go_back(self):
        if self._history_index > 0:
            self._history_index -= 1
            self._navigate(self._history[self._history_index], add_to_history=False)

    def _go_forward(self):
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self._navigate(self._history[self._history_index], add_to_history=False)

    def _go_up(self):
        parent = str(Path(self._current_path).parent)
        if parent != self._current_path:
            self._navigate(parent)

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

    def _on_item_clicked(self, proxy_index: QModelIndex):
        path = self._resolve_path(proxy_index)
        if os.path.isdir(path):
            if self._column_view.isVisible():
                # カラムビュー: QColumnViewのネイティブ列展開に任せ、
                # ルートは変更しない（変更すると1カラム表示になり、
                # 内部カラム再構築との競合でクラッシュもする）
                self._set_current_path(path)
            else:
                self._navigate(path)
            return
        action = self._sm.get("single_click_action", "preview")
        self._dispatch_action(action, path)
        self._sync_quick_look(path)
        self.selection_changed.emit([path])

    def _on_item_activated(self, proxy_index: QModelIndex):
        path = self._resolve_path(proxy_index)
        if os.path.isdir(path):
            if self._column_view.isVisible():
                self._set_current_path(path)  # カラム展開を維持（ルート不変）
            else:
                self._navigate(path)
            return
        action = self._sm.get("double_click_action", "open")
        self._dispatch_action(action, path)

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

    def _show_context_menu(self, pos: QPoint):
        paths = self._get_selected_paths()
        if not paths:
            return

        menu = QMenu(self)
        is_maya = all(Path(p).suffix.lower() in MAYA_EXTENSIONS for p in paths)
        is_single = len(paths) == 1
        is_dir = is_single and os.path.isdir(paths[0])

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

        menu.exec_(self.sender().viewport().mapToGlobal(pos)
                   if hasattr(self.sender(), "viewport")
                   else self.mapToGlobal(pos))

    # ------------------------------------------------------------------
    # File operations (UI wrappers)
    # ------------------------------------------------------------------

    def _add_to_bookmarks(self, paths: List[str]):
        self.status_message.emit(f"ブックマークに追加: {len(paths)} 件")
        # Emit to main window which holds BookmarkManager
        # (connected externally)

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
    # Drag & Drop (accept from external)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.exists(path):
                ext = Path(path).suffix.lower()
                if ext in MAYA_EXTENSIONS and self._on_open:
                    self._on_open(path)
                else:
                    self._navigate(os.path.dirname(path) if os.path.isfile(path) else path)

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
