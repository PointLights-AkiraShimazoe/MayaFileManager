"""
Flat Column
===========
複数フォルダ選択（または単一フォルダ＋平坦化トグル）時に、通常のカラムビューの
«次のカラム» としてインライン表示する「平坦カラム」。別ビューには切り替えない。

- 選択フォルダ群以下の全ファイルを階層無視で平坦に一覧（core.merge_browse.flatten_files）
- そのカラム専用のフィルタ／ソート（名前・種類・日付・サイズ／昇順降順）が使える
- set_sources() で選択が変わるたびに動的更新

QFileSystemModel ベースの通常カラムとは独立に、QStandardItemModel + 軽量プロキシで動く。
"""

import os

from core.compat import (
    Qt, Signal, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox,
    QToolButton, QListView, QAbstractItemView, QSortFilterProxyModel,
    QFileInfo,
)

try:  # PySide6
    from PySide6.QtGui import QStandardItemModel, QStandardItem
    try:
        from PySide6.QtGui import QFileIconProvider
    except ImportError:
        from PySide6.QtWidgets import QFileIconProvider
except ImportError:  # PySide2
    from PySide2.QtGui import QStandardItemModel, QStandardItem
    from PySide2.QtWidgets import QFileIconProvider

from core.merge_browse import flatten_files
from core.file_operations import open_with_default_app

_PATH_ROLE = Qt.UserRole + 1
_SIZE_ROLE = Qt.UserRole + 2
_MTIME_ROLE = Qt.UserRole + 3
_SORT_KEYS = [("name", "名前"), ("type", "種類"), ("date", "日付"), ("size", "サイズ")]


class _FlatProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter = ""
        self._exclude = ""
        self._key = "name"
        self._asc = True
        self.setDynamicSortFilter(True)

    def set_filter(self, text):
        self._filter = (text or "").lower()
        self.invalidateFilter()

    def set_exclude(self, text):
        self._exclude = (text or "").lower()
        self.invalidateFilter()

    def set_sort(self, key, asc):
        self._key = key
        self._asc = asc
        self.sort(-1)
        self.sort(0, Qt.AscendingOrder)

    def filterAcceptsRow(self, row, parent):
        if not self._filter and not self._exclude:
            return True
        idx = self.sourceModel().index(row, 0, parent)
        name = (self.sourceModel().data(idx) or "").lower()
        # 部分一致(substring)。"c00" は "c010" にヒットしない。
        if self._filter and self._filter not in name:
            return False
        if self._exclude and self._exclude in name:
            return False
        return True

    def lessThan(self, l, r):
        try:
            sm = self.sourceModel()
            an = (sm.data(l) or "").lower()
            bn = (sm.data(r) or "").lower()
            if self._key == "size":
                a, b = sm.data(l, _SIZE_ROLE) or 0, sm.data(r, _SIZE_ROLE) or 0
            elif self._key == "date":
                a, b = sm.data(l, _MTIME_ROLE) or 0, sm.data(r, _MTIME_ROLE) or 0
            elif self._key == "type":
                a = an.rsplit(".", 1)[-1] if "." in an else ""
                b = bn.rsplit(".", 1)[-1] if "." in bn else ""
                if a == b:
                    a, b = an, bn
            else:
                a, b = an, bn
            return (a < b) if self._asc else (a > b)
        except Exception:
            return False

    @staticmethod
    def _fuzzy(p, n):
        if p in n:
            return True
        it = iter(n)
        return all(c in it for c in p)


