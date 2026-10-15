"""
Batch Rename Dialog
===================
ファイル / フォルダを複数選択してまとめてリネームするダイアログ。

機能
----
- 変換ルール: 置換 / プレフィックス / サフィックス / 連番 / 正規表現
- ライブプレビュー（変換前 → 変換後を表で即時確認）
- 競合（同名ファイルが既存）検出・警告
- Dry-run 確認 → 実行
"""

from pathlib import Path
from typing import List, Tuple, Optional

from core.i18n import tr  # r118

from core.compat import (
    Qt, Signal,
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit, QComboBox,
    QSpinBox, QGroupBox, QToolButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox, QColor
)
from core.file_operations import RenameRule, batch_rename, rename_path
import os


# ---------------------------------------------------------------------------
# Preview Table
# ---------------------------------------------------------------------------

def _tv():
    """テーマトークン（色・形状・書体）。**遅延 import** すること。
    トップレベルで core.theme_engine から名前を取り込むと、Maya 内の
    ホットリロードや部分再読込で «partially initialized module» に当たり
    ImportError（cannot import name 'qss_vars'）になる（r82 で実害）。"""
    from core.theme_engine import qss_vars
    return qss_vars()


class PreviewTable(QTableWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels([tr("変換前", "Before"), tr("変換後", "After"),
                                        tr("状態", "Status")])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(2, 80)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().hide()
        self.setAlternatingRowColors(True)

    def update_preview(self, results: List[Tuple[str, str, Optional[str]]]):
        self.setRowCount(len(results))
        for row, (old, new, err) in enumerate(results):
            old_name = Path(old).name
            new_name = Path(new).name

            old_item = QTableWidgetItem(old_name)
            new_item = QTableWidgetItem(new_name)

            if err:
                status = QTableWidgetItem(tr("⚠ エラー", "⚠ Error"))
                status.setForeground(QColor(_tv()["error"]))
                new_item.setForeground(QColor(_tv()["error"]))
            elif old_name == new_name:
                status = QTableWidgetItem(tr("変化なし", "No change"))
                status.setForeground(QColor(_tv()["on_surface_dim"]))
            else:
                # Check if new file already exists
                new_path = Path(new)
                if new_path.exists() and new_path != Path(old):
                    status = QTableWidgetItem(tr("⚠ 競合", "⚠ Conflict"))
                    status.setForeground(QColor(_tv()["warning"]))
                    new_item.setForeground(QColor(_tv()["warning"]))
                else:
                    status = QTableWidgetItem("✓ OK")
                    status.setForeground(QColor(_tv()["success"]))
                    new_item.setForeground(QColor(_tv()["success"]))

            self.setItem(row, 0, old_item)
            self.setItem(row, 1, new_item)
            self.setItem(row, 2, status)


# ---------------------------------------------------------------------------
# Batch Rename Dialog
# ---------------------------------------------------------------------------

class _RuleRow(QWidget):
    """変換ルール 1 件（r146、ユーザー指示 2026-10-15）。

      「変換ルールを複数持てるようにしてください。
        ＋ボタンで増やし、上から順に適用されたとして結果表示を出して
        ください。」

    1 件は «モード＋そのモードの引数» なので丸ごと 1 つのウィジェットに
    まとめ、ダイアログ側は «これを縦に並べるだけ» にする。
    """

    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        head = QGridLayout(self)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)

        head.addWidget(QLabel(tr("モード:", "Mode:")), 0, 0)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems([
            tr("文字列置換", "Find & Replace"),
            tr("プレフィックス追加", "Add Prefix"),
            tr("サフィックス追加", "Add Suffix"),
            tr("連番", "Numbering"),
            tr("正規表現", "Regular Expression"),
        ])
        self._mode_combo.setFixedWidth(160)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        head.addWidget(self._mode_combo, 0, 1)
        head.setColumnStretch(2, 1)

        self._del_btn = QToolButton()
        self._del_btn.setText("\u2715")
        self._del_btn.setToolTip(tr("このルールを削除", "Remove this rule"))
        self._del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        head.addWidget(self._del_btn, 0, 3)

        self._params_widget = QWidget()
        self._params_layout = QGridLayout(self._params_widget)
        self._params_layout.setContentsMargins(0, 0, 0, 0)
        self._params_layout.setSpacing(6)
        head.addWidget(self._params_widget, 1, 0, 1, 4)

        self._replace_find_edit = QLineEdit()
        self._replace_find_edit.setPlaceholderText(tr("検索文字列", "Text to find"))
        self._replace_find_edit.textChanged.connect(lambda _t: self.changed.emit())
        self._replace_repl_edit = QLineEdit()
        self._replace_repl_edit.setPlaceholderText(tr("置換文字列", "Replacement text"))
        self._replace_repl_edit.textChanged.connect(lambda _t: self.changed.emit())
        self._prefix_edit = QLineEdit()
        self._prefix_edit.setPlaceholderText(tr("追加するプレフィックス", "Prefix to add"))
        self._prefix_edit.textChanged.connect(lambda _t: self.changed.emit())
        self._suffix_edit = QLineEdit()
        self._suffix_edit.setPlaceholderText(tr("追加するサフィックス", "Suffix to add"))
        self._suffix_edit.textChanged.connect(lambda _t: self.changed.emit())
        self._seq_token_edit = QLineEdit("{n}")
        self._seq_token_edit.setFixedWidth(60)
        self._seq_token_edit.textChanged.connect(lambda _t: self.changed.emit())
        self._seq_start_spin = QSpinBox()
        self._seq_start_spin.setRange(0, 99999)
        self._seq_start_spin.setValue(1)
        self._seq_start_spin.valueChanged.connect(lambda _v: self.changed.emit())
        self._seq_pad_spin = QSpinBox()
        self._seq_pad_spin.setRange(1, 8)
        self._seq_pad_spin.setValue(3)
        self._seq_pad_spin.valueChanged.connect(lambda _v: self.changed.emit())
        self._regex_pattern_edit = QLineEdit()
        self._regex_pattern_edit.setPlaceholderText(
            tr("正規表現パターン (例: ^(.+)_v\\d+$)",
               "Regex pattern (e.g. ^(.+)_v\\d+$)"))
        self._regex_pattern_edit.textChanged.connect(lambda _t: self.changed.emit())
        self._regex_replace_edit = QLineEdit()
        self._regex_replace_edit.setPlaceholderText(
            tr("置換文字列 (例: \\1)", "Replacement (e.g. \\1)"))
        self._regex_replace_edit.textChanged.connect(lambda _t: self.changed.emit())

        self._show_mode_params(0)

    def set_removable(self, on: bool):
        self._del_btn.setVisible(bool(on))

    def _on_mode_changed(self, idx: int):
        self._clear_params()
        self._show_mode_params(idx)
        self.changed.emit()

    def _clear_params(self):
        while self._params_layout.count():
            item = self._params_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

    def _show_mode_params(self, idx: int):
        pl = self._params_layout

        if idx == 0:  # 文字列置換
            pl.addWidget(QLabel(tr("検索:", "Find:")), 0, 0)
            pl.addWidget(self._replace_find_edit, 0, 1)
            pl.addWidget(QLabel(tr("置換:", "Replace:")), 0, 2)
            pl.addWidget(self._replace_repl_edit, 0, 3)

        elif idx == 1:  # プレフィックス
            pl.addWidget(QLabel(tr("プレフィックス:", "Prefix:")), 0, 0)
            pl.addWidget(self._prefix_edit, 0, 1)

        elif idx == 2:  # サフィックス
            pl.addWidget(QLabel(tr("サフィックス:", "Suffix:")), 0, 0)
            pl.addWidget(self._suffix_edit, 0, 1)

        elif idx == 3:  # 連番
            pl.addWidget(QLabel(tr("置換トークン:", "Token:")), 0, 0)
            pl.addWidget(self._seq_token_edit, 0, 1)
            pl.addWidget(QLabel(tr("開始番号:", "Start:")), 0, 2)
            pl.addWidget(self._seq_start_spin, 0, 3)
            pl.addWidget(QLabel(tr("桁数:", "Digits:")), 0, 4)
            pl.addWidget(self._seq_pad_spin, 0, 5)

        elif idx == 4:  # 正規表現
            pl.addWidget(QLabel(tr("パターン:", "Pattern:")), 0, 0)
            pl.addWidget(self._regex_pattern_edit, 0, 1, 1, 3)
            pl.addWidget(QLabel(tr("置換:", "Replace:")), 1, 0)
            pl.addWidget(self._regex_replace_edit, 1, 1, 1, 3)

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def rule(self) -> RenameRule:
        idx = self._mode_combo.currentIndex()
        if idx == 0:
            return RenameRule("replace",
                              find=self._replace_find_edit.text(),
                              replace=self._replace_repl_edit.text())
        elif idx == 1:
            return RenameRule("prefix", prefix=self._prefix_edit.text())
        elif idx == 2:
            return RenameRule("suffix", suffix=self._suffix_edit.text())
        elif idx == 3:
            return RenameRule("sequence",
                              token=self._seq_token_edit.text(),
                              start=self._seq_start_spin.value(),
                              pad=self._seq_pad_spin.value())
        elif idx == 4:
            return RenameRule("regex",
                              pattern=self._regex_pattern_edit.text(),
                              replacement=self._regex_replace_edit.text())
        return RenameRule("replace")


