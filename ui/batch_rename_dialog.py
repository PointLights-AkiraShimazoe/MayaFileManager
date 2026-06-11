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

import os
from pathlib import Path
from typing import List, Tuple, Optional

from core.compat import (
    Qt, Signal,
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QToolButton, QLineEdit, QComboBox,
    QCheckBox, QSpinBox, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox, QColor, QFont, QSize
)
from core.file_operations import RenameRule, batch_rename


# ---------------------------------------------------------------------------
# Preview Table
# ---------------------------------------------------------------------------

class PreviewTable(QTableWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["変換前", "変換後", "状態"])
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
                status = QTableWidgetItem("⚠ エラー")
                status.setForeground(QColor("#E87070"))
                new_item.setForeground(QColor("#E87070"))
            elif old_name == new_name:
                status = QTableWidgetItem("変化なし")
                status.setForeground(QColor("#888888"))
            else:
                # Check if new file already exists
                new_path = Path(new)
                if new_path.exists() and new_path != Path(old):
                    status = QTableWidgetItem("⚠ 競合")
                    status.setForeground(QColor("#E8A070"))
                    new_item.setForeground(QColor("#E8A070"))
                else:
                    status = QTableWidgetItem("✓ OK")
                    status.setForeground(QColor("#70C870"))
                    new_item.setForeground(QColor("#70C870"))

            self.setItem(row, 0, old_item)
            self.setItem(row, 1, new_item)
            self.setItem(row, 2, status)


# ---------------------------------------------------------------------------
# Batch Rename Dialog
# ---------------------------------------------------------------------------