class FlatColumn(QWidget):
    """インライン平坦カラム（ヘッダ＝フィルタ＋ソート、本体＝平坦ファイル一覧）。"""

    file_activated = Signal(str)
    file_selected = Signal(str)
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._icons = QFileIconProvider()
        self._sources = []
        self.setMinimumWidth(200)
        self.setObjectName("mfmFlatCol")
        self.setStyleSheet(
            "#mfmFlatCol{background:rgba(26,30,38,255);}"
            "QWidget#flatHdr{background:rgba(40,46,60,238);"
            "border-bottom:1px solid rgba(120,120,140,150);}"
            "QLineEdit{background:rgba(20,20,20,235);color:#ddd;"
            "border:1px solid rgba(110,110,110,150);border-radius:3px;padding:0 4px;}"
            "QComboBox{background:rgba(45,45,45,235);color:#ddd;"
            "border:1px solid rgba(110,110,110,150);border-radius:3px;padding:0 4px;}"
            "QToolButton{background:rgba(55,55,55,235);color:#ddd;"
            "border:1px solid rgba(110,110,110,150);border-radius:3px;}"
            "QToolButton:hover{background:rgba(90,90,90,235);}"
            "QListView{background:rgba(26,30,38,255);color:#ddd;border:none;}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        hdr = QWidget(self)
        hdr.setObjectName("flatHdr")
        hl = QVBoxLayout(hdr)
        hl.setContentsMargins(3, 3, 3, 3)
        hl.setSpacing(3)
        frow = QHBoxLayout()
        frow.setContentsMargins(0, 0, 0, 0)
        frow.setSpacing(3)
        self._title = QLineEdit(hdr)
        self._title.setPlaceholderText("フィルタ（平坦）")
        self._title.setClearButtonEnabled(True)
        self._title.setFixedHeight(20)
        self._title.textChanged.connect(lambda t: self._proxy.set_filter(t))
        self._excl = QLineEdit(hdr)
        self._excl.setPlaceholderText("排他")
        self._excl.setClearButtonEnabled(True)
        self._excl.setFixedHeight(20)
        self._excl.setToolTip("入力に一致するファイルを除外")
        self._excl.textChanged.connect(lambda t: self._proxy.set_exclude(t))
        frow.addWidget(self._title, 1)
        frow.addWidget(self._excl, 1)
        hl.addLayout(frow)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self._sort_combo = QComboBox(hdr)
        self._sort_combo.setFixedHeight(20)
        for key, label in _SORT_KEYS:
            self._sort_combo.addItem(label, key)
        self._order_btn = QToolButton(hdr)
        self._order_btn.setCheckable(True)
        self._order_btn.setFixedSize(26, 20)
        self._order_btn.setText("▲")
        self._order_btn.setToolTip("昇順／降順")
        self._sort_combo.currentIndexChanged.connect(lambda _i: self._apply_sort())
        self._order_btn.clicked.connect(lambda _c=False: self._apply_sort())
        self._close_btn = QToolButton(hdr)
        self._close_btn.setText("✕")
        self._close_btn.setFixedSize(24, 20)
        self._close_btn.setToolTip("平坦カラムを閉じる")
        self._close_btn.clicked.connect(self.closed.emit)
        row.addWidget(self._sort_combo, 1)
        row.addWidget(self._order_btn, 0)
        row.addWidget(self._close_btn, 0)
        hl.addLayout(row)
        lay.addWidget(hdr, 0)

        self._src = QStandardItemModel(self)
        self._proxy = _FlatProxy(self)
        self._proxy.setSourceModel(self._src)
        self._proxy.sort(0, Qt.AscendingOrder)
        self._view = QListView(self)
        self._view.setModel(self._proxy)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._view.setUniformItemSizes(True)
        self._view.clicked.connect(self._on_clicked)
        self._view.activated.connect(self._on_activated)
        lay.addWidget(self._view, 1)

    def _apply_sort(self):
        key = self._sort_combo.currentData()
        asc = not self._order_btn.isChecked()
        self._order_btn.setText("▲" if asc else "▼")
        self._proxy.set_sort(key, asc)

    def set_sources(self, dirs):
        """選択フォルダ群を設定して平坦一覧を再構築する。"""
        self._sources = list(dirs or [])
        self._src.clear()
        for fp in flatten_files(self._sources):
            it = QStandardItem(os.path.basename(fp))
            it.setEditable(False)
            try:
                it.setIcon(self._icons.icon(QFileInfo(fp)))
            except Exception:
                pass
            it.setData(fp, _PATH_ROLE)
            try:
                st = os.stat(fp)
                it.setData(st.st_size, _SIZE_ROLE)
                it.setData(st.st_mtime, _MTIME_ROLE)
            except OSError:
                pass
            self._src.appendRow(it)
        self._proxy.set_sort(self._sort_combo.currentData() or "name",
                             not self._order_btn.isChecked())

    def selected_paths(self):
        out = []
        for idx in self._view.selectedIndexes():
            p = self._proxy.data(idx, _PATH_ROLE)
            if p:
                out.append(p)
        return out

    def _path_of(self, index):
        return self._proxy.data(index, _PATH_ROLE) if index.isValid() else ""

    def _on_clicked(self, index):
        p = self._path_of(index)
        if p:
            self.file_selected.emit(p)

    def _on_activated(self, index):
        p = self._path_of(index)
        if p and os.path.isfile(p):
            try:
                open_with_default_app(p)
            except Exception:
                pass