class BatchRenameDialog(QDialog):

    renamed = Signal(list)  # List[Tuple[old_path, new_path]]

    def __init__(self, paths: List[str], parent=None):
        super().__init__(parent)
        self._paths = list(paths)
        self._current_results: List[Tuple] = []
        # r146（ユーザー指示 2026-10-13）:
        #   「バッチリネームが選択しているフォルダが有る場合に階層下も
        #     出来るように、「サブフォルダにも適用」ボタンをください。」
        self._has_dirs = any(os.path.isdir(p) for p in self._paths)

        self.setWindowTitle(tr("バッチリネーム", "Batch Rename"))
        self.setMinimumSize(720, 560)
        from ui.dialog_util import apply_window_buttons as _awb
        _awb(self)   # r146: ✕ を必ず押せる状態にする

        self._build_ui()
        self._update_preview()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Rule area ─────────────────────────────────────────────────
        # r146（ユーザー指示 2026-10-15）: ルールは **複数** 持てる。
        # ＋ で増やし、**上から順に** 当てた結果をプレビューに出す。
        rule_group = QGroupBox(tr("変換ルール", "Rename Rules"))
        rule_layout = QVBoxLayout(rule_group)
        rule_layout.setSpacing(8)

        self._rules_box = QWidget()
        self._rules_layout = QVBoxLayout(self._rules_box)
        self._rules_layout.setContentsMargins(0, 0, 0, 0)
        self._rules_layout.setSpacing(6)
        rule_layout.addWidget(self._rules_box)

        add_row = QHBoxLayout()
        self._add_rule_btn = QPushButton(tr("＋ ルールを追加", "+ Add rule"))
        self._add_rule_btn.setAutoDefault(False)
        self._add_rule_btn.setToolTip(tr(
            "ルールは上から順に当たります（1 つ目の結果に 2 つ目を当てる）。",
            "Rules are applied top to bottom (rule 2 runs on rule 1's result)."))
        self._add_rule_btn.clicked.connect(lambda _c=False: self._add_rule())
        add_row.addWidget(self._add_rule_btn)
        add_row.addStretch()
        rule_layout.addLayout(add_row)

        self._rule_rows = []
        self._preview_table = None      # まだ無い（_add_rule が触るので先に置く）
        self._add_rule()

        root.addWidget(rule_group)

        # ── Case options ──────────────────────────────────────────────
        case_row = QHBoxLayout()
        self._case_combo = QComboBox()
        self._case_combo.addItems([tr("変更なし", "No change"), tr("小文字", "lowercase"),
                                   tr("大文字", "UPPERCASE"), tr("タイトルケース", "Title Case")])
        self._case_combo.currentIndexChanged.connect(self._update_preview)
        case_row.addWidget(QLabel(tr("大文字小文字:", "Letter case:")))
        case_row.addWidget(self._case_combo)
        case_row.addSpacing(16)

        # r146: 選択にフォルダが含まれている時だけ出す。
        from core.compat import QCheckBox
        self._recursive_cb = QCheckBox(tr("サブフォルダにも適用",
                                          "Apply to subfolders"))
        self._recursive_cb.setToolTip(tr(
            "選んだフォルダの中身（さらにその下も）を対象に加えます。\n"
            "実行は «深いものから» 行うので、親フォルダの名前が変わっても\n"
            "中身の処理が外れません。",
            "Also include everything inside the selected folders.\n"
            "Renaming runs deepest-first, so renaming a parent folder does "
            "not break the children."))
        self._recursive_cb.setVisible(self._has_dirs)
        self._recursive_cb.toggled.connect(lambda _v=False: self._update_preview())
        case_row.addWidget(self._recursive_cb)
        case_row.addStretch()
        root.addLayout(case_row)

        # ── Preview table ─────────────────────────────────────────────
        root.addWidget(QLabel(tr("プレビュー:", "Preview:")))
        self._preview_table = PreviewTable()
        root.addWidget(self._preview_table)

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            "color:%(on_surface_dim)s;font-size:%(label_px)spx;" % _tv())
        btn_row.addWidget(self._status_label)
        btn_row.addStretch()

        cancel_btn = QPushButton(tr("キャンセル", "Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._apply_btn = QPushButton(tr("▶  実行", "▶  Apply"))
        self._apply_btn.setStyleSheet(
            "QPushButton { background:%(primary)s; color:%(on_primary)s;"
            " font-weight:%(w_strong)s; border:1px solid %(primary)s;"
            " border-radius:%(r_pill)spx; padding:4px 20px; }"
            "QPushButton:hover { background:%(cta_tint_hover)s; }"
            "QPushButton:disabled { background:%(fill_subtle)s;"
            " color:%(on_surface_dim)s; border-color:%(hairline)s; }"
            % _tv()
        )
        self._apply_btn.clicked.connect(self._apply_rename)
        btn_row.addWidget(self._apply_btn)

        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _add_rule(self):
        row = _RuleRow()
        row.changed.connect(self._update_preview)
        row.remove_requested.connect(self._remove_rule)
        self._rule_rows.append(row)
        self._rules_layout.addWidget(row)
        self._sync_rule_buttons()
        self._update_preview()
        return row

    def _remove_rule(self, row):
        if row in self._rule_rows and len(self._rule_rows) > 1:
            self._rule_rows.remove(row)
            row.setParent(None)
            row.deleteLater()
            self._sync_rule_buttons()
            self._update_preview()

    def _sync_rule_buttons(self):
        """1 件しか無い時は «削除» を出さない（消せると空になる）。"""
        many = len(self._rule_rows) > 1
        for r in self._rule_rows:
            r.set_removable(many)

    def _build_rules(self):
        """上から順に当てるルールの一覧（r146）。"""
        return [r.rule() for r in getattr(self, "_rule_rows", [])]

    def _build_rule(self) -> RenameRule:
        """後方互換（1 件目のルール）。"""
        rules = self._build_rules()
        return rules[0] if rules else RenameRule("replace")


    def _apply_case(self, name: str) -> str:
        idx = self._case_combo.currentIndex()
        if idx == 1:
            return name.lower()
        elif idx == 2:
            return name.upper()
        elif idx == 3:
            stem = Path(name).stem.title()
            return stem + Path(name).suffix
        return name

    def _targets(self) -> List[str]:
        """リネーム対象（r146: «サブフォルダにも適用» を反映）。

        並びは «選んだ順 → その中身を上から» のまま。連番モードの番号が
        見た目どおりになるようにするため。実行時だけ深い順に並べ替える。"""
        cb = getattr(self, "_recursive_cb", None)   # 構築途中でも落ちない
        if not (self._has_dirs and cb is not None and cb.isChecked()):
            return list(self._paths)
        out, seen = [], set()

        def _add(p):
            k = os.path.normcase(os.path.abspath(p))
            if k not in seen:
                seen.add(k)
                out.append(p)
        for p in self._paths:
            _add(p)
            if not os.path.isdir(p):
                continue
            try:
                for root, dirs, files in os.walk(p):
                    for n in sorted(dirs) + sorted(files):
                        _add(os.path.join(root, n))
            except OSError:
                continue
        return out

    @staticmethod
    def _deepest_first(pairs):
        """親より先に子をリネームする（親を先に変えると子のパスが迷子になる）。"""
        return sorted(pairs, key=lambda t: len(os.path.normpath(t[0]).split(os.sep)),
                      reverse=True)

    def _new_name_for(self, name: str, index: int) -> str:
        """ルールを **上から順に** 当てた結果の名前（r146）。

        1 つ目の結果に 2 つ目を当てる（«上から順に適用された» 結果を
        そのままプレビューに出すため）。"""
        for rule in self._build_rules():
            name = rule.apply(name, index=index)
        return self._apply_case(name)

    def _update_preview(self):
        if not getattr(self, "_rule_rows", None) \
                or getattr(self, "_preview_table", None) is None:
            return                      # 構築途中（まだ出す先が無い）
        case_results = []
        for i, old in enumerate(self._targets()):
            op = Path(old)
            try:
                new = str(op.parent / self._new_name_for(op.name, i))
                err = None
            except Exception as e:
                new, err = old, str(e)
            case_results.append((old, new, err))

        self._current_results = case_results
        self._preview_table.update_preview(case_results)

        ok_count = sum(1 for _, _, e in case_results if e is None)
        err_count = sum(1 for _, _, e in case_results if e is not None)
        self._status_label.setText(
            tr("%d 件変換可能  /  %d 件  ", "%d of %d can be renamed  ")
            % (ok_count, len(case_results))
            + ((tr("⚠ %d 件エラー", "⚠ %d error(s)") % err_count) if err_count else "")
        )
        self._apply_btn.setEnabled(ok_count > 0 and err_count == 0)

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def _apply_rename(self):
        # Confirm
        ok_paths = [(old, new) for old, new, err in self._current_results
                    if err is None and Path(old).name != Path(new).name]
        if not ok_paths:
            QMessageBox.information(self, tr("情報", "Information"),
                                    tr("変更するファイルはありません。",
                                       "There are no files to rename."))
            return

        # r146: 走っているコピー／移動／削除の対象は触らせない
        from core.file_locks import conflicts
        bad = conflicts([o for o, _n in ok_paths])
        if bad:
            QMessageBox.information(
                self, tr("バッチリネーム", "Batch Rename"),
                tr("処理中のため、いまリネームできない項目があります:\n%s",
                   "Some items are busy and cannot be renamed right now:\n%s")
                % "\n".join("  " + os.path.basename(r.rstrip("/\\"))
                            for r, _l in bad[:10]))
            return

        ret = QMessageBox.question(
            self, tr("実行確認", "Confirm"),
            tr("%d 件のファイルをリネームしますか？",
               "Rename %d file(s)?") % len(ok_paths),
            QMessageBox.Yes | QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return

        # r146: プレビューで出した «変換後» をそのまま使う。
        # ここで batch_rename を呼び直すと、サブフォルダ対応の時に
        # «親を先に変えて子のパスが迷子になる» ことが起きる。
        results = []
        for old_p, new_p in self._deepest_first(ok_paths):
            if os.path.lexists(new_p) and os.path.normcase(new_p) != \
                    os.path.normcase(old_p):
                results.append((old_p, new_p,
                                tr("同名が既にあります", "Target already exists")))
                continue
            ok, err = rename_path(old_p, new_p)
            results.append((old_p, new_p, None if ok else str(err)))

        failed = [(o, n, e) for o, n, e in results if e]
        if failed:
            msg = "\n".join(f"{Path(o).name}: {e}" for o, n, e in failed[:10])
            QMessageBox.warning(self, tr("一部失敗", "Partially Failed"),
                                tr("%d 件のリネームに失敗しました:\n%s",
                                   "Failed to rename %d file(s):\n%s") % (len(failed), msg))

        done = [(o, n) for o, n, e in results if not e]
        self.renamed.emit(done)
        self.accept()
