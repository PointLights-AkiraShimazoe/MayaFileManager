"""
Quick-Nav Preset Editor
========================
クイックナビゲーションバーのプリセットを管理するダイアログ。

プリセット = ナビゲーションボタンのリスト
ボタン = { label, path, icon(optional) }

操作
----
- プリセットの追加 / 削除 / 複製
- ボタンの追加 / 削除 / 上下移動
- ラベルとパスの編集
- D&D でパスをドロップして追加可能
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.compat import (
    Qt, Signal,
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QToolButton, QLineEdit, QComboBox,
    QListWidget, QListWidgetItem, QAbstractItemView,
    QGroupBox, QSplitter,
    QMenu, QAction, QMessageBox, QFileDialog, QInputDialog,
    QSize, QUrl
)


# ---------------------------------------------------------------------------
# Nav Item Row Widget
# ---------------------------------------------------------------------------

class NavItemRow(QWidget):
    """Single row: label + path + browse + up/down/delete."""

    remove_requested = Signal(object)
    move_up_requested = Signal(object)
    move_down_requested = Signal(object)
    changed = Signal()

    def __init__(self, item: Dict, parent=None):
        super().__init__(parent)
        self._item = item
        self._build()
        self.setAcceptDrops(True)

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        # Move buttons
        up_btn = QToolButton()
        up_btn.setText("▲")
        up_btn.setFixedSize(24, 24)
        up_btn.setToolTip("上へ")
        up_btn.clicked.connect(lambda: self.move_up_requested.emit(self))
        layout.addWidget(up_btn)

        down_btn = QToolButton()
        down_btn.setText("▼")
        down_btn.setFixedSize(24, 24)
        down_btn.setToolTip("下へ")
        down_btn.clicked.connect(lambda: self.move_down_requested.emit(self))
        layout.addWidget(down_btn)

        # Label
        layout.addWidget(QLabel("ラベル:"))
        self._label_edit = QLineEdit(self._item.get("label", ""))
        self._label_edit.setFixedWidth(100)
        self._label_edit.setPlaceholderText("ボタン名")
        self._label_edit.textChanged.connect(self._sync)
        layout.addWidget(self._label_edit)

        # Path
        layout.addWidget(QLabel("パス:"))
        self._path_edit = QLineEdit(self._item.get("path", ""))
        self._path_edit.setPlaceholderText("/path/to/directory")
        self._path_edit.textChanged.connect(self._sync)
        layout.addWidget(self._path_edit)

        browse_btn = QToolButton()
        browse_btn.setText("…")
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

        del_btn = QToolButton()
        del_btn.setText("✕")
        del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(del_btn)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "ディレクトリを選択",
                                             self._path_edit.text() or str(Path.home()))
        if d:
            self._path_edit.setText(d)

    def _sync(self):
        self._item["label"] = self._label_edit.text()
        self._item["path"]  = self._path_edit.text()
        self.changed.emit()

    def get_item(self) -> Dict:
        return self._item

    # Drag-drop: accept folder path from OS or bookmark panel
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            p = urls[0].toLocalFile()
            if os.path.isdir(p):
                self._path_edit.setText(p)
                if not self._label_edit.text():
                    self._label_edit.setText(Path(p).name)


# ---------------------------------------------------------------------------
# Quick-nav Preset Editor Dialog
# ---------------------------------------------------------------------------

class QuickNavPresetEditor(QDialog):

    presets_saved = Signal()

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        self._presets: Dict[str, List[Dict]] = {}
        self._current_preset: Optional[str] = None
        self._rows: List[NavItemRow] = []

        self.setWindowTitle("クイックナビ プリセットエディタ")
        self.setMinimumSize(760, 500)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._build_ui()
        self._load_presets()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Left: preset list ─────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(180)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        ll.addWidget(QLabel("プリセット"))
        self._preset_list = QListWidget()
        self._preset_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._preset_list.currentRowChanged.connect(self._on_preset_selected)
        ll.addWidget(self._preset_list)

        btn_row = QHBoxLayout()
        new_btn = QPushButton("✚ 新規")
        new_btn.clicked.connect(self._new_preset)
        btn_row.addWidget(new_btn)
        dup_btn = QPushButton("⧉ 複製")
        dup_btn.clicked.connect(self._duplicate_preset)
        btn_row.addWidget(dup_btn)
        ll.addLayout(btn_row)

        del_btn = QPushButton("🗑 削除")
        del_btn.clicked.connect(self._delete_preset)
        ll.addWidget(del_btn)

        root.addWidget(left)

        # ── Right: item list ──────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)

        # Preset name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("プリセット名:"))
        self._name_edit = QLineEdit()
        self._name_edit.textChanged.connect(lambda _: None)  # name edited separately
        name_row.addWidget(self._name_edit)
        rl.addLayout(name_row)

        rl.addWidget(QLabel("ナビゲーションボタン （上から左ツールバーの順）:"))

        # Item container
        self._items_container = QWidget()
        self._items_container.setAcceptDrops(True)
        self._items_layout = QVBoxLayout(self._items_container)
        self._items_layout.setContentsMargins(0, 0, 0, 0)
        self._items_layout.setSpacing(2)
        self._items_layout.addStretch()

        from core.compat import QScrollArea
        scroll = QScrollArea()
        scroll.setWidget(self._items_container)
        scroll.setWidgetResizable(True)
        scroll.setAcceptDrops(True)
        rl.addWidget(scroll)

        add_item_btn = QPushButton("＋ ボタンを追加")
        add_item_btn.clicked.connect(self._add_item)
        rl.addWidget(add_item_btn)

        # Quick-add standard directories
        quick_row = QHBoxLayout()
        quick_row.addWidget(QLabel("クイック追加:"))
        for label, path_fn in [
            ("ホーム",      lambda: str(Path.home())),
            ("デスクトップ", lambda: str(Path.home() / "Desktop")),
            ("ドキュメント", lambda: str(Path.home() / "Documents")),
        ]:
            btn = QPushButton(label)
            p = path_fn()
            btn.clicked.connect(lambda checked=False, l=label, p=p: self._add_item(label=l, path=p))
            quick_row.addWidget(btn)
        quick_row.addStretch()
        rl.addLayout(quick_row)

        # Buttons
        btn_row2 = QHBoxLayout()
        btn_row2.addStretch()
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        btn_row2.addWidget(cancel_btn)

        save_btn = QPushButton("💾 保存して閉じる")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save_and_close)
        btn_row2.addWidget(save_btn)
        rl.addLayout(btn_row2)

        root.addWidget(right)

    # ------------------------------------------------------------------
    # Preset management
    # ------------------------------------------------------------------

    def _load_presets(self):
        import copy
        raw = self._sm.get_quick_nav_presets()
        self._presets = copy.deepcopy(raw)

        self._preset_list.blockSignals(True)
        self._preset_list.clear()
        for name in sorted(self._presets.keys()):
            self._preset_list.addItem(name)
        self._preset_list.blockSignals(False)

        # Select active preset
        active = self._sm.get("quick_nav_preset", "default")
        items = self._preset_list.findItems(active, Qt.MatchExactly)
        if items:
            self._preset_list.setCurrentItem(items[0])
        elif self._preset_list.count():
            self._preset_list.setCurrentRow(0)

    def _on_preset_selected(self, row: int):
        if row < 0:
            self._clear_items()
            return
        name = self._preset_list.item(row).text()
        self._current_preset = name
        self._name_edit.blockSignals(True)
        self._name_edit.setText(name)
        self._name_edit.blockSignals(False)
        self._populate_items(self._presets.get(name, []))

    def _new_preset(self):
        name, ok = QInputDialog.getText(self, "新しいプリセット", "プリセット名:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._presets:
            QMessageBox.warning(self, "重複", "同名のプリセットが既に存在します。")
            return
        self._presets[name] = []
        self._preset_list.addItem(name)
        items = self._preset_list.findItems(name, Qt.MatchExactly)
        if items:
            self._preset_list.setCurrentItem(items[0])

    def _duplicate_preset(self):
        if not self._current_preset:
            return
        import copy
        new_name = self._current_preset + "_copy"
        self._presets[new_name] = copy.deepcopy(self._presets[self._current_preset])
        self._preset_list.addItem(new_name)
        items = self._preset_list.findItems(new_name, Qt.MatchExactly)
        if items:
            self._preset_list.setCurrentItem(items[0])

    def _delete_preset(self):
        if not self._current_preset:
            return
        ret = QMessageBox.question(self, "削除確認",
                                   f"「{self._current_preset}」を削除しますか？",
                                   QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        del self._presets[self._current_preset]
        row = self._preset_list.currentRow()
        self._preset_list.takeItem(row)
        self._current_preset = None
        self._clear_items()

    # ------------------------------------------------------------------
    # Item management
    # ------------------------------------------------------------------

    def _populate_items(self, items: List[Dict]):
        self._clear_items()
        import copy
        for item in items:
            self._insert_row(copy.deepcopy(item))

    def _clear_items(self):
        self._rows.clear()
        while self._items_layout.count() > 1:
            w = self._items_layout.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _add_item(self, label: str = "", path: str = ""):
        item = {"label": label, "path": path}
        self._insert_row(item)
        self._sync_current_preset()

    def _insert_row(self, item: Dict):
        row = NavItemRow(item)
        row.remove_requested.connect(self._remove_row)
        row.move_up_requested.connect(self._move_row_up)
        row.move_down_requested.connect(self._move_row_down)
        row.changed.connect(self._sync_current_preset)
        self._rows.append(row)
        self._items_layout.insertWidget(self._items_layout.count() - 1, row)

    def _remove_row(self, row: NavItemRow):
        if row in self._rows:
            self._rows.remove(row)
        self._items_layout.removeWidget(row)
        row.deleteLater()
        self._sync_current_preset()

    def _move_row_up(self, row: NavItemRow):
        idx = self._rows.index(row) if row in self._rows else -1
        if idx <= 0:
            return
        self._rows.insert(idx - 1, self._rows.pop(idx))
        self._items_layout.removeWidget(row)
        self._items_layout.insertWidget(idx - 1, row)
        self._sync_current_preset()

    def _move_row_down(self, row: NavItemRow):
        idx = self._rows.index(row) if row in self._rows else -1
        if idx < 0 or idx >= len(self._rows) - 1:
            return
        self._rows.insert(idx + 1, self._rows.pop(idx))
        self._items_layout.removeWidget(row)
        self._items_layout.insertWidget(idx + 1, row)
        self._sync_current_preset()

    def _sync_current_preset(self):
        if not self._current_preset:
            return
        self._presets[self._current_preset] = [r.get_item() for r in self._rows]

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_and_close(self):
        # Handle rename
        new_name = self._name_edit.text().strip()
        if self._current_preset and new_name and new_name != self._current_preset:
            if new_name in self._presets:
                QMessageBox.warning(self, "重複", "同名のプリセットが既に存在します。")
                return
            self._presets[new_name] = self._presets.pop(self._current_preset)
            self._current_preset = new_name

        self._sync_current_preset()
        self._sm.save_quick_nav_presets(self._presets)
        self.presets_saved.emit()
        self.accept()
