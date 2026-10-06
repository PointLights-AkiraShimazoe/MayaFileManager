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
from core.diag import swallow as _swallow  # r112

import os

from core.compat import (
    Qt, Signal, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox,
    QToolButton, QListView, QAbstractItemView, QSortFilterProxyModel,
    QFileInfo, QApplication, QDrag, QMimeData, QUrl, QSize,
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
from core.i18n import tr

_PATH_ROLE = Qt.UserRole + 1
_SIZE_ROLE = Qt.UserRole + 2
_MTIME_ROLE = Qt.UserRole + 3


class _DragListView(QListView):
    """平坦ビューの一覧（r74）: 複数選択したまま D&D できる QListView。

    QStandardItemModel の既定 mimeData はファイル URL を持たないため、
    自前で text/uri-list を作って QDrag する。Explorer 同様、«選択済み項目»
    を修飾キーなしで押した時は選択を崩さず（プレスを保留）、
    - Move 閾値を超えたら選択全体をドラッグ
    - 動かさずに離したらその項目だけの単一選択に確定（clicked も発火）
    未選択項目・修飾キー付きは QListView 標準の選択処理に任せる。
    ドラッグ終了は drag_finished(paths) で通知（落下先が DCC なら親が処理）。"""

    drag_finished = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending = None          # (pos, index, ctrl_only)
        self._path_role = _PATH_ROLE

    def _selected_paths(self):
        out = []
        for idx in self.selectedIndexes():
            p = self.model().data(idx, self._path_role)
            if p:
                out.append(p)
        return out

    def mousePressEvent(self, event):
        try:
            pos = event.position().toPoint()
        except AttributeError:
            pos = event.pos()
        idx = self.indexAt(pos)
        sm = self.selectionModel()
        mods = event.modifiers()
        # r125: Ctrl（Shift 無し）も «ドラッグ候補» にする。
        # 従来は修飾キー無しの時だけ候補にしており、Ctrl を押したまま
        # 掴むと Qt 標準の処理に落ちて «その場でトグル»＝掴んだ項目が
        # 選択から外れていた（ユーザー報告 2026-10-06
        # 「複数選択をし、D&D をすると一つ選択が外れる」）。
        # Explorer と同じく «離した時に初めてトグル» にする。
        ctrl_only = bool(mods & Qt.ControlModifier) and not (mods & Qt.ShiftModifier)
        if (event.button() == Qt.LeftButton and idx.isValid() and sm is not None
                and sm.isSelected(idx) and (mods == Qt.NoModifier or ctrl_only)):
            self._pending = (pos, idx, ctrl_only)
            self.setFocus(Qt.MouseFocusReason)
            return                      # 選択を崩さない（ドラッグ候補）
        self._pending = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pending is not None and (event.buttons() & Qt.LeftButton):
            try:
                pos = event.position().toPoint()
            except AttributeError:
                pos = event.pos()
            if (pos - self._pending[0]).manhattanLength() >= QApplication.startDragDistance():
                self._pending = None
                self._start_drag()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pending is not None:
            _pos, idx, ctrl_only = self._pending
            self._pending = None
            sm = self.selectionModel()
            if sm is not None and idx.isValid():
                from core.compat import QtCore as _QC
                QISM = _QC.QItemSelectionModel
                if ctrl_only:
                    # r125: ドラッグに至らなかった Ctrl+クリック＝トグル解除
                    sm.select(idx, QISM.Deselect | QISM.Rows)
                else:
                    sm.select(idx, QISM.ClearAndSelect | QISM.Rows)
                    sm.setCurrentIndex(idx, QISM.NoUpdate)
                    self.clicked.emit(idx)
            return
        super().mouseReleaseEvent(event)

    def _start_drag(self):
        paths = self._selected_paths()
        if not paths:
            return
        # r90: 親（BrowserPanel）が DCC 対応の mime を用意していればそれを使う
        factory = getattr(self, "_mime_factory", None)
        if callable(factory):
            mime = factory(paths)
        else:
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        self.last_mime = mime
        drag = QDrag(self)
        drag.setMimeData(mime)
        try:
            drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)
        except AttributeError:
            drag.exec_(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)
        self.drag_finished.emit(list(paths))


