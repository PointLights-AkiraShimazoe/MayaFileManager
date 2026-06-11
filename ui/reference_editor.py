"""
Reference Content Editor
=========================
Mayaシーン内の既存リファレンスを一覧表示し、
パス変更・Namespace 変更・ロード状態切替・削除を行うダイアログ。

Maya セッション内で開いた場合のみ実際の操作が可能。
スタンドアロンでは .ma ファイルを直接パース（テキスト）して一覧を表示する。
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.compat import (
    Qt, Signal,
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QToolButton, QLineEdit, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QFrame, QCheckBox,
    QMenu, QAction, QMessageBox, QFileDialog, QInputDialog,
    QColor, QSize, QSplitter, QTextEdit
)
from core.maya_version import is_running_inside_maya


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class ReferenceEntry:
    """Represents one reference node."""

    def __init__(self, ref_node: str, path: str, namespace: str,
                 loaded: bool, is_locked: bool = False):
        self.ref_node  = ref_node    # e.g. "WPN_swordRN"
        self.path      = path        # resolved file path
        self.namespace = namespace
        self.loaded    = loaded
        self.is_locked = is_locked

    def to_row(self) -> List[str]:
        return [
            self.ref_node,
            self.namespace,
            Path(self.path).name,
            "✓ ロード済" if self.loaded else "✗ アンロード",
            str(Path(self.path).parent),
        ]


# ---------------------------------------------------------------------------
# Reference Table
# ---------------------------------------------------------------------------

class ReferenceTable(QTableWidget):

    HEADERS = ["リファレンスノード", "Namespace", "ファイル名", "状態", "ディレクトリ"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(self.HEADERS))
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(3, 100)
        self.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().hide()
        self.setAlternatingRowColors(True)

    def populate(self, entries: List[ReferenceEntry]):
        self.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            for col, text in enumerate(entry.to_row()):
                item = QTableWidgetItem(text)
                if not entry.loaded:
                    item.setForeground(QColor("#888888"))
                if col == 3:  # Status column
                    if entry.loaded:
                        item.setForeground(QColor("#70C870"))
                    else:
                        item.setForeground(QColor("#E87070"))
                self.setItem(row, col, item)


# ---------------------------------------------------------------------------
# Reference Editor Dialog
# ---------------------------------------------------------------------------

class ReferenceEditor(QDialog):

    def __init__(self, parent=None, ma_file_path: Optional[str] = None):
        super().__init__(parent)
        self._inside_maya = is_running_inside_maya()
        self._ma_file_path = ma_file_path
        self._entries: List[ReferenceEntry] = []

        title = "リファレンス エディタ"
        if not self._inside_maya and ma_file_path:
            title += f"  —  {Path(ma_file_path).name}"
        self.setWindowTitle(title)
        self.setMinimumSize(900, 560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._build_ui()
        self._load_references()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Toolbar ───────────────────────────────────────────────────
        toolbar = QHBoxLayout()

        refresh_btn = QPushButton("🔄 更新")
        refresh_btn.clicked.connect(self._load_references)
        toolbar.addWidget(refresh_btn)

        if not self._inside_maya and not self._ma_file_path:
            open_btn = QPushButton("📂 .ma ファイルを開く…")
            open_btn.clicked.connect(self._open_ma_file)
            toolbar.addWidget(open_btn)

        toolbar.addStretch()

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(self._status_label)
        root.addLayout(toolbar)

        # ── Table ─────────────────────────────────────────────────────
        self._table = ReferenceTable()
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.doubleClicked.connect(self._edit_selected)
        root.addWidget(self._table)

        # ── Edit area ─────────────────────────────────────────────────
        edit_group = QGroupBox("選択したリファレンスを編集")
        edit_layout = QGridLayout(edit_group)
        edit_layout.setSpacing(8)

        edit_layout.addWidget(QLabel("ファイルパス:"), 0, 0)
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("リファレンスファイルのパス")
        edit_layout.addWidget(self._path_edit, 0, 1)

        browse_btn = QToolButton()
        browse_btn.setText("…")
        browse_btn.clicked.connect(self._browse_ref_path)
        edit_layout.addWidget(browse_btn, 0, 2)

        edit_layout.addWidget(QLabel("Namespace:"), 1, 0)
        self._ns_edit = QLineEdit()
        self._ns_edit.setPlaceholderText("namespace")
        edit_layout.addWidget(self._ns_edit, 1, 1)

        self._loaded_cb = QCheckBox("ロード済み")
        edit_layout.addWidget(self._loaded_cb, 1, 2)

        # Action buttons
        action_row = QHBoxLayout()
        self._apply_change_btn = QPushButton("✓ 変更を適用")
        self._apply_change_btn.setEnabled(False)
        self._apply_change_btn.clicked.connect(self._apply_change)
        action_row.addWidget(self._apply_change_btn)

        self._reload_btn = QPushButton("↺ リロード")
        self._reload_btn.setEnabled(False)
        self._reload_btn.clicked.connect(self._reload_reference)
        action_row.addWidget(self._reload_btn)

        self._remove_btn = QPushButton("🗑 削除")
        self._remove_btn.setEnabled(False)
        self._remove_btn.setStyleSheet("color: #E87070;")
        self._remove_btn.clicked.connect(self._remove_reference)
        action_row.addWidget(self._remove_btn)

        action_row.addStretch()
        edit_layout.addLayout(action_row, 2, 0, 1, 3)

        root.addWidget(edit_group)

        # ── Raw path viewer (MA parse mode) ──────────────────────────
        if not self._inside_maya:
            raw_group = QGroupBox("参照パス一覧（テキスト）")
            raw_layout = QVBoxLayout(raw_group)
            self._raw_text = QTextEdit()
            self._raw_text.setReadOnly(True)
            self._raw_text.setMaximumHeight(120)
            self._raw_text.setStyleSheet("font-family: monospace; font-size: 11px;")
            raw_layout.addWidget(self._raw_text)
            root.addWidget(raw_group)
        else:
            self._raw_text = None

        # ── Footer ────────────────────────────────────────────────────
        footer = QHBoxLayout()
        if not self._inside_maya:
            footer.addWidget(QLabel("⚠ Maya外ではファイルパス変更のみ可能（.ma ファイルのテキスト編集）"))
        footer.addStretch()
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    # ------------------------------------------------------------------
    # Load references
    # ------------------------------------------------------------------

    def _load_references(self):
        if self._inside_maya:
            self._entries = self._load_from_maya()
        elif self._ma_file_path and os.path.isfile(self._ma_file_path):
            self._entries = self._parse_ma_file(self._ma_file_path)
        else:
            self._entries = []

        self._table.populate(self._entries)
        count = len(self._entries)
        loaded = sum(1 for e in self._entries if e.loaded)
        self._status_label.setText(f"{count} 件  （ロード: {loaded}  アンロード: {count - loaded}）")

        if self._raw_text and self._ma_file_path:
            self._raw_text.setPlainText(self._build_raw_text())

    def _load_from_maya(self) -> List[ReferenceEntry]:
        try:
            import maya.cmds as cmds
            refs = cmds.file(query=True, reference=True) or []
            entries = []
            for ref_path in refs:
                try:
                    ref_node = cmds.referenceQuery(ref_path, referenceNode=True)
                    ns = cmds.referenceQuery(ref_path, namespace=True) or ""
                    loaded = cmds.referenceQuery(ref_path, isLoaded=True)
                    entries.append(ReferenceEntry(ref_node, ref_path, ns.lstrip(":"), loaded))
                except Exception:
                    pass
            return entries
        except Exception as e:
            QMessageBox.warning(self, "エラー", f"Maya からの読み込みに失敗: {e}")
            return []

    @staticmethod
    def _parse_ma_file(path: str) -> List[ReferenceEntry]:
        """
        .ma ファイルをテキストパースして file -r コマンドを抽出する。

        典型的な構文例:
            file -rdi 1 -ns "CHAR" -rfn "CHARR" -op "v=0;" -typ "mayaAscii"
                 "/projects/chr_hero.ma";
        """
        entries: List[ReferenceEntry] = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            # Match multi-line file -r ... "path"; blocks
            block_re = re.compile(
                r'file\s+-r[^;]*?(?:-ns\s+"([^"]*)")?[^;]*?(?:-rfn\s+"([^"]*)")?[^;]*?"([^"]+)"\s*;',
                re.DOTALL | re.MULTILINE
            )
            for m in block_re.finditer(content):
                ns      = m.group(1) or ""
                rfn     = m.group(2) or ""
                fp      = m.group(3)
                entries.append(ReferenceEntry(
                    ref_node  = rfn or Path(fp).stem + "RN",
                    path      = fp,
                    namespace = ns,
                    loaded    = True,   # MA file always shows as loaded
                ))
        except Exception as e:
            print(f"[ReferenceEditor] parse error: {e}")
        return entries

    def _build_raw_text(self) -> str:
        lines = []
        for e in self._entries:
            lines.append(f"[{e.ref_node}]  NS={e.namespace}  →  {e.path}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_selection_changed(self):
        rows = self._table.selectedItems()
        has_sel = bool(rows)
        self._apply_change_btn.setEnabled(has_sel and self._inside_maya)
        self._reload_btn.setEnabled(has_sel and self._inside_maya)
        self._remove_btn.setEnabled(has_sel)

        if has_sel:
            row = self._table.currentRow()
            if 0 <= row < len(self._entries):
                entry = self._entries[row]
                self._path_edit.setText(entry.path)
                self._ns_edit.setText(entry.namespace)
                self._loaded_cb.setChecked(entry.loaded)

    def _edit_selected(self, index):
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _browse_ref_path(self):
        d = QFileDialog.getOpenFileName(
            self, "リファレンスファイルを選択", self._path_edit.text() or "",
            "Maya / FBX (*.ma *.mb *.fbx);;All (*.*)"
        )
        if d[0]:
            self._path_edit.setText(d[0])

    def _apply_change(self):
        if not self._inside_maya:
            return
        row = self._table.currentRow()
        if row < 0 or row >= len(self._entries):
            return
        entry = self._entries[row]
        new_path = self._path_edit.text().strip()
        new_ns   = self._ns_edit.text().strip()
        new_loaded = self._loaded_cb.isChecked()

        try:
            import maya.cmds as cmds

            # Change file path if needed
            if new_path and new_path != entry.path:
                cmds.file(new_path, loadReference=entry.ref_node)

            # Change namespace
            if new_ns and new_ns != entry.namespace:
                cmds.file(referenceNode=entry.ref_node,
                          edit=True, namespace=new_ns)

            # Load / unload
            if new_loaded != entry.loaded:
                if new_loaded:
                    cmds.file(loadReference=entry.ref_node)
                else:
                    cmds.file(unloadReference=entry.ref_node)

            self._load_references()
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e))

    def _reload_reference(self):
        if not self._inside_maya:
            return
        row = self._table.currentRow()
        if row < 0 or row >= len(self._entries):
            return
        entry = self._entries[row]
        try:
            import maya.cmds as cmds
            cmds.file(loadReference=entry.ref_node)
            self._load_references()
        except Exception as e:
            QMessageBox.critical(self, "リロードエラー", str(e))

    def _remove_reference(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._entries):
            return
        entry = self._entries[row]

        ret = QMessageBox.question(
            self, "削除確認",
            f"「{entry.ref_node}」を削除しますか？\n{entry.path}",
            QMessageBox.Yes | QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return

        if self._inside_maya:
            try:
                import maya.cmds as cmds
                cmds.file(removeReference=True, referenceNode=entry.ref_node)
                self._load_references()
            except Exception as e:
                QMessageBox.critical(self, "エラー", str(e))
        else:
            # Standalone: edit .ma file text
            if not self._ma_file_path:
                QMessageBox.warning(self, "エラー", ".ma ファイルが開かれていません。")
                return
            try:
                self._remove_from_ma_file(entry)
                self._load_references()
            except Exception as e:
                QMessageBox.critical(self, "ファイル編集エラー", str(e))

    def _remove_from_ma_file(self, entry: ReferenceEntry):
        """Best-effort removal of the reference block from .ma text."""
        with open(self._ma_file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Build pattern that matches the specific reference block
        path_escaped = re.escape(entry.path)
        pattern = re.compile(
            r'file\s+-r[^;]*?"' + path_escaped + r'"\s*;',
            re.DOTALL
        )
        new_content = pattern.sub("", content)

        if new_content == content:
            raise ValueError(f"パターンが見つかりませんでした: {entry.path}")

        # Backup
        backup = self._ma_file_path + ".mfm_bak"
        import shutil
        shutil.copy2(self._ma_file_path, backup)

        with open(self._ma_file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    def _open_ma_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, ".ma ファイルを開く", "",
            "Maya ASCII (*.ma);;All (*.*)"
        )
        if path:
            self._ma_file_path = path
            self.setWindowTitle(f"リファレンス エディタ  —  {Path(path).name}")
            self._load_references()

    # ------------------------------------------------------------------
    # Open selected ref in browser
    # ------------------------------------------------------------------

    def get_selected_path(self) -> Optional[str]:
        row = self._table.currentRow()
        if 0 <= row < len(self._entries):
            return self._entries[row].path
        return None
