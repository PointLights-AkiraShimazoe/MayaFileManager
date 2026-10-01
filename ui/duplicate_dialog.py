# -*- coding: utf-8 -*-
"""
複製ダイアログ（r67、Ctrl+D）
=============================

- 名前モード: 複製物の名前を1つ入力（既定 «元名_Copy»）。複数選択時は各項目に
  同じ規則（_Copy）を適用するため入力欄は無効。
- Replace モード: Search / Replace で元名を置換した名前にする（複数選択向け）。
  「サブフォルダ以下にも適用」ON なら複製したフォルダの中身の名前にも同じ置換。
- 見た目は MayaFileManager のテーマ（theme_engine の QSS）に従う。
"""

import os
from typing import List, Tuple

from core.compat import (
    Qt, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QToolButton, QPushButton, QCheckBox,
)
from core.i18n import tr
from core.file_operations import default_copy_name, apply_replace_name


class DuplicateDialog(QDialog):
    def __init__(self, paths: List[str], parent=None):
        super().__init__(parent)
        self._paths = [p for p in paths if p]
        self.setWindowTitle(tr("複製", "Duplicate"))
        self.setMinimumWidth(520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._build()
        self._update_preview()

    # ── UI ─────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # モード切替（ピル型トグル）
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(tr("モード:", "Mode:")))
        self._mode_btn = QToolButton()
        self._mode_btn.setCheckable(True)
        self._mode_btn.setObjectName("mfmPillToggle")
        self._mode_btn.setToolTip(tr("OFF: 名前を指定 / ON: Search→Replace で命名",
                                     "OFF: type a name / ON: name by Search→Replace"))
        self._mode_btn.toggled.connect(self._on_mode)
        mode_row.addWidget(self._mode_btn)
        mode_row.addStretch(1)
        n = len(self._paths)
        mode_row.addWidget(QLabel(tr("対象: %d 件", "Items: %d") % n))
        root.addLayout(mode_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        # 名前モード
        self._name_label = QLabel(tr("複製物の名前:", "Name:"))
        self._name_edit = QLineEdit()
        if n == 1:
            self._name_edit.setText(default_copy_name(self._paths[0]))
        else:
            self._name_edit.setText(tr("（複数選択: 各項目に _Copy を付けます）",
                                       "(multiple: _Copy is appended to each)"))
            self._name_edit.setEnabled(False)
        self._name_edit.textChanged.connect(self._update_preview)
        grid.addWidget(self._name_label, 0, 0)
        grid.addWidget(self._name_edit, 0, 1)
        # Replace モード
        self._search_label = QLabel("Search:")
        self._search_edit = QLineEdit()
        self._replace_label = QLabel("Replace:")
        self._replace_edit = QLineEdit()
        for e in (self._search_edit, self._replace_edit):
            e.textChanged.connect(self._update_preview)
        grid.addWidget(self._search_label, 1, 0)
        grid.addWidget(self._search_edit, 1, 1)
        grid.addWidget(self._replace_label, 2, 0)
        grid.addWidget(self._replace_edit, 2, 1)
        self._recursive_cb = QCheckBox(tr("サブフォルダ以下の名前にも適用",
                                          "Also apply inside subfolders"))
        self._recursive_cb.setToolTip(tr(
            "複製したフォルダの中のファイル/フォルダ名にも同じ Search→Replace を適用",
            "Apply the same Search→Replace to names inside duplicated folders"))
        grid.addWidget(self._recursive_cb, 3, 1)
        root.addLayout(grid)

        self._preview = QLabel()
        self._preview.setObjectName("mfmDupPreview")
        self._preview.setWordWrap(True)
        root.addWidget(self._preview)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(tr("キャンセル", "Cancel"))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        self._ok = QPushButton(tr("複製", "Duplicate"))
        self._ok.setDefault(True)
        self._ok.clicked.connect(self.accept)
        btns.addWidget(self._ok)
        root.addLayout(btns)

        self._on_mode(False)
        # 名前欄は拡張子を除いて選択（Explorer 準拠）
        if n == 1 and self._name_edit.isEnabled():
            name = self._name_edit.text()
            stem = name if os.path.isdir(self._paths[0]) else os.path.splitext(name)[0]
            self._name_edit.setSelection(0, len(stem))
            self._name_edit.setFocus()

    def _on_mode(self, replace_mode: bool):
        self._mode_btn.setText(tr("Replace モード: ON", "Replace mode: ON") if replace_mode
                               else tr("Replace モード: OFF", "Replace mode: OFF"))
        for w in (self._name_label, self._name_edit):
            w.setVisible(not replace_mode)
        for w in (self._search_label, self._search_edit, self._replace_label,
                  self._replace_edit, self._recursive_cb):
            w.setVisible(replace_mode)
        if replace_mode:
            self._search_edit.setFocus()
        self._update_preview()

    # ── 結果 ────────────────────────────────────────────────────────────
    def specs(self) -> List[Tuple[str, str]]:
        """[(src, dst), ...]（同じ親フォルダ内）。"""
        out = []
        taken = set()
        replace_mode = self._mode_btn.isChecked()
        search, repl = self._search_edit.text(), self._replace_edit.text()
        for p in self._paths:
            parent, name = os.path.dirname(p), os.path.basename(p.rstrip("/\\"))
            if replace_mode:
                new = apply_replace_name(name, search, repl) if search else \
                    default_copy_name(p, taken)
                if new == name:               # 置換対象なし → _Copy に退避
                    new = default_copy_name(p, taken)
            elif len(self._paths) == 1:
                new = self._name_edit.text().strip() or default_copy_name(p, taken)
            else:
                new = default_copy_name(p, taken)
            taken.add(new)
            out.append((p, os.path.join(parent, new)))
        return out

    def rename_inside(self):
        if self._mode_btn.isChecked() and self._recursive_cb.isChecked() \
                and self._search_edit.text():
            return (self._search_edit.text(), self._replace_edit.text())
        return None

    def _update_preview(self):
        try:
            specs = self.specs()
        except Exception:
            specs = []
        lines = []
        bad = False
        for src, dst in specs[:6]:
            n = os.path.basename(dst)
            mark = ""
            if os.path.exists(dst):
                mark = tr("  ← 既に存在", "  ← already exists")
                bad = True
            if any(c in n for c in '\\/:*?"<>|'):
                mark = tr("  ← 使えない文字", "  ← invalid characters")
                bad = True
            lines.append("%s → %s%s" % (os.path.basename(src), n, mark))
        if len(specs) > 6:
            lines.append(tr("…他 %d 件", "… %d more") % (len(specs) - 6))
        self._preview.setText("\n".join(lines))
        self._ok.setEnabled(bool(specs) and not bad)
