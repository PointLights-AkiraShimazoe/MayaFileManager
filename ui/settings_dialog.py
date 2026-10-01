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

from typing import Dict, List

from core.i18n import tr  # r118

from core.compat import (
    Qt, Signal,
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QToolButton, QLineEdit, QComboBox,
    QCheckBox, QSpinBox, QListWidget, QListWidgetItem,
    QTabWidget, QFrame, QScrollArea,
    QMenu, QMessageBox, QInputDialog
)


# ---------------------------------------------------------------------------
# Helper widgets
# ---------------------------------------------------------------------------

def _tv():
    """テーマトークン（色・形状・書体）。**遅延 import** すること。
    トップレベルで core.theme_engine から名前を取り込むと、Maya 内の
    ホットリロードや部分再読込で «partially initialized module» に当たり
    ImportError（cannot import name 'qss_vars'）になる（r82 で実害）。"""
    from core.theme_engine import qss_vars
    return qss_vars()


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
        self.setStyleSheet("color:%(primary)s;margin-top:8px;" % _tv())


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
        add_btn = QPushButton(tr("追加", "Add"))
        add_btn.clicked.connect(self._add_ext)
        add_row.addWidget(add_btn)
        del_btn = QPushButton(tr("削除", "Delete"))
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

        layout.addWidget(QLabel(tr("ディレクトリ:", "Directory:")), 0, 0)
        self._dir_edit = QLineEdit(directory)
        self._dir_edit.setPlaceholderText(
            tr("このフォルダ以下で保存する時に効きます（例 D:/Projects/CHR）",
               "Applies when saving in this folder or below "
               "(e.g. D:/Projects/CHR)"))
        self._dir_edit.setToolTip(tr(
            "ここに入れたフォルダ «以下» で保存する時に、下のテンプレートで\n"
            "ファイル名を提案します。\n"
            "複数のルールが当てはまる場合は «より深い» ルールが優先されます。",
            "When you save in this folder or below, the template suggests a "
            "file name.\nIf several rules match, the deepest one wins."))
        layout.addWidget(self._dir_edit, 0, 1)

        browse_btn = QToolButton()
        browse_btn.setText("…")
        browse_btn.clicked.connect(self._browse_dir)
        layout.addWidget(browse_btn, 0, 2)

        del_btn = QToolButton()
        del_btn.setText("✕")
        del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(del_btn, 0, 3)

        layout.addWidget(QLabel(tr("テンプレート:", "Template:")), 1, 0)
        self._tmpl_edit = QLineEdit(self._rule.get("template", "{seq:04d}"))
        self._tmpl_edit.setPlaceholderText("CHR_{seq:04d}")
        # r119d: «指定方法が分からない» という指摘。使えるトークンは
        # core.file_operations.AUTO_NAME_TOKENS を唯一の出所にして、
        # ツールチップと下の説明の両方へ出す（取り残しが起きない）。
        from core.file_operations import AUTO_NAME_TOKENS
        from core.i18n import current_lang
        _ja = current_lang() == "ja"
        tips = "\n".join("  %-11s %s" % (tok, ja if _ja else en)
                          for tok, ja, en in AUTO_NAME_TOKENS)
        self._tmpl_edit.setToolTip(
            tr("使えるトークン:\n%s", "Available tokens:\n%s") % tips)
        layout.addWidget(self._tmpl_edit, 1, 1)

        layout.addWidget(QLabel(tr("開始番号:", "Start number:")), 1, 2)
        self._start_spin = QSpinBox()
        self._start_spin.setRange(0, 99999)
        self._start_spin.setValue(self._rule.get("seq_start", 1))
        self._start_spin.setToolTip(tr(
            "連番の最初の番号。実際に保存するたびに 1 ずつ進み、\n"
            "進んだ値はそのフォルダの .mfm_seq に記録されます。",
            "The first sequence number. It advances by one on each actual "
            "save,\nand the current value is kept in .mfm_seq in that folder."))
        layout.addWidget(self._start_spin, 1, 3)

        # 使い方をその場に出す（ツールチップだけでは気付けない）
        hint = QLabel(tr(
            "使えるトークン: %s　／　例: CHR_{seq:04d} → CHR_0001",
            "Tokens: %s　/　e.g. CHR_{seq:04d} \u2192 CHR_0001")
            % "  ".join(tok for tok, _j, _e in AUTO_NAME_TOKENS))
        hint.setWordWrap(True)
        hint.setToolTip(self._tmpl_edit.toolTip())
        hint.setStyleSheet("color:%(on_surface_dim)s;font-size:%(label_px)spx;"
                           % _tv())
        layout.addWidget(hint, 2, 0, 1, 4)

    def _browse_dir(self):
        from core.compat import QFileDialog
        d = QFileDialog.getExistingDirectory(
            self, "ディレクトリを選択", self._dir_edit.text() or "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog)   # ネイティブは mayapy でクラッシュ（r64）
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
        self.setWindowTitle(tr("設定", "Settings"))
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

        self._tabs.addTab(self._tab_general(),     tr("一般", "General"))
        self._tabs.addTab(self._tab_browser(),     tr("ブラウザ", "Browser"))
        self._tabs.addTab(self._tab_history(),     tr("履歴 / ブックマーク", "History / Bookmarks"))
        self._tabs.addTab(self._tab_maya(),        "Maya")
        self._tabs.addTab(self._tab_auto_naming(), tr("自動命名", "Auto Naming"))

        # ── Bottom buttons ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        reset_btn = QPushButton(tr("デフォルトに戻す", "Restore Defaults"))
        reset_btn.clicked.connect(self._reset_defaults)
        btn_row.addWidget(reset_btn)

        cancel_btn = QPushButton(tr("キャンセル", "Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._apply_and_close)
        btn_row.addWidget(ok_btn)

        apply_btn = QPushButton(tr("適用", "Apply"))
        apply_btn.clicked.connect(self._apply)
        btn_row.addWidget(apply_btn)

        root.addLayout(btn_row)

    # ── Tab: 一般 ─────────────────────────────────────────────────────

    def _tab_general(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(8)

        layout.addRow(_SectionLabel(tr("言語 / Language", "Language")))
        self._lang_combo = QComboBox()
        self._lang_combo.addItems([
            tr("システム（OS / Maya に追従）", "System (follow OS / Maya)"),
            "日本語",
            "English",
        ])
        layout.addRow(tr("表示言語:", "UI Language:"), self._lang_combo)
        _lang_note = QLabel(tr("※ 再起動後に反映されます",
                               "* Takes effect after restart"))
        _lang_note.setStyleSheet(
            "color:%(on_surface_dim)s;font-size:%(label_px)spx;" % _tv())
        layout.addRow("", _lang_note)

        layout.addRow(_HLine())
        layout.addRow(_SectionLabel(tr("テーマ", "Theme")))
        self._theme_combo = QComboBox()
        self._theme_combo.addItems([tr("ダーク", "Dark"), tr("ライト", "Light")])
        layout.addRow(tr("テーマ:", "Theme:"), self._theme_combo)
        _theme_note = QLabel(tr("※ 再起動後に反映されます",
                                "* Takes effect after restart"))
        _theme_note.setStyleSheet(
            "color:%(on_surface_dim)s;font-size:%(label_px)spx;" % _tv())
        layout.addRow("", _theme_note)

        layout.addRow(_HLine())
        layout.addRow(_SectionLabel(tr("クリック動作", "Click Actions")))

        # 「プレビュー」は「開く」と同一動作のため統合済み
        self._single_click_combo = QComboBox()
        self._single_click_combo.addItems([
            tr("開く", "Open"), tr("インポート", "Import"),
            tr("リファレンス", "Reference"), tr("何もしない (None)", "None")])
        layout.addRow(tr("シングルクリック:", "Single click:"),
                      self._single_click_combo)

        self._double_click_combo = QComboBox()
        self._double_click_combo.addItems([
            tr("開く", "Open"), tr("インポート", "Import"),
            tr("リファレンス", "Reference")])
        layout.addRow(tr("ダブルクリック:", "Double click:"),
                      self._double_click_combo)

        layout.addRow(_HLine())
        layout.addRow(_SectionLabel("表示"))

        self._show_hidden_cb = QCheckBox(tr("隠しファイルを表示", "Show hidden files"))
        layout.addRow("", self._show_hidden_cb)

        return w

    # ── Tab: ブラウザ ─────────────────────────────────────────────────

    def _tab_browser(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(8)

        layout.addRow(_SectionLabel("カラムビュー"))

        # 「最大カラム深度」は廃止（フルパス保持。ルート自動シフトは
        # スクロール整合性を壊すため r54 で完全無効化）。ウィジェットは互換のため
        # 残すが非表示。
        self._col_depth_spin = QSpinBox()
        self._col_depth_spin.setRange(1, 12)
        self._col_depth_spin.setVisible(False)

        self._col_auto_width_cb = QCheckBox(tr("カラム幅を最大文字数に合わせる",
                                               "Fit column width to the longest name"))
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
        self._thumb_cache_spin.setSuffix(tr(" 件", " items"))
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

        layout.addRow(_SectionLabel(tr("履歴", "History")))

        self._history_max_spin = QSpinBox()
        self._history_max_spin.setRange(5, 1000)
        self._history_max_spin.setSuffix(tr(" 件", " items"))
        self._history_max_spin.setToolTip(tr(
            "履歴に残しておくフォルダの数。超えた分は古いものから消えます。",
            "How many folders to keep in the history; the oldest drop off."))
        layout.addRow(tr("保持件数:", "Keep:"), self._history_max_spin)

        self._history_per_maya_cb = QCheckBox(tr(
            "Maya の中で使う時、Maya のバージョン別に分ける（OFF = 全て共通）",
            "Inside Maya, keep a separate list per Maya version (off = shared)"))
        # r119: «ON だと何が起きるか» が分からない、という指摘。具体例で示す。
        self._history_per_maya_cb.setToolTip(tr(
            "«Maya の中から» このツールを開いた時だけ効きます。\n"
            "スタンドアロン（単体起動）では常に共通の履歴です。\n"
            "ヘッダーの «起動する Maya» を切り替えても履歴は変わりません。\n\n"
            "OFF（既定）: Maya 2026 の中でも 2025 の中でも同じ履歴。\n"
            "ON: Maya 2026 の中で開いたフォルダは 2025 の中では出ません。\n"
            "　　バージョンごとに扱う案件が完全に分かれている場合向け。",
            "Applies only when this tool runs INSIDE Maya.\n"
            "Standalone always uses the shared history, and changing the\n"
            "“Maya to launch” dropdown never switches it.\n\n"
            "Off (default): the same history inside 2026 and inside 2025.\n"
            "On: a folder opened inside 2026 does not appear inside 2025."))
        layout.addRow("", self._history_per_maya_cb)

        layout.addRow(_HLine())
        layout.addRow(_SectionLabel(tr("ブックマーク", "Bookmarks")))

        self._bm_per_maya_cb = QCheckBox(tr(
            "Maya の中で使う時、Maya のバージョン別に分ける（OFF = 全て共通）",
            "Inside Maya, keep a separate set per Maya version (off = shared)"))
        self._bm_per_maya_cb.setToolTip(tr(
            "«Maya の中から» このツールを開いた時だけ効きます。\n"
            "スタンドアロン（単体起動）では常に共通のブックマークです。\n"
            "ヘッダーの «起動する Maya» を切り替えても変わりません。\n\n"
            "ON: Maya 2026 の中で登録したものは 2025 の中では出ません。",
            "Applies only when this tool runs INSIDE Maya.\n"
            "Standalone always uses the shared bookmarks, and changing the\n"
            "“Maya to launch” dropdown never switches them.\n\n"
            "On: what you add inside 2026 does not show up inside 2025."))
        layout.addRow("", self._bm_per_maya_cb)

        return w

    # ── Tab: Maya ─────────────────────────────────────────────────────

    def _tab_maya(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(8)

        layout.addRow(_SectionLabel(tr("起動", "Launch")))

        self._maya_args_edit = QLineEdit()
        self._maya_args_edit.setPlaceholderText(tr("-batch など（スペース区切り）",
                                                   "e.g. -batch (space separated)"))
        layout.addRow("追加引数:", self._maya_args_edit)

        self._last_maya_ver_edit = QLineEdit()
        self._last_maya_ver_edit.setReadOnly(True)
        self._last_maya_ver_edit.setStyleSheet("color:%(on_surface_dim)s;" % _tv())
        layout.addRow("最後に使用したバージョン:", self._last_maya_ver_edit)

        return w

    # ── Tab: 自動命名 ─────────────────────────────────────────────────

    def _tab_auto_naming(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(6)

        # r119: 自動命名はプリセット毎にも設定できる（クイックナビ
        # プリセットエディタの「自動命名」タブ）。ここは «共通» 設定で、
        # プリセットが自前の設定を持たない時に使われる。
        scope = QLabel(tr(
            "ここは «共通» の設定です。プリセット毎に変えたい場合は\n"
            "ツール →「クイックナビ プリセットエディタ...」→「自動命名」タブで\n"
            "そのプリセット専用の設定を作れます。",
            "These are the shared settings, used when a preset has none of its "
            "own.\nTo vary them per preset, use Tools > \u201cQuick Nav Preset "
            "Editor...\u201d > \u201cAuto Naming\u201d."))
        scope.setWordWrap(True)
        scope.setStyleSheet("color:%(on_surface_dim)s;font-size:%(label_px)spx;"
                            % _tv())
        layout.addWidget(scope)

        self._auto_naming_enabled_cb = QCheckBox(tr("自動命名を有効にする", "Enable auto naming"))
        layout.addWidget(self._auto_naming_enabled_cb)

        info = QLabel(
            "指定ディレクトリ以下で新規ファイルを保存する際、\n"
            "テンプレートに従って自動的にファイル名を提案します。"
        )
        info.setStyleSheet(
            "color:%(on_surface_dim)s;font-size:%(label_px)spx;" % _tv())
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

        add_btn = QPushButton(tr("＋ ルールを追加", "+ Add rule"))
        # clicked(bool) の checked 引数が directory に流れ込むのを防ぐ
        add_btn.clicked.connect(lambda _c=False: self._add_naming_rule())
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

        # 言語モード
        lang_map = {"auto": 0, "ja": 1, "en": 2}
        self._lang_combo.setCurrentIndex(
            lang_map.get(sm.get("ui_language", "auto"), 0))

        # 「プレビュー」は「開く」へ移行済み
        single_map = {"open": 0, "preview": 0, "import": 1,
                      "reference": 2, "none": 3}
        self._single_click_combo.setCurrentIndex(
            single_map.get(sm.get("single_click_action", "open"), 0))
        double_map = {"open": 0, "preview": 0, "import": 1, "reference": 2}
        self._double_click_combo.setCurrentIndex(
            double_map.get(sm.get("double_click_action", "open"), 0))

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

        sm.set("ui_language",
               {0: "auto", 1: "ja", 2: "en"}.get(
                   self._lang_combo.currentIndex(), "auto"), save=False)

        single_map = {0: "open", 1: "import", 2: "reference", 3: "none"}
        sm.set("single_click_action",
               single_map.get(self._single_click_combo.currentIndex(), "open"),
               save=False)
        double_map = {0: "open", 1: "import", 2: "reference"}
        sm.set("double_click_action",
               double_map.get(self._double_click_combo.currentIndex(), "open"),
               save=False)
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
