"""
Bookmark Panel
==============
QTreeWidget-based panel showing the bookmark tree.

Features
--------
* Folder / directory / file nodes with icons
* Drag-and-drop reorder (within the tree)
* Hover shows full path in tooltip
* Context menu: navigate, open, import, reference, rename, remove, set color
* Accepts drops from the browser panel (file paths → add bookmark)
* Emits navigate_requested(path) for the browser to pick up
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from core.compat import (
    Qt, Signal,
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QToolButton, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView,
    QMenu, QAction, QInputDialog, QMessageBox,
    QColor, QFont, QSize, QIcon, QPixmap, QPainter,
    QUrl, QMimeData, QPoint
)
from core.bookmark_manager import BookmarkManager, BookmarkNode


# ---------------------------------------------------------------------------
# Item roles
# ---------------------------------------------------------------------------

ROLE_ID   = Qt.UserRole
ROLE_TYPE = Qt.UserRole + 1
ROLE_PATH = Qt.UserRole + 2

ICON_FOLDER = "📁"
ICON_DIR    = "🗂"
ICON_FILE   = "📄"
ICON_MAYA   = "🎬"


def _icon_for(node: BookmarkNode) -> str:
    btype = node.get("type", "generic")
    if btype == "folder":
        return ICON_FOLDER
    path = node.get("path", "")
    ext = Path(path).suffix.lower()
    if ext in (".ma", ".mb"):
        return ICON_MAYA
    if btype == "directory":
        return ICON_DIR
    return ICON_FILE


def _make_text_icon(text: str, color: str = "#888888") -> QPixmap:
    pm = QPixmap(20, 20)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setPen(QColor(color))
    f = QFont()
    f.setPointSize(12)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, text)
    p.end()
    return pm


# ---------------------------------------------------------------------------
# Bookmark Tree Widget
# ---------------------------------------------------------------------------

class BookmarkTree(QTreeWidget):
    """Internal tree with custom drag-and-drop."""

    item_dropped = Signal(str, str, str)  # moved_id, new_parent_id, before_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(1)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setIconSize(QSize(18, 18))

    def dropEvent(self, event):
        dragged_item = self.currentItem()
        if dragged_item is None:
            event.ignore()
            return

        target_item = self.itemAt(event.pos())
        drop_indicator = self.dropIndicatorPosition()

        dragged_id = dragged_item.data(0, ROLE_ID)
        parent_id = None
        before_id = None

        if target_item:
            target_id = target_item.data(0, ROLE_ID)
            target_type = target_item.data(0, ROLE_TYPE)

            if drop_indicator == QAbstractItemView.OnItem and target_type == "folder":
                parent_id = target_id
            elif drop_indicator == QAbstractItemView.AboveItem:
                p = target_item.parent()
                parent_id = p.data(0, ROLE_ID) if p else None
                before_id = target_id
            elif drop_indicator == QAbstractItemView.BelowItem:
                p = target_item.parent()
                parent_id = p.data(0, ROLE_ID) if p else None
                # before = next sibling
                idx = (target_item.parent() or self.invisibleRootItem()).indexOfChild(target_item)
                next_item = (target_item.parent() or self.invisibleRootItem()).child(idx + 1)
                before_id = next_item.data(0, ROLE_ID) if next_item else None

        self.item_dropped.emit(dragged_id or "", parent_id or "", before_id or "")
        event.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        event.acceptProposedAction()


# ---------------------------------------------------------------------------
# Bookmark Panel
# ---------------------------------------------------------------------------

class BookmarkPanel(QWidget):
    """
    Signals
    -------
    navigate_requested(path)   : browser should navigate to this path
    open_requested(path)       : open file in Maya
    import_requested(path)     : import file in Maya
    reference_requested(path)  : reference file in Maya
    """

    navigate_requested  = Signal(str)
    open_requested      = Signal(str)
    import_requested    = Signal(str)
    reference_requested = Signal(str)

    def __init__(self, bookmark_manager: BookmarkManager, parent=None):
        super().__init__(parent)
        self._bm = bookmark_manager
        self._bm.register_on_change(self._on_bookmarks_changed)
        self._build_ui()
        self._populate()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar
        header = QWidget()
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(4, 4, 4, 4)
        h_layout.setSpacing(4)
        h_layout.addWidget(QLabel("⭐ ブックマーク"))
        h_layout.addStretch()

        add_folder_btn = QToolButton()
        add_folder_btn.setText("📁+")
        add_folder_btn.setToolTip("フォルダを追加")
        add_folder_btn.clicked.connect(self._add_folder)
        h_layout.addWidget(add_folder_btn)

        layout.addWidget(header)

        # Tree
        self._tree = BookmarkTree()
        self._tree.item_dropped.connect(self._on_item_dropped)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.setAcceptDrops(True)
        layout.addWidget(self._tree)

        # Drag-drop from external (file paths)
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # Populate
    # ------------------------------------------------------------------

    def _populate(self):
        self._tree.blockSignals(True)
        self._tree.clear()
        for node in self._bm.get_tree():
            item = self._build_item(node)
            self._tree.addTopLevelItem(item)
        self._tree.expandAll()
        self._tree.blockSignals(False)

    def _build_item(self, node: BookmarkNode) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(0, ROLE_ID,   node["id"])
        item.setData(0, ROLE_TYPE, node.get("type", "generic"))
        item.setData(0, ROLE_PATH, node.get("path", ""))

        label = node.get("name", "")
        icon  = _icon_for(node)
        item.setText(0, f"{icon}  {label}")

        color = node.get("color")
        if color:
            item.setForeground(0, QColor(color))

        # Tooltip = full path
        path = node.get("path", "")
        if path:
            item.setToolTip(0, path)

        # Children
        for child in node.get("children", []):
            item.addChild(self._build_item(child))

        return item

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _on_bookmarks_changed(self, tree):
        self._populate()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _col: int):
        btype = item.data(0, ROLE_TYPE)
        path  = item.data(0, ROLE_PATH)
        if btype == "folder" or not path:
            return
        self.navigate_requested.emit(path)

    def _on_item_dropped(self, moved_id: str, parent_id: str, before_id: str):
        self._bm.move(
            moved_id,
            parent_id or None,
            before_id or None,
        )

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos: QPoint):
        item = self._tree.itemAt(pos)
        menu = QMenu(self)

        if item:
            btype = item.data(0, ROLE_TYPE)
            path  = item.data(0, ROLE_PATH)
            bid   = item.data(0, ROLE_ID)

            if path:
                nav_act = menu.addAction("🗂  ここへ移動")
                nav_act.triggered.connect(lambda: self.navigate_requested.emit(path))

            if btype in ("file",) and path:
                menu.addSeparator()
                open_act = menu.addAction("🎬  Maya で開く")
                open_act.triggered.connect(lambda: self.open_requested.emit(path))
                imp_act = menu.addAction("⬇  インポート")
                imp_act.triggered.connect(lambda: self.import_requested.emit(path))
                ref_act = menu.addAction("🔗  リファレンス")
                ref_act.triggered.connect(lambda: self.reference_requested.emit(path))

            menu.addSeparator()

            ren_act = menu.addAction("✏  名前変更...")
            ren_act.triggered.connect(lambda: self._rename_item(item, bid))

            color_menu = menu.addMenu("🎨  色を設定")
            for name, hex_color in [
                ("赤", "#E87070"), ("オレンジ", "#E8A070"),
                ("黄", "#E8E070"), ("緑", "#70E870"),
                ("青", "#70A0E8"), ("なし", None)
            ]:
                act = color_menu.addAction(name)
                act.triggered.connect(lambda checked=False, c=hex_color: self._bm.set_color(bid, c))

            menu.addSeparator()
            del_act = menu.addAction("🗑  削除")
            del_act.triggered.connect(lambda: self._remove_item(bid))

        menu.addSeparator()
        add_folder_act = menu.addAction("📁  新しいフォルダ...")
        add_folder_act.triggered.connect(self._add_folder)

        menu.exec_(self._tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _add_folder(self):
        name, ok = QInputDialog.getText(self, "新しいフォルダ", "フォルダ名:")
        if ok and name:
            # Add under selected folder if possible
            selected = self._tree.currentItem()
            parent_id = None
            if selected and selected.data(0, ROLE_TYPE) == "folder":
                parent_id = selected.data(0, ROLE_ID)
            self._bm.add_folder(name, parent_folder_id=parent_id)

    def _rename_item(self, item: QTreeWidgetItem, bid: str):
        old_name = item.text(0).split("  ", 1)[-1]
        new_name, ok = QInputDialog.getText(self, "名前変更", "新しい名前:", text=old_name)
        if ok and new_name:
            self._bm.rename(bid, new_name)

    def _remove_item(self, bid: str):
        ret = QMessageBox.question(self, "削除確認",
                                   "このブックマークを削除しますか？",
                                   QMessageBox.Yes | QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._bm.remove(bid)

    # ------------------------------------------------------------------
    # Public: add bookmark from browser panel
    # ------------------------------------------------------------------

    def add_path(self, path: str, btype: str = "directory"):
        """Called by BrowserPanel context menu 'Add bookmark'."""
        if btype == "directory":
            self._bm.add_directory(path)
        else:
            self._bm.add_file(path)

    # ------------------------------------------------------------------
    # Accept drops from browser / OS
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            import os
            for url in urls:
                p = url.toLocalFile()
                if os.path.isdir(p):
                    self._bm.add_directory(p)
                elif os.path.isfile(p):
                    self._bm.add_file(p)
