"""
Merge View
==========
複数フォルダ選択時の「マージ表示」用のカスタムモデルと専用パネル。

- MergeModel: core.merge_browse.merge_children を使い、選択フォルダ群の
  «直下1階層だけ» をマージしたツリーを QColumnView に供給する。
  同名フォルダは1ノードに統合（sources に全実パスを保持）、ファイルは全件。
  フォルダノードに入ると、その sources の直下を再び1階層だけマージする。
- MergePanel: 上部バー＋ QColumnView。

既存のブラウズ（QFileSystemModel + proxy）には一切手を入れず、独立して動作する。
"""

import os

from core.compat import (
    Qt, Signal, QModelIndex, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QColumnView, QAbstractItemView, QFileInfo,
    QUrl, QMimeData,
)

try:  # PySide6
    from PySide6.QtCore import QAbstractItemModel
    try:
        from PySide6.QtGui import QFileIconProvider
    except ImportError:
        from PySide6.QtWidgets import QFileIconProvider
except ImportError:  # PySide2
    from PySide2.QtCore import QAbstractItemModel
    from PySide2.QtWidgets import QFileIconProvider

from core.merge_browse import merge_children, flatten_files
from core.file_operations import open_with_default_app


class _MNode:
    __slots__ = ("name", "is_dir", "sources", "path", "parent", "row", "_kids")

    def __init__(self, name, is_dir, sources=None, path=None, parent=None, row=0):
        self.name = name
        self.is_dir = is_dir
        self.sources = sources or []
        self.path = path
        self.parent = parent
        self.row = row
        self._kids = None