def _sort_keys():
    return [("name", tr("名前", "Name")), ("type", tr("種類", "Type")),
            ("date", tr("日付", "Date")), ("size", tr("サイズ", "Size"))]


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
    drag_finished = Signal(list)      # r74: D&D 終了（選択パス群）
    view_mode_changed = Signal(str)   # r120: "list" / "thumb"（保存は親の責務）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._icons = QFileIconProvider()
        self._sources = []
        # r120: 表示モード（ユーザー指示「平坦表示でもサムネイルモードを
        # 選べるように」）。カラムと違い平坦カラムは «フォルダ» を持たないので、
        # 記憶はフォルダ別ではなく 1 つ（保存は親＝BrowserPanel が行う）。
        self._view_mode = "list"
        self._thumb_mgr = None
        self._thumb_prefetch_cb = None
        self._item_size = {"list": 16, "thumb": 96}
        self.setMinimumWidth(200)
        self.setObjectName("mfmFlatCol")
        from core.theme_engine import qss_vars
        self.setStyleSheet(
            "#mfmFlatCol{background:%(plane_flat)s;}"
            "QWidget#flatHdr{background:%(plane_flat_header)s;"
            "border-bottom:1px solid %(hairline)s;}"
            "QLineEdit{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:10px;"
            "min-height:18px;max-height:20px;padding:0 8px;}"
            "QComboBox{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:10px;"
            "min-height:18px;max-height:20px;padding:0 8px;}"
            "QToolButton{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:9px;"
            "min-height:16px;max-height:18px;padding:0px 6px;}"
            "QToolButton:hover{background:%(fill_subtle_hover)s;}"
            "QListView{background:%(plane_flat)s;color:%(on_surface)s;border:none;}"
            % qss_vars()
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
        self._title.setPlaceholderText(tr("フィルタ（平坦）", "Filter (flat)"))
        self._title.setClearButtonEnabled(True)
        self._title.setFixedHeight(20)
        self._title.textChanged.connect(lambda t: self._proxy.set_filter(t))
        self._excl = QLineEdit(hdr)
        self._excl.setPlaceholderText(tr("排他", "Exclude"))
        self._excl.setClearButtonEnabled(True)
        self._excl.setFixedHeight(20)
        self._excl.setToolTip(tr("入力に一致するファイルを除外",
                                 "Hide files matching this text"))
        self._excl.textChanged.connect(lambda t: self._proxy.set_exclude(t))
        frow.addWidget(self._title, 1)
        frow.addWidget(self._excl, 1)
        hl.addLayout(frow)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self._sort_combo = QComboBox(hdr)
        self._sort_combo.setFixedHeight(20)
        for key, label in _sort_keys():
            self._sort_combo.addItem(label, key)
        self._order_btn = QToolButton(hdr)
        self._order_btn.setCheckable(True)
        self._order_btn.setFixedSize(26, 20)
        self._order_btn.setText("▲")
        self._order_btn.setToolTip(tr("昇順／降順", "Ascending / Descending"))
        self._sort_combo.currentIndexChanged.connect(lambda _i: self._apply_sort())
        self._order_btn.clicked.connect(lambda _c=False: self._apply_sort())
        self._view_btn = QToolButton(hdr)
        self._view_btn.setText("▦")
        self._view_btn.setCheckable(True)
        self._view_btn.setFixedSize(26, 20)
        self._view_btn.setToolTip(tr("リスト⇄サムネイル切替",
                                     "Toggle list/thumbnail view"))
        self._view_btn.clicked.connect(
            lambda _c=False: self.set_view_mode(
                "thumb" if self._view_mode == "list" else "list", remember=True))
        self._close_btn = QToolButton(hdr)
        self._close_btn.setText("✕")
        self._close_btn.setFixedSize(24, 20)
        self._close_btn.setToolTip(tr("平坦カラムを閉じる", "Close flat column"))
        self._close_btn.clicked.connect(self.closed.emit)
        row.addWidget(self._sort_combo, 1)
        row.addWidget(self._order_btn, 0)
        row.addWidget(self._view_btn, 0)
        row.addWidget(self._close_btn, 0)
        hl.addLayout(row)
        lay.addWidget(hdr, 0)

        self._src = QStandardItemModel(self)
        self._proxy = _FlatProxy(self)
        self._proxy.setSourceModel(self._src)
        self._proxy.sort(0, Qt.AscendingOrder)
        self._view = _DragListView(self)
        self._view.setModel(self._proxy)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._view.setUniformItemSizes(True)
        self._view.setDragEnabled(False)     # ドラッグは _DragListView が自前で行う
        self._view.drag_finished.connect(self.drag_finished)
        self._view.clicked.connect(self._on_clicked)
        self._view.activated.connect(self._on_activated)
        lay.addWidget(self._view, 1)

    # ── r120: 表示モード（リスト／サムネイル）────────────────────────
    def set_thumb_mgr(self, mgr):
        """サムネイル供給元。設定後にサムネイル表示が選べるようになる。"""
        self._thumb_mgr = mgr
        if self._view_mode == "thumb":
            self._apply_view_mode()

    def set_thumb_prefetch_callback(self, cb):
        """サムネイル先読みの依頼先 cb(paths)。サムネイル表示に «してから» 呼ぶ
        （リスト表示で走らせても無駄なだけ。カラム側と同じ考え方）。"""
        self._thumb_prefetch_cb = cb

    def view_mode(self) -> str:
        return self._view_mode

    def set_view_mode(self, mode: str, remember: bool = False):
        """"list" / "thumb" を切り替える。
        remember=True のときだけ view_mode_changed を出す（保存は親の責務。
        復元で呼ばれた分まで保存すると «最近の状態» を上書きしてしまう）。"""
        mode = "thumb" if mode == "thumb" else "list"
        self._view_mode = mode
        try:
            self._view_btn.setChecked(mode == "thumb")
        except Exception as _e:
            _swallow(_e, "ui/flat_column.py set_view_mode(btn)")
        self._apply_view_mode()
        if remember:
            self.view_mode_changed.emit(mode)

    def item_size(self, mode: str = None) -> int:
        return int(self._item_size.get(mode or self._view_mode, 16))

    def set_item_size(self, px: int, mode: str = None):
        """表示サイズ(px)。モードごとに別の値を持つ（カラムと同じ）。"""
        mode = mode or self._view_mode
        self._item_size["thumb" if mode == "thumb" else "list"] = max(8, int(px))
        if (mode == "thumb") == (self._view_mode == "thumb"):
            self._apply_view_mode()

    def _apply_view_mode(self):
        from ui.browser_delegates import ThumbnailDelegate
        px = self.item_size()
        v = self._view
        try:
            v.setIconSize(QSize(px, px))
            if self._view_mode == "thumb" and self._thumb_mgr is not None:
                v.setViewMode(QListView.IconMode)
                v.setResizeMode(QListView.Adjust)
                v.setWrapping(True)
                v.setSpacing(6)
                v.setGridSize(QSize(px + 18, px + ThumbnailDelegate._TEXT_H + 8))
                v.setItemDelegate(ThumbnailDelegate(
                    self._thumb_mgr, px, v, path_of_index=self._path_of))
                self._request_prefetch()
            else:
                v.setViewMode(QListView.ListMode)
                v.setWrapping(False)
                v.setSpacing(0)
                v.setGridSize(QSize())
                # 既定の描画へ戻す（None を渡すのは PySide 版により不安定）
                from core.compat import QStyledItemDelegate
                v.setItemDelegate(QStyledItemDelegate(v))
            v.doItemsLayout()
            v.viewport().update()
        except Exception as _e:
            _swallow(_e, "ui/flat_column.py _apply_view_mode")

    def _request_prefetch(self):
        if self._view_mode != "thumb" or not callable(self._thumb_prefetch_cb):
            return
        try:
            self._thumb_prefetch_cb(self.all_paths())
        except Exception as _e:
            _swallow(_e, "ui/flat_column.py _request_prefetch")

    def refresh_thumbs(self):
        """サムネイルが届いた時の再描画（サムネイル表示の時だけ）。"""
        if self._view_mode == "thumb":
            try:
                self._view.viewport().update()
            except Exception as _e:
                _swallow(_e, "ui/flat_column.py refresh_thumbs")

    def _apply_sort(self):
        key = self._sort_combo.currentData()
        asc = not self._order_btn.isChecked()
        self._order_btn.setText("▲" if asc else "▼")
        self._proxy.set_sort(key, asc)

    def set_recursive(self, recursive: bool):
        """平坦化の深さ: True=全階層（既定）/ False=各フォルダ直下のみ。
        変更時は現在のソースで一覧を作り直す。"""
        recursive = bool(recursive)
        if getattr(self, "_recursive", True) == recursive:
            return
        self._recursive = recursive
        self.set_sources(self._sources)

    def is_recursive(self) -> bool:
        return bool(getattr(self, "_recursive", True))

    def set_sources(self, dirs):
        """選択フォルダ群を設定して平坦一覧を再構築する。"""
        self._sources = list(dirs or [])
        self._src.clear()
        for fp in flatten_files(self._sources,
                                recursive=getattr(self, "_recursive", True)):
            it = QStandardItem(os.path.basename(fp))
            it.setEditable(False)
            try:
                it.setIcon(self._icons.icon(QFileInfo(fp)))
            except Exception as _e:
                _swallow(_e, "ui/flat_column.py:322 set_sources")
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
        self._request_prefetch()      # r120: 中身が変わったら先読みし直す

    def all_paths(self):
        """一覧にある全ファイルパス（連携状態の要求などに使う）。"""
        out = []
        for r in range(self._src.rowCount()):
            p = self._src.item(r).data(_PATH_ROLE)
            if p:
                out.append(p)
        return out

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
            except Exception as _e:
                _swallow(_e, "ui/flat_column.py:365 _on_activated")
