"""
Settings Dialog
===============
タブ構成の設定ダイアログ。SettingsManager を直接読み書きする。

タブ
----
1. 一般           – テーマ、クリック動作、表示設定
2. ブラウザ       – カラム深度、サムネイルサイズ、ソート、フィルタ拡張子
3. 履歴・ブックマーク – 保持件数、共通/バージョン別切替
4. Maya           – 起動引数、デフォルトバージョン
5. 自動命名       – ディレクトリ別ルール一覧と編集
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from core.compat import (
    Qt, Signal,
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QToolButton, QLineEdit, QTextEdit, QComboBox,
    QCheckBox, QSpinBox, QGroupBox, QListWidget, QListWidgetItem,
    QTabWidget, QFrame, QSizePolicy, QScrollArea,
    QMenu, QAction, QMessageBox, QFileDialog, QInputDialog,
    QAbstractItemView, QSize
)


# ---------------------------------------------------------------------------
# Helper widgets
# ---------------------------------------------------------------------------

class _HLine(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.HLine)
        self.setFrameShadow(QFrame.Sunken)


class _SectionLabel(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        f = self.font()
        f.setBold(True)
        self.setFont(f)
        self.setStyleSheet("color: #8AB4D4; margin-top: 8px;")


class ExtensionListWidget(QWidget):
    """
    QListWidget でファイル拡張子のオン/オフを管理するミニウィジェット。
    """
    def __init__(self, extensions: List[str], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._list = QListWidget()
        self._list.setMaximumHeight(160)
        for ext in extensions:
            item = QListWidgetItem(ext)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self._list.addItem(item)
        layout.addWidget(self._list)

        add_row = QHBoxLayout()
        self._add_edit = QLineEdit()
        self._add_edit.setPlaceholderText(".usd")
        self._add_edit.setFixedWidth(80)
        add_row.addWidget(self._add_edit)
        add_btn = QPushButton("追加")
        add_btn.clicked.connect(self._add_ext)
        add_row.addWidget(add_btn)
        del_btn = QPushButton("削除")
        del_btn.clicked.connect(self._del_selected)
        add_row.addWidget(del_btn)
        add_row.addStretch()
        layout.addLayout(add_row)

    def _add_ext(self):
        ext = self._add_edit.text().strip()
        if ext and not ext.startswith("."):
            ext = "." + ext
        if ext:
            item = QListWidgetItem(ext)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self._list.addItem(item)
            self._add_edit.clear()

    def _del_selected(self):
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))

    def get_extensions(self) -> List[str]:
        return [
            self._list.item(i).text()
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.Checked
        ]

    def set_extensions(self, extensions: List[str]):
        self._list.clear()
        for ext in extensions:
            item = QListWidgetItem(ext)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self._list.addItem(item)


# ---------------------------------------------------------------------------
# Auto-naming rule editor (embedded in settings tab)
# ---------------------------------------------------------------------------

class AutoNamingRuleRow(QFrame):
    """One row = one directory rule."""

    remove_requested = Signal(object)

    def __init__(self, directory: str = "", rule: Dict = None, parent=None):
        super().__init__(parent)
        self._rule = rule or {
            "template": "{seq:04d}",
            "seq_start": 1,
            "counter_file": ".mfm_seq",
        }
        self.setFrameShape(QFrame.StyledPanel)
        self._build(directory)

    def _build(self, directory: str):
        layout = QGridLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        layout.addWidget(QLabel("ディレクトリ:"), 0, 0)
        self._dir_edit = QLineEdit(directory)
        self._dir_edit.setPlaceholderText("/projects/CHR")
        layout.addWidget(self._dir_edit, 0, 1)

        browse_btn = QToolButton()
        browse_btn.setText("…")
        browse_btn.clicked.connect(self._browse_dir)
        layout.addWidget(browse_btn, 0, 2)

        del_btn = QToolButton()
        del_btn.setText("✕")
        del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(del_btn, 0, 3)

        layout.addWidget(QLabel("テンプレート:"), 1, 0)
        self._tmpl_edit = QLineEdit(self._rule.get("template", "{seq:04d}"))
        self._tmpl_edit.setToolTip(
            "利用可能トークン:\n"
            "  {seq}      シーケンス番号\n"
            "  {seq:04d}  ゼロパディング4桁\n"
            "  {desc}     説明（入力ダイアログ）"
        )
        layout.addWidget(self._tmpl_edit, 1, 1)

        layout.addWidget(QLabel("開始番号:"), 1, 2)
        self._start_spin = QSpinBox()
        self._start_spin.setRange(0, 99999)
        self._start_spin.setValue(self._rule.get("seq_start", 1))
        layout.addWidget(self._start_spin, 1, 3)

    def _browse_dir(self):
        from core.compat import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "ディレクトリを選択")
        if d:
            self._dir_edit.setText(d)

    def get_data(self):
        return self._dir_edit.text(), {
            "template": self._tmpl_edit.text(),
            "seq_start": self._start_spin.value(),
            "counter_file": self._rule.get("counter_file", ".mfm_seq"),
        }


# ---------------------------------------------------------------------------
# Settings Dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):

    settings_changed = Signal()

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        self.setWindowTitle("設定")
        self.setMinimumSize(640, 520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._auto_naming_rows: List[AutoNamingRuleRow] = []
        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs)

        self._tabs.addTab(self._tab_general(),     "一般")
        self._tabs.addTab(self._tab_browser(),     "ブラウザ")
        self._tabs.addTab(self._tab_history(),     "履歴 / ブックマーク")
        self._tabs.addTab(self._tab_maya(),        "Maya")
        self._tabs.addTab(self._tab_auto_naming(), "自動命名")

        # ── Bottom buttons ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        reset_btn = QPushButton("デフォルトに戻す")
        reset_btn.clicked.connect(self._reset_defaults)
        btn_row.addWidget(reset_btn)

        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._apply_and_close)
        btn_row.addWidget(ok_btn)

        apply_btn = QPushButton("適用")
        apply_btn.clicked.connect(self._apply)
        btn_row.addWidget(apply_btn)

        root.addLayout(btn_row)

    # ── Tab: 一般 ─────────────────────────────────────────────────────

    def _tab_general(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(8)

        layout.addRow(_SectionLabel("テーマ"))
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["ダーク", "ライト"])
        layout.addRow("テーマ:", self._theme_combo)

        layout.addRow(_HLine())
        layout.addRow(_SectionLabel("クリック動作"))

        self._single_click_combo = QComboBox()
        self._single_click_combo.addItems(["プレビュー", "開く", "インポート", "リファレンス"])
        layout.addRow("シングルクリック:", self._single_click_combo)

        self._double_click_combo = QComboBox()
        self._double_click_combo.addItems(["開く", "インポート", "リファレンス", "プレビュー"])
        layout.addRow("ダブルクリック:", self._double_click_combo)

        layout.addRow(_HLine())
        layout.addRow(_SectionLabel("表示"))

        self._show_hidden_cb = QCheckBox("隠しファイルを表示")
        layout.addRow("", self._show_hidden_cb)

        return w

    # ── Tab: ブラウザ ─────────────────────────────────────────────────

    def _tab_browser(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(8)

        layout.addRow(_SectionLabel("カラムビュー"))

        self._col_depth_spin = QSpinBox()
        self._col_depth_spin.setRange(1, 12)
        layout.addRow("最大カラム深度:", self._col_depth_spin)

        self._col_auto_width_cb = QCheckBox("カラム幅を最大文字数に合わせる")
        layout.addRow("", self._col_auto_width_cb)

        layout.addRow(_HLine())
        layout.addRow(_SectionLabel("サムネイル"))

        self._thumb_size_spin = QSpinBox()
        self._thumb_size_spin.setRange(32, 512)
        self._thumb_size_spin.setSingleStep(32)
        self._thumb_size_spin.setSuffix(" px")
        layout.addRow("サムネイルサイズ:", self._thumb_size_spin)

        self._thumb_cache_spin = QSpinBox()
        self._thumb_cache_spin.setRange(16, 2048)
        self._thumb_cache_spin.setSingleStep(32)
        self._thumb_cache_spin.setSuffix(" 件")
        layout.addRow("キャッシュ件数:", self._thumb_cache_spin)

        layout.addRow(_HLine())
        layout.addRow(_SectionLabel("表示拡張子"))

        default_exts = [".ma", ".mb", ".fbx", ".obj", ".abc",
                        ".usd", ".usda", ".usdc", ".py", ".mel"]
        self._ext_list = ExtensionListWidget(default_exts)
        layout.addRow("", self._ext_list)

        return w

    # ── Tab: 履歴 / ブックマーク ──────────────────────────────────────

    def _tab_history(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(8)

        layout.addRow(_SectionLabel("履歴"))

        self._history_max_spin = QSpinBox()
        self._history_max_spin.setRange(5, 1000)
        self._history_max_spin.setSuffix(" 件")
        layout.addRow("保持件数:", self._history_max_spin)

        self._history_per_maya_cb = QCheckBox(
            "Maya バージョン別に独立して管理する\n"
            "（OFF = 全バージョン共通）"
        )
        layout.addRow("", self._history_per_maya_cb)

        layout.addRow(_HLine())
        layout.addRow(_SectionLabel("ブックマーク"))

        self._bm_per_maya_cb = QCheckBox(
            "Maya バージョン別に独立して管理する\n"
            "（OFF = 全バージョン共通）"
        )
        layout.addRow("", self._bm_per_maya_cb)

        return w

    # ── Tab: Maya ─────────────────────────────────────────────────────

    def _tab_maya(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(8)

        layout.addRow(_SectionLabel("起動"))

        self._maya_args_edit = QLineEdit()
        self._maya_args_edit.setPlaceholderText("-batch など（スペース区切り）")
        layout.addRow("追加引数:", self._maya_args_edit)

        self._last_maya_ver_edit = QLineEdit()
        self._last_maya_ver_edit.setReadOnly(True)
        self._last_maya_ver_edit.setStyleSheet("color: #888;")
        layout.addRow("最後に使用したバージョン:", self._last_maya_ver_edit)

        return w

    # ── Tab: 自動命名 ─────────────────────────────────────────────────

    def _tab_auto_naming(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(6)

        self._auto_naming_enabled_cb = QCheckBox("自動命名を有効にする")
        layout.addWidget(self._auto_naming_enabled_cb)

        info = QLabel(
            "指定ディレクトリ以下で新規ファイルを保存する際、\n"
            "テンプレートに従って自動的にファイル名を提案します。"
        )
        info.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(info)

        layout.addWidget(_HLine())

        # Rule container (scrollable)
        self._rule_container = QWidget()
        self._rule_layout = QVBoxLayout(self._rule_container)
        self._rule_layout.setContentsMargins(0, 0, 0, 0)
        self._rule_layout.setSpacing(4)
        self._rule_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(self._rule_container)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        add_btn = QPushButton("＋ ルールを追加")
        add_btn.clicked.connect(self._add_naming_rule)
        layout.addWidget(add_btn)

        return w

    # ------------------------------------------------------------------
    # Load / Apply values
    # ------------------------------------------------------------------

    def _load_values(self):
        sm = self._sm

        # 一般
        theme_map = {"dark": 0, "light": 1}
        self._theme_combo.setCurrentIndex(theme_map.get(sm.get("theme", "dark"), 0))

        action_map = {"preview": 0, "open": 1, "import": 2, "reference": 3}
        self._single_click_combo.setCurrentIndex(
            action_map.get(sm.get("single_click_action", "preview"), 0))
        self._double_click_combo.setCurrentIndex(
            action_map.get(sm.get("double_click_action", "open"), 0))

        self._show_hidden_cb.setChecked(sm.get("show_hidden_files", False))

        # ブラウザ
        self._col_depth_spin.setValue(sm.get("column_max_depth", 4))
        self._col_auto_width_cb.setChecked(sm.get("column_auto_width", True))
        self._thumb_size_spin.setValue(sm.get("thumbnail_size", 128))
        self._thumb_cache_spin.setValue(sm.get("thumbnail_cache_size", 256))

        exts = sm.get("file_extensions_visible", [])
        if exts:
            self._ext_list.set_extensions(exts)

        # 履歴・ブックマーク
        self._history_max_spin.setValue(sm.get("history_max_count", 50))
        self._history_per_maya_cb.setChecked(sm.get("history_per_maya", False))
        self._bm_per_maya_cb.setChecked(sm.get("bookmarks_per_maya", False))

        # Maya
        args = sm.get("maya_launch_args", [])
        self._maya_args_edit.setText(" ".join(args))
        self._last_maya_ver_edit.setText(sm.get("last_maya_version", ""))

        # 自動命名
        self._auto_naming_enabled_cb.setChecked(sm.get("auto_naming_enabled", True))
        rules = sm.get_auto_naming_rules()
        for directory, rule in rules.items():
            self._add_naming_rule(directory=directory, rule=rule)

    def _apply(self):
        sm = self._sm

        # 一般
        theme_map = {0: "dark", 1: "light"}
        sm.set("theme", theme_map[self._theme_combo.currentIndex()], save=False)

        action_map = {0: "preview", 1: "open", 2: "import", 3: "reference"}
        sm.set("single_click_action",
               action_map[self._single_click_combo.currentIndex()], save=False)
        sm.set("double_click_action",
               action_map.get(self._double_click_combo.currentIndex(), "open"), save=False)
        sm.set("show_hidden_files", self._show_hidden_cb.isChecked(), save=False)

        # ブラウザ
        sm.set("column_max_depth", self._col_depth_spin.value(), save=False)
        sm.set("column_auto_width", self._col_auto_width_cb.isChecked(), save=False)
        sm.set("thumbnail_size", self._thumb_size_spin.value(), save=False)
        sm.set("thumbnail_cache_size", self._thumb_cache_spin.value(), save=False)
        sm.set("file_extensions_visible", self._ext_list.get_extensions(), save=False)

        # 履歴・ブックマーク
        sm.set("history_max_count", self._history_max_spin.value(), save=False)
        sm.set("history_per_maya", self._history_per_maya_cb.isChecked(), save=False)
        sm.set("bookmarks_per_maya", self._bm_per_maya_cb.isChecked(), save=False)

        # Maya
        args_text = self._maya_args_edit.text().strip()
        sm.set("maya_launch_args", args_text.split() if args_text else [], save=False)

        # 自動命名
        sm.set("auto_naming_enabled", self._auto_naming_enabled_cb.isChecked(), save=False)
        rules: Dict = {}
        for row in self._auto_naming_rows:
            directory, rule_data = row.get_data()
            if directory.strip():
                rules[directory.strip()] = rule_data
        sm.save_auto_naming_rules(rules)

        sm.save()
        self.settings_changed.emit()

    def _apply_and_close(self):
        self._apply()
        self.accept()

    def _reset_defaults(self):
        ret = QMessageBox.question(
            self, "リセット確認",
            "すべての設定をデフォルト値に戻しますか？",
            QMessageBox.Yes | QMessageBox.No
        )
        if ret == QMessageBox.Yes:
            from core.settings_manager import DEFAULT_SETTINGS
            for key, val in DEFAULT_SETTINGS.items():
                self._sm.set(key, val, save=False)
            self._sm.save()
            self._load_values()

    # ------------------------------------------------------------------
    # Auto-naming helpers
    # ------------------------------------------------------------------

    def _add_naming_rule(self, directory: str = "", rule: Dict = None):
        row = AutoNamingRuleRow(directory=directory, rule=rule)
        row.remove_requested.connect(self._remove_naming_rule)
        self._auto_naming_rows.append(row)
        # Insert before stretch
        self._rule_layout.insertWidget(self._rule_layout.count() - 1, row)

    def _remove_naming_rule(self, row: AutoNamingRuleRow):
        if row in self._auto_naming_rows:
            self._auto_naming_rows.remove(row)
        self._rule_layout.removeWidget(row)
        row.deleteLater()
