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
from core.diag import swallow as _swallow  # r112

import os
from pathlib import Path
from typing import Dict, List, Optional

from core.i18n import tr  # r118

from core.compat import (
    Qt, Signal,
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolButton, QLineEdit, QListWidget, QAbstractItemView,

    QMenu, QMessageBox, QFileDialog, QInputDialog
)
from core.compat import QtCore as _QtCore


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
        up_btn.setToolTip(tr("上へ", "Move up"))
        up_btn.clicked.connect(lambda: self.move_up_requested.emit(self))
        layout.addWidget(up_btn)

        down_btn = QToolButton()
        down_btn.setText("▼")
        down_btn.setFixedSize(24, 24)
        down_btn.setToolTip(tr("下へ", "Move down"))
        down_btn.clicked.connect(lambda: self.move_down_requested.emit(self))
        layout.addWidget(down_btn)

        # Label
        layout.addWidget(QLabel(tr("ラベル:", "Label:")))
        self._label_edit = QLineEdit(self._item.get("label", ""))
        self._label_edit.setFixedWidth(100)
        self._label_edit.setPlaceholderText(tr("ボタン名", "Button name"))
        self._label_edit.textChanged.connect(self._sync)
        layout.addWidget(self._label_edit)

        # Path
        layout.addWidget(QLabel(tr("パス:", "Path:")))
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
        # ネイティブダイアログはシェル拡張の読み込みで mayapy がクラッシュする
        # （r64）。アプリ属性 AA_DontUseNativeDialogs に加え、呼び出し側でも明示。
        d = QFileDialog.getExistingDirectory(
            self, "ディレクトリを選択", self._path_edit.text() or str(Path.home()),
            QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog)
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
        # r119: 自動命名はプリセット毎。保存するまでは «下書き» に貯める
        # （プリセットを行き来しても編集が消えないようにするため）。
        self._naming_rows = []
        self._naming_draft: Dict[str, Dict] = {}
        self._renamed_presets: Dict[str, str] = {}
        self._deleted_presets = set()

        self.setWindowTitle(tr("クイックナビ プリセットエディタ", "Quick Nav Preset Editor"))
        self.setMinimumSize(820, 560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._build_ui()
        self._disable_auto_default()
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

        ll.addWidget(QLabel(tr("プリセット", "Presets")))
        self._preset_list = QListWidget()
        self._preset_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._preset_list.currentRowChanged.connect(self._on_preset_selected)
        ll.addWidget(self._preset_list)

        btn_row = QHBoxLayout()
        new_btn = QPushButton(tr("✚ 新規", "✚ New"))
        new_btn.clicked.connect(self._new_preset)
        btn_row.addWidget(new_btn)
        dup_btn = QPushButton(tr("⧉ 複製", "⧉ Duplicate"))
        dup_btn.clicked.connect(self._duplicate_preset)
        btn_row.addWidget(dup_btn)
        ll.addLayout(btn_row)

        del_btn = QPushButton(tr("🗑 削除", "🗑 Delete"))
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
        name_row.addWidget(QLabel(tr("プリセット名:", "Preset name:")))
        self._name_edit = QLineEdit()
        # r63: 名前欄の Enter / フォーカス喪失で «その場で» 改名を反映する。
        # 従来は Enter がダイアログの既定ボタン（保存して閉じる）に流れて閉じて
        # しまい、改名が保存されない／空の新規プリセットだけ残る、が起きた。
        self._name_edit.returnPressed.connect(self._apply_rename)
        self._name_edit.editingFinished.connect(self._apply_rename)
        # r106: 名前欄の Enter は «改名するだけ»。ダイアログの既定ボタンへ
        # 流さない（新規プリセット作成や «保存して閉じる» を誘発しない）。
        self._name_edit.installEventFilter(self)
        name_row.addWidget(self._name_edit)
        rl.addLayout(name_row)

        # r119: プリセット毎に «自動命名» も変えたい、というユーザー指示。
        # ナビゲーションボタンと並べてタブで切り替える。
        from core.compat import QTabWidget
        self._tabs = QTabWidget()
        rl.addWidget(self._tabs, 1)

        nav_tab = QWidget()
        nl = QVBoxLayout(nav_tab)
        nl.setContentsMargins(6, 6, 6, 6)
        nl.setSpacing(6)
        self._tabs.addTab(nav_tab, tr("ナビゲーション", "Navigation"))

        nl.addWidget(QLabel(tr("ナビゲーションボタン （上から左ツールバーの順）:",
                               "Navigation buttons (top to bottom = left toolbar order):")))

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
        nl.addWidget(scroll, 1)

        add_item_btn = QPushButton(tr("＋ ボタンを追加", "+ Add button"))
        # clicked(bool) の checked 引数が label に流れ込むのを防ぐ（lambdaで遮断）
        add_item_btn.clicked.connect(lambda _c=False: self._add_item())
        nl.addWidget(add_item_btn)

        # Quick-add standard directories
        quick_row = QHBoxLayout()
        quick_row.addWidget(QLabel(tr("クイック追加:", "Quick add:")))
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
        nl.addLayout(quick_row)

        self._tabs.addTab(self._build_naming_tab(),
                          tr("自動命名", "Auto Naming"))

        # Buttons
        btn_row2 = QHBoxLayout()
        btn_row2.addStretch()
        cancel_btn = QPushButton(tr("キャンセル", "Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row2.addWidget(cancel_btn)

        save_btn = QPushButton(tr("💾 保存して閉じる", "💾 Save and close"))
        # 既定ボタンにしない: 名前欄・ラベル欄・パス欄で Enter を押すたびに
        # ダイアログが閉じてしまうため（r63）
        save_btn.setDefault(False)
        save_btn.setAutoDefault(False)
        cancel_btn.setAutoDefault(False)
        save_btn.clicked.connect(self._save_and_close)
        btn_row2.addWidget(save_btn)
        rl.addLayout(btn_row2)

        root.addWidget(right)

    def keyPressEvent(self, event):
        """ダイアログ全体で Enter を握り潰す（r106）。

        プリセット設定の **どこで Enter を押しても** «新しいプリセット» が
        立ち上がっていた（autoDefault ボタンが拾っていた）。ここで止めて、
        各入力欄の returnPressed だけで完結させる。Esc は従来どおり閉じる。"""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        """名前欄の Enter をここで消費する（r106）。"""
        try:
            if obj is self._name_edit and \
                    event.type() == _QtCore.QEvent.KeyPress:
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self._apply_rename()
                    return True
        except Exception as _e:
            _swallow(_e, "ui/quick_nav_editor.py:281 eventFilter")
        return super().eventFilter(obj, event)

    def _disable_auto_default(self):
        """ダイアログ内の全ボタンの autoDefault を切る（r106）。

        QDialog 内の QPushButton は既定で autoDefault=True。Enter を押すと
        «フォーカス連鎖で最初の autoDefault ボタン» が押されるため、
        プリセット名を直して Enter → 「✚ 新規」が発火して
        «新しいプリセット» ダイアログが開いていた（ユーザー報告 2026-10-01）。
        r63 で保存／キャンセルだけ切っていたが、残りのボタンが拾っていた。
        名前欄の Enter は returnPressed（改名）だけで完結させる。"""
        for b in self.findChildren(QPushButton):
            try:
                b.setAutoDefault(False)
                b.setDefault(False)
            except Exception as _e:
                _swallow(_e, "ui/quick_nav_editor.py:298 _disable_auto_default")

    # ------------------------------------------------------------------
    # Preset management
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 自動命名（プリセット毎。r119）
    #
    # 共通設定（設定ダイアログの「自動命名」）はフォールバックとして残す。
    # プリセットが «自前の設定を持つ» かどうかはチェックボックスで決める。
    # ------------------------------------------------------------------

    def _build_naming_tab(self):
        from core.compat import QCheckBox, QScrollArea
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        self._naming_own_cb = QCheckBox(
            tr("このプリセット専用の自動命名を使う",
               "Use auto-naming settings specific to this preset"))
        self._naming_own_cb.setToolTip(tr(
            "OFF のときは設定ダイアログの «共通» 設定に従います。",
            "When off, the shared settings from the Settings dialog apply."))
        self._naming_own_cb.toggled.connect(self._on_naming_own_toggled)
        lay.addWidget(self._naming_own_cb)

        self._naming_enabled_cb = QCheckBox(
            tr("自動命名を有効にする", "Enable auto naming"))
        lay.addWidget(self._naming_enabled_cb)

        self._naming_container = QWidget()
        self._naming_layout = QVBoxLayout(self._naming_container)
        self._naming_layout.setContentsMargins(0, 0, 0, 0)
        self._naming_layout.setSpacing(4)
        self._naming_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(self._naming_container)
        scroll.setWidgetResizable(True)
        lay.addWidget(scroll, 1)

        self._naming_add_btn = QPushButton(tr("＋ ルールを追加", "+ Add rule"))
        self._naming_add_btn.setAutoDefault(False)
        self._naming_add_btn.clicked.connect(lambda _c=False: self._add_naming_rule())
        lay.addWidget(self._naming_add_btn)

        self._naming_rows = []
        return w

    def _on_naming_own_toggled(self, on):
        for wdg in (self._naming_enabled_cb, self._naming_container,
                    self._naming_add_btn):
            wdg.setEnabled(bool(on))

    def _clear_naming_rows(self):
        for row in list(getattr(self, "_naming_rows", [])):
            row.setParent(None)
            row.deleteLater()
        self._naming_rows = []

    def _add_naming_rule(self, directory="", rule=None):
        from ui.settings_dialog import AutoNamingRuleRow
        row = AutoNamingRuleRow(directory=directory, rule=rule)
        row.remove_requested.connect(self._remove_naming_rule)
        self._naming_rows.append(row)
        self._naming_layout.insertWidget(self._naming_layout.count() - 1, row)
        return row

    def _remove_naming_rule(self, row):
        if row in self._naming_rows:
            self._naming_rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def _load_naming_for(self, preset: str):
        """プリセットの自動命名設定を画面へ。未保存の編集は _naming_draft に持つ。"""
        self._clear_naming_rows()
        draft = self._naming_draft.get(preset)
        if draft is None:
            own = self._sm.has_auto_naming_for_preset(preset)
            draft = {
                "own": own,
                "enabled": self._sm.get_auto_naming_enabled(preset if own else None),
                "rules": dict(self._sm.get_auto_naming_rules(preset) if own else {}),
            }
        self._naming_own_cb.blockSignals(True)
        self._naming_own_cb.setChecked(bool(draft["own"]))
        self._naming_own_cb.blockSignals(False)
        self._naming_enabled_cb.setChecked(bool(draft["enabled"]))
        for d, rule in (draft["rules"] or {}).items():
            self._add_naming_rule(d, rule)
        self._on_naming_own_toggled(bool(draft["own"]))

    def _collect_naming(self):
        rules = {}
        for row in getattr(self, "_naming_rows", []):
            d, rule = row.get_data()
            if d:
                rules[d] = rule
        return {"own": self._naming_own_cb.isChecked(),
                "enabled": self._naming_enabled_cb.isChecked(),
                "rules": rules}

    def _stash_naming(self):
        """表示中のプリセットの編集内容を下書きへ退避する。"""
        if self._current_preset and hasattr(self, "_naming_own_cb"):
            self._naming_draft[self._current_preset] = self._collect_naming()

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
        self._stash_naming()           # r119: 切り替える前に編集内容を退避
        if row < 0:
            self._clear_items()
            self._clear_naming_rows()
            return
        name = self._preset_list.item(row).text()
        self._current_preset = name
        self._name_edit.blockSignals(True)
        self._name_edit.setText(name)
        self._name_edit.blockSignals(False)
        self._populate_items(self._presets.get(name, []))
        self._load_naming_for(name)

    def _apply_rename(self):
        """名前欄の内容を現在のプリセット名に反映する（辞書とリスト項目の両方）。

        r124: 再入禁止。QLineEdit は Enter で returnPressed と editingFinished を
        «両方» 出し、さらに重複名の警告（モーダル）を出すとフォーカスが外れて
        editingFinished が «警告を出している最中に» 飛ぶ。素のままだと警告が
        二重に出る／同じ改名が二度走る。"""
        if getattr(self, "_renaming", False):
            return
        self._renaming = True
        try:
            self._apply_rename_now()
        finally:
            self._renaming = False

    def _apply_rename_now(self):
        new_name = self._name_edit.text().strip()
        cur = self._current_preset
        if not cur or not new_name or new_name == cur:
            return
        if new_name in self._presets:
            QMessageBox.warning(self, tr("重複", "Duplicate"),
                                tr("同名のプリセットが既に存在します。",
                                   "A preset with that name already exists."))
            self._name_edit.blockSignals(True)
            self._name_edit.setText(cur)
            self._name_edit.blockSignals(False)
            return
        self._presets[new_name] = self._presets.pop(cur)
        if cur in self._naming_draft:      # r119: 自動命名の下書きも改名に追従
            self._naming_draft[new_name] = self._naming_draft.pop(cur)
        self._renamed_presets[cur] = new_name
        self._current_preset = new_name
        for it in self._preset_list.findItems(cur, Qt.MatchExactly):
            it.setText(new_name)

    def _new_preset(self):
        name, ok = QInputDialog.getText(self, tr("新しいプリセット", "New Preset"), tr("プリセット名:", "Preset name:"))
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._presets:
            QMessageBox.warning(self, tr("重複", "Duplicate"),
                                tr("同名のプリセットが既に存在します。",
                                   "A preset with that name already exists."))
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
        self._stash_naming()
        if self._current_preset in self._naming_draft:
            self._naming_draft[new_name] = copy.deepcopy(
                self._naming_draft[self._current_preset])
        self._preset_list.addItem(new_name)
        items = self._preset_list.findItems(new_name, Qt.MatchExactly)
        if items:
            self._preset_list.setCurrentItem(items[0])

    def _delete_preset(self):
        if not self._current_preset:
            return
        ret = QMessageBox.question(self, tr("削除確認", "Confirm Delete"),
                                   tr("「%s」を削除しますか？", "Delete \u201c%s\u201d?")
                                   % self._current_preset,
                                   QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self._naming_draft.pop(self._current_preset, None)
        self._deleted_presets.add(self._current_preset)
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
        # 未反映の改名があれば反映（重複なら警告して中断）
        new_name = self._name_edit.text().strip()
        if self._current_preset and new_name and new_name != self._current_preset:
            if new_name in self._presets:
                QMessageBox.warning(self, tr("重複", "Duplicate"),
                                tr("同名のプリセットが既に存在します。",
                                   "A preset with that name already exists."))
                return
            self._apply_rename()

        self._sync_current_preset()
        self._sm.save_quick_nav_presets(self._presets)
        # r119: プリセット毎の自動命名も一緒に保存する
        self._stash_naming()
        for old_name, new_nm in self._renamed_presets.items():
            self._sm.rename_auto_naming_preset(old_name, new_nm)
        for gone in self._deleted_presets:
            if gone not in self._presets:
                self._sm.clear_auto_naming_for_preset(gone)
        for name, draft in self._naming_draft.items():
            if name not in self._presets:
                continue
            if draft.get("own"):
                self._sm.save_auto_naming_for_preset(
                    name, draft.get("rules") or {},
                    enabled=bool(draft.get("enabled", True)))
            else:
                self._sm.clear_auto_naming_for_preset(name)
        self.presets_saved.emit()
        self.accept()