class MergeModel(QAbstractItemModel):
    """選択フォルダ群の直下1階層をマージして見せるツリーモデル。"""

    def __init__(self, source_dirs, mode="tree", parent=None):
        super().__init__(parent)
        self._icons = QFileIconProvider()
        self._mode = mode if mode in ("tree", "flat") else "tree"
        # 不可視ルート直下に «アンカー» を1つ置く。統合ビューは rootIndex を
        # このアンカーに設定して使う。最初の統合カラムの «親» が有効indexになり、
        # カラム別フィルタ/ソート（親パスをキー）が最初の列でも効く。
        self._root = _MNode("<root>", True)
        self._anchor = _MNode("⛓統合", True, sources=list(source_dirs or []),
                              parent=self._root, row=0)
        self._root._kids = [self._anchor]
        # 複数選択の統合オーバーライド: id(node) -> 統合元ソース群。
        self._overrides = {}
        # 平坦表示にするノード集合（id(node)）。
        self._flat_nodes = set()

    def anchor_index(self):
        """統合ビューが rootIndex に設定するアンカーindex。"""
        return self.createIndex(0, 0, self._anchor)

    def _effective_sources(self, node):
        return self._overrides.get(id(node), node.sources)

    # --- 子の遅延構築 ---
    def _children(self, node):
        if node._kids is None:
            kids = []
            if not node.is_dir:
                node._kids = []
                return node._kids
            srcs = self._effective_sources(node)
            is_flat = (self._mode == "flat" and node is self._anchor) \
                or (id(node) in self._flat_nodes)
            if is_flat:
                for i, fp in enumerate(flatten_files(srcs)):
                    kids.append(_MNode(os.path.basename(fp), False, path=fp,
                                       parent=node, row=i))
            else:
                folders, files = merge_children(srcs)
                r = 0
                for name, fsrcs in folders:
                    kids.append(_MNode(name, True, sources=fsrcs, parent=node, row=r)); r += 1
                for fp in files:
                    kids.append(_MNode(os.path.basename(fp), False, path=fp, parent=node, row=r)); r += 1
            node._kids = kids
        return node._kids

    # --- QAbstractItemModel 実装 ---
    def index(self, row, column, parent=QModelIndex()):
        if column != 0 or row < 0:
            return QModelIndex()
        pnode = parent.internalPointer() if parent.isValid() else self._root
        kids = self._children(pnode)
        if row < len(kids):
            return self.createIndex(row, column, kids[row])
        return QModelIndex()

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        p = node.parent
        if p is None or p is self._root:
            return QModelIndex()
        return self.createIndex(p.row, 0, p)

    def rowCount(self, parent=QModelIndex()):
        pnode = parent.internalPointer() if parent.isValid() else self._root
        if not pnode.is_dir:
            return 0
        return len(self._children(pnode))

    def columnCount(self, parent=QModelIndex()):
        return 1

    def hasChildren(self, parent=QModelIndex()):
        pnode = parent.internalPointer() if parent.isValid() else self._root
        return bool(pnode.is_dir)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        if role == Qt.DisplayRole:
            return node.name
        if role == Qt.DecorationRole:
            try:
                if node.is_dir:
                    return self._icons.icon(QFileIconProvider.Folder)
                if node.path:
                    return self._icons.icon(QFileInfo(node.path))
            except Exception:
                return None
        if role == Qt.ToolTipRole:
            if node.is_dir:
                return "マージ元:\n" + "\n".join(node.sources)
            return node.path
        return None

    # --- ヘルパー ---
    def is_dir(self, index):
        return index.isValid() and bool(index.internalPointer().is_dir)

    def file_path(self, index):
        if not index.isValid():
            return ""
        node = index.internalPointer()
        if node.is_dir:
            return node.sources[0] if node.sources else ""
        return node.path or ""

    # ------------------------------------------------------------------
    # QFileSystemModel 互換アクセサ（CappedColumnView 機構の再利用用）
    # ------------------------------------------------------------------
    def filePath(self, index):
        return self.file_path(index)

    def isDir(self, index):
        return self.is_dir(index)

    def fileName(self, index):
        return index.internalPointer().name if index.isValid() else ""

    def fileInfo(self, index):
        return QFileInfo(self.file_path(index))

    def lastModified(self, index):
        try:
            return os.path.getmtime(self.file_path(index))
        except OSError:
            return 0.0

    def size(self, index):
        if index.isValid():
            n = index.internalPointer()
            if not n.is_dir and n.path:
                try:
                    return os.path.getsize(n.path)
                except OSError:
                    return 0
        return 0

    def mimeData(self, indexes):
        urls = []
        seen = set()
        for idx in indexes or []:
            if not idx.isValid() or idx.column() != 0:
                continue
            n = idx.internalPointer()
            paths = list(n.sources) if n.is_dir else ([n.path] if n.path else [])
            for p in paths:
                if p and p not in seen:
                    seen.add(p)
                    urls.append(QUrl.fromLocalFile(p))
        md = QMimeData()
        md.setUrls(urls)
        return md

    def sources_for_index(self, index):
        if not index.isValid():
            return []
        n = index.internalPointer()
        return list(self._effective_sources(n)) if n.is_dir else []

    # ------------------------------------------------------------------
    # 複数選択の統合オーバーライド／平坦指定
    # ------------------------------------------------------------------
    def set_merge_override(self, index, sources):
        if not index.isValid():
            return
        n = index.internalPointer()
        self.layoutAboutToBeChanged.emit()
        self._overrides[id(n)] = list(sources or [])
        n._kids = None
        self.layoutChanged.emit()

    def clear_override(self, index):
        if not index.isValid():
            return
        n = index.internalPointer()
        if id(n) in self._overrides:
            self.layoutAboutToBeChanged.emit()
            self._overrides.pop(id(n), None)
            n._kids = None
            self.layoutChanged.emit()

    def set_flat_node(self, index, on):
        if not index.isValid():
            return
        n = index.internalPointer()
        self.layoutAboutToBeChanged.emit()
        if on:
            self._flat_nodes.add(id(n))
        else:
            self._flat_nodes.discard(id(n))
        n._kids = None
        self.layoutChanged.emit()

    def is_flat_node(self, index):
        return index.isValid() and id(index.internalPointer()) in self._flat_nodes