class BatchRenameDialog(QDialog):

    renamed = Signal(list)  # List[Tuple[old_path, new_path]]

    def __init__(self, paths: List[str], parent=None):
        super().__init__(parent)
        self._paths = list(paths)
        self._current_results: List[Tuple] = []

        self.setWindowTitle("バッチリネーム")
        self.setMinimumSize(720, 560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

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
        rule_group = QGroupBox("変換ルール")
        rule_layout = QGridLayout(rule_group)
        rule_layout.setSpacing(8)

        rule_layout.addWidget(QLabel("モード:"), 0, 0)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems([
            "文字列置換",
            "プレフィックス追加",
            "サフィックス追加",
            "連番",
            "正規表現",
        ])
        self._mode_combo.setFixedWidth(160)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        rule_layout.addWidget(self._mode_combo, 0, 1)

        # ── Stacked params area ───────────────────────────────────────
        self._params_widget = QWidget()
        self._params_layout = QGridLayout(self._params_widget)
        self._params_layout.setContentsMargins(0, 0, 0, 0)
        self._params_layout.setSpacing(6)
        rule_layout.addWidget(self._params_widget, 1, 0, 1, 4)

        # 文字列置換
        self._replace_find_edit = QLineEdit()
        self._replace_find_edit.setPlaceholderText("検索文字列")
        self._replace_find_edit.textChanged.connect(self._update_preview)
        self._replace_repl_edit = QLineEdit()
        self._replace_repl_edit.setPlaceholderText("置換文字列")
        self._replace_repl_edit.textChanged.connect(self._update_preview)

        # プレフィックス
        self._prefix_edit = QLineEdit()
        self._prefix_edit.setPlaceholderText("追加するプレフィックス")
        self._prefix_edit.textChanged.connect(self._update_preview)

        # サフィックス
        self._suffix_edit = QLineEdit()
        self._suffix_edit.setPlaceholderText("追加するサフィックス")
        self._suffix_edit.textChanged.connect(self._update_preview)

        # 連番
        self._seq_token_edit = QLineEdit("{n}")
        self._seq_token_edit.setFixedWidth(60)
        self._seq_token_edit.textChanged.connect(self._update_preview)
        self._seq_start_spin = QSpinBox()
        self._seq_start_spin.setRange(0, 99999)
        self._seq_start_spin.setValue(1)
        self._seq_start_spin.valueChanged.connect(self._update_preview)
        self._seq_pad_spin = QSpinBox()
        self._seq_pad_spin.setRange(1, 8)
        self._seq_pad_spin.setValue(3)
        self._seq_pad_spin.valueChanged.connect(self._update_preview)

        # 正規表現
        self._regex_pattern_edit = QLineEdit()
        self._regex_pattern_edit.setPlaceholderText("正規表現パターン (例: ^(.+)_v\\d+$)")
        self._regex_pattern_edit.textChanged.connect(self._update_preview)
        self._regex_replace_edit = QLineEdit()
        self._regex_replace_edit.setPlaceholderText("置換文字列 (例: \\1)")
        self._regex_replace_edit.textChanged.connect(self._update_preview)

        root.addWidget(rule_group)
        self._show_mode_params(0)

        # ── Case options ──────────────────────────────────────────────
        case_row = QHBoxLayout()
        self._case_combo = QComboBox()
        self._case_combo.addItems(["変更なし", "小文字", "大文字", "タイトルケース"])
        self._case_combo.currentIndexChanged.connect(self._update_preview)
        case_row.addWidget(QLabel("大文字小文字:"))
        case_row.addWidget(self._case_combo)
        case_row.addStretch()
        root.addLayout(case_row)

        # ── Preview table ─────────────────────────────────────────────
        root.addWidget(QLabel("プレビュー:"))
        self._preview_table = PreviewTable()
        root.addWidget(self._preview_table)

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        btn_row.addWidget(self._status_label)
        btn_row.addStretch()

        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._apply_btn = QPushButton("▶  実行")
        self._apply_btn.setStyleSheet(
            "QPushButton { background: #4A90D9; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 4px 16px; }"
            "QPushButton:hover { background: #5AA0E9; }"
            "QPushButton:disabled { background: #444; color: #888; }"
        )
        self._apply_btn.clicked.connect(self._apply_rename)
        btn_row.addWidget(self._apply_btn)

        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _on_mode_changed(self, idx: int):
        self._clear_params()
        self._show_mode_params(idx)
        self._update_preview()

    def _clear_params(self):
        while self._params_layout.count():
            item = self._params_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

    def _show_mode_params(self, idx: int):
        pl = self._params_layout

        if idx == 0:  # 文字列置換
            pl.addWidget(QLabel("検索:"), 0, 0)
            pl.addWidget(self._replace_find_edit, 0, 1)
            pl.addWidget(QLabel("置換:"), 0, 2)
            pl.addWidget(self._replace_repl_edit, 0, 3)

        elif idx == 1:  # プレフィックス
            pl.addWidget(QLabel("プレフィックス:"), 0, 0)
            pl.addWidget(self._prefix_edit, 0, 1)

        elif idx == 2:  # サフィックス
            pl.addWidget(QLabel("サフィックス:"), 0, 0)
            pl.addWidget(self._suffix_edit, 0, 1)

        elif idx == 3:  # 連番
            pl.addWidget(QLabel("置換トークン:"), 0, 0)
            pl.addWidget(self._seq_token_edit, 0, 1)
            pl.addWidget(QLabel("開始番号:"), 0, 2)
            pl.addWidget(self._seq_start_spin, 0, 3)
            pl.addWidget(QLabel("桁数:"), 0, 4)
            pl.addWidget(self._seq_pad_spin, 0, 5)

        elif idx == 4:  # 正規表現
            pl.addWidget(QLabel("パターン:"), 0, 0)
            pl.addWidget(self._regex_pattern_edit, 0, 1, 1, 3)
            pl.addWidget(QLabel("置換:"), 1, 0)
            pl.addWidget(self._regex_replace_edit, 1, 1, 1, 3)

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _build_rule(self) -> RenameRule:
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

    def _update_preview(self):
        rule = self._build_rule()
        results = batch_rename(self._paths, rule, dry_run=True)

        # Apply case transform to new names
        case_results = []
        for old, new, err in results:
            if not err:
                new_path = Path(new)
                new_name = self._apply_case(new_path.name)
                new = str(new_path.parent / new_name)
            case_results.append((old, new, err))

        self._current_results = case_results
        self._preview_table.update_preview(case_results)

        ok_count = sum(1 for _, _, e in case_results if e is None)
        err_count = sum(1 for _, _, e in case_results if e is not None)
        self._status_label.setText(
            f"{ok_count} 件変換可能  /  {len(case_results)} 件  "
            + (f"⚠ {err_count} 件エラー" if err_count else "")
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
            QMessageBox.information(self, "情報", "変更するファイルはありません。")
            return

        ret = QMessageBox.question(
            self, "実行確認",
            f"{len(ok_paths)} 件のファイルをリネームしますか？",
            QMessageBox.Yes | QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return

        rule = self._build_rule()
        results = batch_rename(self._paths, rule, dry_run=False)

        failed = [(o, n, e) for o, n, e in results if e]
        if failed:
            msg = "\n".join(f"{Path(o).name}: {e}" for o, n, e in failed[:10])
            QMessageBox.warning(self, "一部失敗",
                                f"{len(failed)} 件のリネームに失敗しました:\n{msg}")

        done = [(o, n) for o, n, e in results if not e]
        self.renamed.emit(done)
        self.accept()
