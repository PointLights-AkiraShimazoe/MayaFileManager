# -*- coding: utf-8 -*-
"""名前の衝突ダイアログ（r87）

D&D やペーストで «移動先に同じ名前がある» 時に出す。
選択肢は Explorer と同じ 3 つ:

  - 上書き        … 既存を置き換える（フォルダ同士なら中身を統合）
  - 名前を変えて  … 連番（name_1）を付けて両方残す
  - スキップ      … その項目は移動しない

「残りすべてに適用」を ON にすると、以降の衝突は同じ選択で自動処理する。
ダイアログを閉じる／キャンセルしたら «操作全体を中止»（= cancelled）。
"""

import os
from datetime import datetime

from core.compat import (
    Qt, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QCheckBox,
)
from core.i18n import tr


def _info(path: str) -> str:
    """サイズと更新日時（判断材料。取れなければ空）。"""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    if os.path.isdir(path):
        size = tr("フォルダ", "Folder")
    else:
        n = float(st.st_size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                size = "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
                break
            n /= 1024
    return "%s　|　%s" % (size, datetime.fromtimestamp(st.st_mtime)
                          .strftime("%Y-%m-%d %H:%M"))


class ConflictDialog(QDialog):
    """戻り値は exec 後に choice() / apply_to_all() で取る。
    reject（×・Esc・キャンセル）＝操作全体の中止。"""

    OVERWRITE = "overwrite"
    RENAME = "rename"
    SKIP = "skip"

    def __init__(self, src: str, dst: str, remaining: int = 0, parent=None):
        super().__init__(parent)
        self._choice = None
        self._src = src
        self._dst = dst
        self.setWindowTitle(tr("同じ名前があります", "Name already exists"))
        self.setMinimumWidth(560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        name = os.path.basename(dst.rstrip("\\/")) or dst
        head = QLabel(tr("移動先に «%s» が既にあります。どうしますか？",
                         "«%s» already exists in the destination. What do you want to do?")
                      % name)
        head.setWordWrap(True)
        root.addWidget(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.addWidget(QLabel(tr("移動するもの:", "Moving:")), 0, 0)
        grid.addWidget(QLabel(_info(src) or src), 0, 1)
        grid.addWidget(QLabel(tr("既にあるもの:", "Existing:")), 1, 0)
        grid.addWidget(QLabel(_info(dst) or dst), 1, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        if os.path.isdir(src) and os.path.isdir(dst):
            note = QLabel(tr("※ フォルダ同士の «上書き» は中身を統合します"
                             "（同名ファイルだけ置き換え）",
                             "* Overwriting a folder merges its contents"))
            note.setWordWrap(True)
            note.setObjectName("mfmConflictNote")
            root.addWidget(note)

        self._all = QCheckBox(tr("残りすべてに適用", "Apply to all remaining"))
        if remaining > 0:
            self._all.setText(tr("残りすべてに適用（あと %d 件）",
                                 "Apply to all remaining (%d more)") % remaining)
        else:
            self._all.setVisible(False)
        root.addWidget(self._all)

        btns = QHBoxLayout()
        cancel = QPushButton(tr("中止", "Cancel"))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        btns.addStretch(1)
        skip = QPushButton(tr("スキップ", "Skip"))
        skip.setAutoDefault(False)
        skip.clicked.connect(lambda: self._pick(self.SKIP))
        btns.addWidget(skip)
        ren = QPushButton(tr("名前を変えて移動", "Keep both"))
        ren.setAutoDefault(False)
        ren.clicked.connect(lambda: self._pick(self.RENAME))
        btns.addWidget(ren)
        over = QPushButton(tr("上書き", "Replace"))
        over.setDefault(True)
        over.clicked.connect(lambda: self._pick(self.OVERWRITE))
        btns.addWidget(over)
        root.addLayout(btns)

    def _pick(self, choice: str):
        self._choice = choice
        self.accept()

    def choice(self):
        return self._choice

    def apply_to_all(self) -> bool:
        return bool(self._all.isChecked())