class MergePanel(QWidget):
    """マージ表示の専用パネル（バー＋カラムビュー）。"""

    closed = Signal()
    file_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = None
        self._sources = []
        self._mode = "tree"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QWidget(self)
        bar.setObjectName("mfmMergeBar")
        bar.setStyleSheet(
            "#mfmMergeBar{background:rgba(40,46,60,235);"
            "border-bottom:1px solid rgba(120,120,140,150);}"
            "QLabel{color:#cdd6ff;}"
            "QPushButton{background:rgba(70,76,92,235);color:#fff;"
            "border:1px solid rgba(130,130,150,160);border-radius:4px;padding:2px 10px;}"
            "QPushButton:hover{background:rgba(100,106,126,235);}"
            "QPushButton:checked{background:rgba(60,120,200,245);"
            "border-color:rgba(120,170,240,220);}"
        )
        blay = QHBoxLayout(bar)
        blay.setContentsMargins(4, 2, 4, 2)
        blay.setSpacing(3)
        self._label = QLabel("", bar)
        self._label.setToolTip("マージ表示中のフォルダ")
        self._tree_btn = QPushButton("ツリー", bar)
        self._tree_btn.setCheckable(True)
        self._tree_btn.setFixedHeight(22)
        self._tree_btn.setToolTip("同名フォルダを全階層で統合したツリーをブラウズ")
        self._tree_btn.clicked.connect(lambda: self._set_mode("tree"))
        self._flat_btn = QPushButton("平坦", bar)
        self._flat_btn.setCheckable(True)
        self._flat_btn.setFixedHeight(22)
        self._flat_btn.setToolTip("選択フォルダ以下の全ファイルを階層無視で一覧表示")
        self._flat_btn.clicked.connect(lambda: self._set_mode("flat"))
        self._back_btn = QPushButton("✕", bar)
        self._back_btn.setFixedSize(24, 22)
        self._back_btn.setToolTip("マージ表示を閉じる（通常表示に戻る）")
        self._back_btn.clicked.connect(self.closed.emit)
        blay.addWidget(self._label, 1)
        blay.addWidget(self._tree_btn, 0)
        blay.addWidget(self._flat_btn, 0)
        blay.addWidget(self._back_btn, 0)
        lay.addWidget(bar, 0)

        self._view = QColumnView(self)
        self._view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._view.clicked.connect(self._on_clicked)
        self._view.activated.connect(self._on_activated)
        lay.addWidget(self._view, 1)

    def start(self, source_dirs, mode="tree"):
        self._sources = list(source_dirs)
        self._mode = mode if mode in ("tree", "flat") else "tree"
        names = ", ".join(os.path.basename(p.rstrip("/\\")) or p for p in source_dirs)
        self._label.setText(f"⛓ {len(source_dirs)}フォルダ統合")
        self._label.setToolTip("マージ元: " + names)
        self._rebuild()

    def _set_mode(self, mode):
        if mode == self._mode:
            self._update_mode_buttons()
            return
        self._mode = mode
        self._rebuild()

    def _rebuild(self):
        self._model = MergeModel(self._sources, self._mode, self)
        self._view.setModel(self._model)
        try:
            self._view.setRootIndex(self._model.anchor_index())
        except Exception:
            pass
        self._update_mode_buttons()

    def _update_mode_buttons(self):
        self._tree_btn.setChecked(self._mode == "tree")
        self._flat_btn.setChecked(self._mode == "flat")

    def _on_clicked(self, index):
        if self._model is None or not index.isValid():
            return
        if not self._model.is_dir(index):
            self.file_selected.emit(self._model.file_path(index))

    def _on_activated(self, index):
        if self._model is None or not index.isValid():
            return
        if not self._model.is_dir(index):
            p = self._model.file_path(index)
            if p and os.path.isfile(p):
                try:
                    open_with_default_app(p)
                except Exception:
                    pass
