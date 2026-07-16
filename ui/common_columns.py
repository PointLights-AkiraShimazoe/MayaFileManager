"""
Common Folder Column
====================
複数フォルダ選択時に、選択フォルダ群の «子階層に共通して存在する同名フォルダ» を
1カラムとして表示する（平坦カラムの左に挿入）。

- 項目 = 共通名フォルダ（その名前を持つ各ソースの実パス群を保持）
- 選択（Ctrl/Shift複数選択可）すると、選択した共通フォルダ群の配下だけが
  平坦ビューにリストされる（フィルタリング）
- さらにその選択ソース群に共通フォルダがあれば、次の共通カラムが右に増える
  （再帰。共通名が無くなるまで）

仕様の意図: 「複数選択したフォルダの中で、さらに任意のフォルダ以下のものだけを
平坦ビューに出す」ためのドリルダウン・フィルタ。
"""

import os

from core.compat import (
    Qt, Signal, QWidget, QVBoxLayout, QLabel, QListView,
    QAbstractItemView,
)

try:  # PySide6
    from PySide6.QtGui import QStandardItemModel, QStandardItem
except ImportError:  # PySide2
    from PySide2.QtGui import QStandardItemModel, QStandardItem

_SOURCES_ROLE = Qt.UserRole + 11


class CommonFolderColumn(QWidget):
    """共通フォルダのドリルダウン用カラム。"""

    selection_changed = Signal()

    def __init__(self, level: int, parent=None):
        super().__init__(parent)
        self.level = level
        self.setMinimumWidth(160)
        self.setObjectName("mfmCommonCol")
        self.setStyleSheet(
            "#mfmCommonCol{background:rgba(24,32,28,255);}"
            "QLabel#cchdr{background:rgba(34,58,44,240);color:#cfe8d4;"
            "border-bottom:1px solid rgba(110,150,120,150);"
            "padding:3px 6px;font-weight:bold;}"
            "QListView{background:rgba(24,32,28,255);color:#ddd;border:none;}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._hdr = QLabel("⊞ 共通フォルダ", self)
        self._hdr.setObjectName("cchdr")
        lay.addWidget(self._hdr, 0)
        self._model = QStandardItemModel(self)
        self._view = QListView(self)
        self._view.setModel(self._model)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._view.setUniformItemSizes(True)
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self._view, 1)
        sm = self._view.selectionModel()
        if sm is not None:
            sm.selectionChanged.connect(lambda *_a: self.selection_changed.emit())

    def set_entries(self, entries):
        """entries: [(表示名, [ソース実パス...]), ...]（共通名のみ渡す想定）。"""
        self._model.clear()
        for name, sources in entries:
            it = QStandardItem("📁 %s  (%d)" % (name, len(sources)))
            it.setEditable(False)
            it.setData(list(sources), _SOURCES_ROLE)
            it.setToolTip("\n".join(sources))
            self._model.appendRow(it)
        self._hdr.setText("⊞ 子フォルダ  L%d" % (self.level + 1))

    def selected_sources(self):
        """選択された共通フォルダの実パス群（union）。"""
        out = []
        sm = self._view.selectionModel()
        if sm is None:
            return out
        for idx in sm.selectedIndexes():
            srcs = idx.data(_SOURCES_ROLE) or []
            for s in srcs:
                if s not in out:
                    out.append(s)
        return out

    def has_selection(self):
        sm = self._view.selectionModel()
        return sm is not None and bool(sm.selectedIndexes())
