"""
Reference Preset Editor
=======================
A dialog for creating / editing "Reference Presets" – bundles of:
  - Namespace
  - Files to reference (ma / mb / fbx)
  - Constraints to apply (with target names)
  - Pre/post Python or MEL scripts

Presets are persisted via SettingsManager.
When run inside Maya the "Apply" button executes the preset immediately.
Outside Maya only editing is available.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.compat import (
    Qt, Signal,
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QToolButton, QLineEdit, QTextEdit, QComboBox,
    QCheckBox, QGroupBox, QListWidget, QListWidgetItem, QAbstractItemView,
    QTabWidget, QSplitter, QFrame, QSizePolicy,
    QMenu, QAction, QMessageBox, QFileDialog, QInputDialog,
    QSize, QPoint
)
from core.maya_version import is_running_inside_maya


# ---------------------------------------------------------------------------
# Preset schema
# ---------------------------------------------------------------------------
# {
#   "name": "Weapon+Char setup",
#   "references": [
#       {"namespace": "CHAR", "path": "/proj/chr_hero.ma", "enabled": true},
#       {"namespace": "WPN",  "path": "/proj/wpn_sword.fbx","enabled": true},
#   ],
#   "constraints": [
#       {
#           "type": "parentConstraint",       # or point / orient / scale / aim
#           "source_ns": "WPN",
#           "source_node": "WPN:root",
#           "target_node": "CHAR:hand_R",
#           "maintain_offset": true,
#           "enabled": true,
#       }
#   ],
#   "scripts": [
#       {"phase": "pre",  "lang": "python", "content": "", "file": ""},
#       {"phase": "post", "lang": "mel",    "content": "", "file": ""},
#   ]
# }

CONSTRAINT_TYPES = [
    "parentConstraint",
    "pointConstraint",
    "orientConstraint",
    "scaleConstraint",
    "aimConstraint",
]


# ---------------------------------------------------------------------------
# Reference entry widget
# ---------------------------------------------------------------------------

class ReferenceEntryWidget(QFrame):

    changed = Signal()
    remove_requested = Signal(object)  # self

    def __init__(self, entry: Dict, parent=None):
        super().__init__(parent)
        self._entry = entry
        self.setFrameShape(QFrame.StyledPanel)
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        self._enabled_cb = QCheckBox()
        self._enabled_cb.setChecked(self._entry.get("enabled", True))
        self._enabled_cb.setToolTip("有効/無効")
        self._enabled_cb.toggled.connect(self._sync)
        layout.addWidget(self._enabled_cb)

        # Namespace
        layout.addWidget(QLabel("NS:"))
        self._ns_edit = QLineEdit(self._entry.get("namespace", ""))
        self._ns_edit.setFixedWidth(90)
        self._ns_edit.setPlaceholderText("CHAR")
        self._ns_edit.textChanged.connect(self._sync)
        layout.addWidget(self._ns_edit)

        # File path
        self._path_edit = QLineEdit(self._entry.get("path", ""))
        self._path_edit.setPlaceholderText("ファイルパス (.ma / .mb / .fbx)")
        self._path_edit.textChanged.connect(self._sync)
        layout.addWidget(self._path_edit)

        browse_btn = QToolButton()
        browse_btn.setText("…")
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

        del_btn = QToolButton()
        del_btn.setText("✕")
        del_btn.setToolTip("削除")
        del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(del_btn)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "リファレンスファイルを選択", "",
            "Maya / FBX (*.ma *.mb *.fbx);;All (*.*)"
        )
        if path:
            self._path_edit.setText(path)

    def _sync(self):
        self._entry["namespace"] = self._ns_edit.text()
        self._entry["path"]      = self._path_edit.text()
        self._entry["enabled"]   = self._enabled_cb.isChecked()
        self.changed.emit()

    def get_entry(self) -> Dict:
        return self._entry

    # Drag-and-drop: accept file drops
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self._path_edit.setText(urls[0].toLocalFile())


# ---------------------------------------------------------------------------
# Constraint entry widget
# ---------------------------------------------------------------------------

class ConstraintEntryWidget(QFrame):

    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, entry: Dict, parent=None):
        super().__init__(parent)
        self._entry = entry
        self.setFrameShape(QFrame.StyledPanel)
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        self._enabled_cb = QCheckBox()
        self._enabled_cb.setChecked(self._entry.get("enabled", True))
        self._enabled_cb.toggled.connect(self._sync)
        layout.addWidget(self._enabled_cb)

        # Constraint type
        self._type_combo = QComboBox()
        self._type_combo.addItems(CONSTRAINT_TYPES)
        ct = self._entry.get("type", "parentConstraint")
        idx = CONSTRAINT_TYPES.index(ct) if ct in CONSTRAINT_TYPES else 0
        self._type_combo.setCurrentIndex(idx)
        self._type_combo.currentIndexChanged.connect(self._sync)
        self._type_combo.setFixedWidth(160)
        layout.addWidget(self._type_combo)

        # Source node
        layout.addWidget(QLabel("Source:"))
        self._src_edit = QLineEdit(self._entry.get("source_node", ""))
        self._src_edit.setPlaceholderText("WPN:root")
        self._src_edit.setFixedWidth(130)
        self._src_edit.textChanged.connect(self._sync)
        layout.addWidget(self._src_edit)

        layout.addWidget(QLabel("→ Target:"))
        self._tgt_edit = QLineEdit(self._entry.get("target_node", ""))
        self._tgt_edit.setPlaceholderText("CHAR:hand_R")
        self._tgt_edit.setFixedWidth(130)
        self._tgt_edit.textChanged.connect(self._sync)
        layout.addWidget(self._tgt_edit)

        self._offset_cb = QCheckBox("Maintain Offset")
        self._offset_cb.setChecked(self._entry.get("maintain_offset", True))
        self._offset_cb.toggled.connect(self._sync)
        layout.addWidget(self._offset_cb)

        del_btn = QToolButton()
        del_btn.setText("✕")
        del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(del_btn)

    def _sync(self):
        self._entry["type"]            = self._type_combo.currentText()
        self._entry["source_node"]     = self._src_edit.text()
        self._entry["target_node"]     = self._tgt_edit.text()
        self._entry["maintain_offset"] = self._offset_cb.isChecked()
        self._entry["enabled"]         = self._enabled_cb.isChecked()
        self.changed.emit()

    def get_entry(self) -> Dict:
        return self._entry


# ---------------------------------------------------------------------------
# Script entry widget
# ---------------------------------------------------------------------------

class ScriptEntryWidget(QFrame):

    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, entry: Dict, parent=None):
        super().__init__(parent)
        self._entry = entry
        self.setFrameShape(QFrame.StyledPanel)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        top = QHBoxLayout()

        # Phase
        top.addWidget(QLabel("Phase:"))
        self._phase_combo = QComboBox()
        self._phase_combo.addItems(["pre", "post"])
        self._phase_combo.setCurrentText(self._entry.get("phase", "post"))
        self._phase_combo.currentIndexChanged.connect(self._sync)
        top.addWidget(self._phase_combo)

        # Language
        top.addWidget(QLabel("言語:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(["python", "mel"])
        self._lang_combo.setCurrentText(self._entry.get("lang", "python"))
        self._lang_combo.currentIndexChanged.connect(self._sync)
        top.addWidget(self._lang_combo)

        # File picker
        self._file_edit = QLineEdit(self._entry.get("file", ""))
        self._file_edit.setPlaceholderText("ファイルパス（空でインライン）")
        self._file_edit.textChanged.connect(self._sync)
        top.addWidget(self._file_edit)

        browse_btn = QToolButton()
        browse_btn.setText("…")
        browse_btn.clicked.connect(self._browse_file)
        top.addWidget(browse_btn)

        del_btn = QToolButton()
        del_btn.setText("✕")
        del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        top.addWidget(del_btn)

        layout.addLayout(top)

        # Inline code editor
        self._code_edit = QTextEdit()
        self._code_edit.setPlaceholderText("インラインスクリプトをここに記述…")
        self._code_edit.setPlainText(self._entry.get("content", ""))
        self._code_edit.setFixedHeight(80)
        self._code_edit.textChanged.connect(self._sync)
        layout.addWidget(self._code_edit)

    def _browse_file(self):
        lang = self._lang_combo.currentText()
        ext_filter = "Python (*.py)" if lang == "python" else "MEL (*.mel)"
        path, _ = QFileDialog.getOpenFileName(self, "スクリプトファイルを選択",
                                              "", ext_filter + ";;All (*.*)")
        if path:
            self._file_edit.setText(path)

    def _sync(self):
        self._entry["phase"]   = self._phase_combo.currentText()
        self._entry["lang"]    = self._lang_combo.currentText()
        self._entry["file"]    = self._file_edit.text()
        self._entry["content"] = self._code_edit.toPlainText()
        self.changed.emit()

    def get_entry(self) -> Dict:
        return self._entry


# ---------------------------------------------------------------------------
# Reference Preset Editor Dialog
# ---------------------------------------------------------------------------

class ReferencePresetEditor(QDialog):
    """
    Full preset editor.  Pass settings_manager to persist presets.
    """

    preset_applied = Signal(dict)   # emitted when Apply is clicked (inside Maya)

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        self._presets: Dict[str, Any] = self._sm.get_reference_presets()
        self._current_name: Optional[str] = None
        self._dirty: bool = False

        self.setWindowTitle("リファレンスプリセットエディタ")
        self.setMinimumSize(800, 640)
        self._build_ui()
        self._refresh_preset_list()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Left: preset list ─────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(200)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        ll.addWidget(QLabel("プリセット一覧"))

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

        # ── Right: editor ─────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        # Preset name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("プリセット名:"))
        self._name_edit = QLineEdit()
        self._name_edit.textChanged.connect(lambda t: self._mark_dirty())
        name_row.addWidget(self._name_edit)
        rl.addLayout(name_row)

        tabs = QTabWidget()

        # ── Tab: References ───────────────────────────────────────────
        self._ref_tab = QWidget()
        ref_layout = QVBoxLayout(self._ref_tab)

        ref_toolbar = QHBoxLayout()
        add_ref_btn = QPushButton("＋ リファレンス追加")
        add_ref_btn.clicked.connect(self._add_reference)
        ref_toolbar.addWidget(add_ref_btn)
        ref_toolbar.addStretch()
        ref_layout.addLayout(ref_toolbar)

        self._ref_container = QWidget()
        self._ref_layout = QVBoxLayout(self._ref_container)
        self._ref_layout.setContentsMargins(0, 0, 0, 0)
        self._ref_layout.setSpacing(4)
        self._ref_layout.addStretch()

        from core.compat import QScrollArea
        ref_scroll = QScrollArea()
        ref_scroll.setWidget(self._ref_container)
        ref_scroll.setWidgetResizable(True)
        ref_layout.addWidget(ref_scroll)
        tabs.addTab(self._ref_tab, "🔗 リファレンス")

        # ── Tab: Constraints ──────────────────────────────────────────
        self._con_tab = QWidget()
        con_layout = QVBoxLayout(self._con_tab)

        con_toolbar = QHBoxLayout()
        add_con_btn = QPushButton("＋ コンストレイン追加")
        add_con_btn.clicked.connect(self._add_constraint)
        con_toolbar.addWidget(add_con_btn)
        con_toolbar.addStretch()
        con_layout.addLayout(con_toolbar)

        self._con_container = QWidget()
        self._con_layout = QVBoxLayout(self._con_container)
        self._con_layout.setContentsMargins(0, 0, 0, 0)
        self._con_layout.setSpacing(4)
        self._con_layout.addStretch()

        from core.compat import QScrollArea
        con_scroll = QScrollArea()
        con_scroll.setWidget(self._con_container)
        con_scroll.setWidgetResizable(True)
        con_layout.addWidget(con_scroll)
        tabs.addTab(self._con_tab, "⛓ コンストレイン")

        # ── Tab: Scripts ──────────────────────────────────────────────
        self._scr_tab = QWidget()
        scr_layout = QVBoxLayout(self._scr_tab)

        scr_toolbar = QHBoxLayout()
        add_scr_btn = QPushButton("＋ スクリプト追加")
        add_scr_btn.clicked.connect(self._add_script)
        scr_toolbar.addWidget(add_scr_btn)
        scr_toolbar.addStretch()
        scr_layout.addLayout(scr_toolbar)

        self._scr_container = QWidget()
        self._scr_layout = QVBoxLayout(self._scr_container)
        self._scr_layout.setContentsMargins(0, 0, 0, 0)
        self._scr_layout.setSpacing(4)
        self._scr_layout.addStretch()

        from core.compat import QScrollArea
        scr_scroll = QScrollArea()
        scr_scroll.setWidget(self._scr_container)
        scr_scroll.setWidgetResizable(True)
        scr_layout.addWidget(scr_scroll)
        tabs.addTab(self._scr_tab, "📜 スクリプト")

        rl.addWidget(tabs)

        # ── Bottom buttons ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._save_btn = QPushButton("💾 保存")
        self._save_btn.clicked.connect(self._save_preset)
        btn_row.addWidget(self._save_btn)

        btn_row.addStretch()

        self._apply_btn = QPushButton("▶  Maya に適用")
        self._apply_btn.setEnabled(is_running_inside_maya())
        self._apply_btn.clicked.connect(self._apply_preset)
        btn_row.addWidget(self._apply_btn)

        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)

        rl.addLayout(btn_row)

        root.addWidget(right)

    # ------------------------------------------------------------------
    # Preset list management
    # ------------------------------------------------------------------

    def _refresh_preset_list(self):
        self._preset_list.blockSignals(True)
        self._preset_list.clear()
        for name in sorted(self._presets.keys()):
            self._preset_list.addItem(name)
        self._preset_list.blockSignals(False)
        self._clear_editor()

    def _on_preset_selected(self, row: int):
        if row < 0:
            self._clear_editor()
            return
        name = self._preset_list.item(row).text()
        self._load_preset(name)

    def _load_preset(self, name: str):
        if self._dirty:
            ret = QMessageBox.question(self, "未保存の変更",
                                       "変更を保存しますか？",
                                       QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ret == QMessageBox.Cancel:
                return
            if ret == QMessageBox.Yes:
                self._save_preset()

        self._current_name = name
        preset = self._presets.get(name, {})
        self._name_edit.setText(name)
        self._populate_references(preset.get("references", []))
        self._populate_constraints(preset.get("constraints", []))
        self._populate_scripts(preset.get("scripts", []))
        self._dirty = False

    def _clear_editor(self):
        self._current_name = None
        self._name_edit.setText("")
        self._populate_references([])
        self._populate_constraints([])
        self._populate_scripts([])
        self._dirty = False

    # ------------------------------------------------------------------
    # Reference entries
    # ------------------------------------------------------------------

    def _populate_references(self, entries: List[Dict]):
        # Clear existing
        while self._ref_layout.count() > 1:  # Keep stretch
            item = self._ref_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for entry in entries:
            self._insert_reference_widget(dict(entry))

    def _insert_reference_widget(self, entry: Dict):
        w = ReferenceEntryWidget(entry)
        w.changed.connect(self._mark_dirty)
        w.remove_requested.connect(lambda widget: self._remove_widget(widget, self._ref_layout))
        self._ref_layout.insertWidget(self._ref_layout.count() - 1, w)
        w.setAcceptDrops(True)

    def _add_reference(self):
        entry = {"namespace": "", "path": "", "enabled": True}
        self._insert_reference_widget(entry)
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Constraint entries
    # ------------------------------------------------------------------

    def _populate_constraints(self, entries: List[Dict]):
        while self._con_layout.count() > 1:
            item = self._con_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for entry in entries:
            self._insert_constraint_widget(dict(entry))

    def _insert_constraint_widget(self, entry: Dict):
        w = ConstraintEntryWidget(entry)
        w.changed.connect(self._mark_dirty)
        w.remove_requested.connect(lambda widget: self._remove_widget(widget, self._con_layout))
        self._con_layout.insertWidget(self._con_layout.count() - 1, w)

    def _add_constraint(self):
        entry = {
            "type": "parentConstraint",
            "source_node": "", "target_node": "",
            "maintain_offset": True, "enabled": True,
        }
        self._insert_constraint_widget(entry)
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Script entries
    # ------------------------------------------------------------------

    def _populate_scripts(self, entries: List[Dict]):
        while self._scr_layout.count() > 1:
            item = self._scr_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for entry in entries:
            self._insert_script_widget(dict(entry))

    def _insert_script_widget(self, entry: Dict):
        w = ScriptEntryWidget(entry)
        w.changed.connect(self._mark_dirty)
        w.remove_requested.connect(lambda widget: self._remove_widget(widget, self._scr_layout))
        self._scr_layout.insertWidget(self._scr_layout.count() - 1, w)

    def _add_script(self):
        entry = {"phase": "post", "lang": "python", "content": "", "file": ""}
        self._insert_script_widget(entry)
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _remove_widget(self, widget, layout):
        layout.removeWidget(widget)
        widget.deleteLater()
        self._mark_dirty()

    def _mark_dirty(self):
        self._dirty = True

    def _collect_preset(self) -> Dict:
        refs, cons, scrs = [], [], []

        for i in range(self._ref_layout.count() - 1):
            w = self._ref_layout.itemAt(i).widget()
            if isinstance(w, ReferenceEntryWidget):
                refs.append(w.get_entry())

        for i in range(self._con_layout.count() - 1):
            w = self._con_layout.itemAt(i).widget()
            if isinstance(w, ConstraintEntryWidget):
                cons.append(w.get_entry())

        for i in range(self._scr_layout.count() - 1):
            w = self._scr_layout.itemAt(i).widget()
            if isinstance(w, ScriptEntryWidget):
                scrs.append(w.get_entry())

        return {"references": refs, "constraints": cons, "scripts": scrs}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _new_preset(self):
        name, ok = QInputDialog.getText(self, "新しいプリセット", "プリセット名:")
        if ok and name:
            if name in self._presets:
                QMessageBox.warning(self, "重複", "同名のプリセットが既に存在します。")
                return
            self._presets[name] = {"references": [], "constraints": [], "scripts": []}
            self._sm.save_reference_presets(self._presets)
            self._refresh_preset_list()
            # Select new
            items = self._preset_list.findItems(name, Qt.MatchExactly)
            if items:
                self._preset_list.setCurrentItem(items[0])

    def _duplicate_preset(self):
        if not self._current_name:
            return
        import copy
        new_name = self._current_name + "_copy"
        self._presets[new_name] = copy.deepcopy(self._presets[self._current_name])
        self._sm.save_reference_presets(self._presets)
        self._refresh_preset_list()

    def _delete_preset(self):
        if not self._current_name:
            return
        ret = QMessageBox.question(self, "削除確認",
                                   f"「{self._current_name}」を削除しますか？",
                                   QMessageBox.Yes | QMessageBox.No)
        if ret == QMessageBox.Yes:
            del self._presets[self._current_name]
            self._sm.save_reference_presets(self._presets)
            self._refresh_preset_list()

    def _save_preset(self):
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "エラー", "プリセット名を入力してください。")
            return

        # Handle rename
        if self._current_name and self._current_name != name:
            del self._presets[self._current_name]

        self._presets[name] = self._collect_preset()
        self._current_name = name
        self._sm.save_reference_presets(self._presets)
        self._refresh_preset_list()

        items = self._preset_list.findItems(name, Qt.MatchExactly)
        if items:
            self._preset_list.setCurrentItem(items[0])

        self._dirty = False

    def _apply_preset(self):
        if not is_running_inside_maya():
            return
        preset = self._collect_preset()
        preset["name"] = self._name_edit.text()
        try:
            _execute_preset_in_maya(preset)
            self.preset_applied.emit(preset)
        except Exception as e:
            QMessageBox.critical(self, "適用エラー", str(e))

    # ------------------------------------------------------------------
    # Preset list CRUD buttons
    # ------------------------------------------------------------------

    def _new_preset(self):
        name, ok = QInputDialog.getText(self, "新しいプリセット", "プリセット名:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._presets:
            QMessageBox.warning(self, "重複", "同名のプリセットが既に存在します。")
            return
        self._presets[name] = {"references": [], "constraints": [], "scripts": []}
        self._sm.save_reference_presets(self._presets)
        self._refresh_preset_list()
        items = self._preset_list.findItems(name, Qt.MatchExactly)
        if items:
            self._preset_list.setCurrentItem(items[0])


# ---------------------------------------------------------------------------
# Maya execution
# ---------------------------------------------------------------------------

def _execute_preset_in_maya(preset: Dict):
    """Apply a reference preset inside a live Maya session."""
    import maya.cmds as cmds

    # Pre scripts
    for scr in preset.get("scripts", []):
        if scr.get("phase") == "pre" and scr.get("enabled", True):
            _run_script(scr)

    # References
    for ref in preset.get("references", []):
        if not ref.get("enabled", True):
            continue
        path = ref.get("path", "")
        ns   = ref.get("namespace", "")
        if not path or not os.path.isfile(path):
            raise ValueError(f"ファイルが存在しません: {path}")
        cmds.file(path, reference=True, namespace=ns or "ref",
                  ignoreVersion=True, mergeNamespacesOnClash=False)

    # Constraints
    for con in preset.get("constraints", []):
        if not con.get("enabled", True):
            continue
        ctype  = con.get("type", "parentConstraint")
        src    = con.get("source_node", "")
        tgt    = con.get("target_node", "")
        offset = con.get("maintain_offset", True)
        if src and tgt:
            fn = getattr(cmds, ctype, None)
            if fn:
                fn(src, tgt, maintainOffset=offset)

    # Post scripts
    for scr in preset.get("scripts", []):
        if scr.get("phase") == "post" and scr.get("enabled", True):
            _run_script(scr)


def _run_script(scr: Dict):
    lang    = scr.get("lang", "python")
    content = scr.get("content", "")
    file_   = scr.get("file", "")

    if file_ and os.path.isfile(file_):
        with open(file_, "r", encoding="utf-8") as f:
            content = f.read()

    if not content.strip():
        return

    if lang == "python":
        exec(content, {"__builtins__": __builtins__})
    elif lang == "mel":
        import maya.mel as mel
        mel.eval(content)
