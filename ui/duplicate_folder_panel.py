"""
Duplicate Folder Finder
========================
選択パス以下を再帰的にスキャンし、同名フォルダが複数箇所に存在する場合に
それらを統合表示するパネル。

用途例
------
- /projects/ 以下で "textures" フォルダが何箇所にあるか確認
- 同名フォルダの内容を横断的に閲覧・コピー・移動

表示
----
QTreeWidget: 重複フォルダ名 → 各場所のパス → その中のファイル
"""

import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.compat import (
    Qt, Signal,
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QToolButton, QLineEdit, QSpinBox, QCheckBox,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView,
    QSplitter, QFrame, QMenu, QAction,
    QMessageBox, QFileDialog, QInputDialog,
    QSize, QColor, QFont, QProgressDialog, QApplication,
    QThread, QObject
)
from core.file_operations import (
    copy_items, move_items, open_with_default_app,
    reveal_in_explorer, format_size, FileOperationError
)


# ---------------------------------------------------------------------------
# Background scanner
# ---------------------------------------------------------------------------

class _ScanSignals(QObject):
    finished = Signal(dict)   # {folder_name: [abs_path, ...]}
    progress = Signal(int)    # number of dirs scanned so far


class FolderScanner(QThread):
    """
    Recursively scans root_path up to max_depth levels.
    Emits finished({name: [paths...]}) when done.
    """

    finished  = Signal(dict)
    progress  = Signal(int)

    def __init__(self, root_path: str, max_depth: int = 6,
                 min_duplicates: int = 2, parent=None):
        super().__init__(parent)
        self._root       = root_path
        self._max_depth  = max_depth
        self._min_dups   = min_duplicates
        self._cancelled  = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        name_to_paths: Dict[str, List[str]] = defaultdict(list)
        count = 0

        for dirpath, dirnames, _ in os.walk(self._root):
            if self._cancelled:
                break

            # Depth check
            rel = os.path.relpath(dirpath, self._root)
            depth = len(Path(rel).parts)
            if depth > self._max_depth:
                dirnames.clear()
                continue

            for d in dirnames:
                name_to_paths[d].append(os.path.join(dirpath, d))

            count += 1
            if count % 50 == 0:
                self.progress.emit(count)

        # Filter to duplicates only
        result = {
            name: paths
            for name, paths in name_to_paths.items()
            if len(paths) >= self._min_dups
        }
        self.finished.emit(result)


# ---------------------------------------------------------------------------
# Duplicate Folder Panel
# ---------------------------------------------------------------------------

class DuplicateFolderPanel(QWidget):
    """
    Signals
    -------
    navigate_requested(path)   : user wants to navigate browser to this path
    """

    navigate_requested = Signal(str)
    copy_requested     = Signal(list, str)   # src_paths, dst_dir
    move_requested     = Signal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scanner: Optional[FolderScanner] = None
        self._data: Dict[str, List[str]] = {}
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # ── Toolbar ───────────────────────────────────────────────────
        tb = QHBoxLayout()

        tb.addWidget(QLabel("スキャン対象:"))
        self._root_edit = QLineEdit()
        self._root_edit.setPlaceholderText("スキャンするルートディレクトリ")
        tb.addWidget(self._root_edit)

        browse_btn = QToolButton()
        browse_btn.setText("…")
        browse_btn.clicked.connect(self._browse_root)
        tb.addWidget(browse_btn)

        tb.addWidget(QLabel("深度:"))
        self._depth_spin = QSpinBox()
        self._depth_spin.setRange(1, 20)
        self._depth_spin.setValue(6)
        self._depth_spin.setFixedWidth(55)
        tb.addWidget(self._depth_spin)

        tb.addWidget(QLabel("最低重複数:"))
        self._min_spin = QSpinBox()
        self._min_spin.setRange(2, 99)
        self._min_spin.setValue(2)
        self._min_spin.setFixedWidth(50)
        tb.addWidget(self._min_spin)

        self._scan_btn = QPushButton("🔍 スキャン")
        self._scan_btn.setStyleSheet(
            "QPushButton { background: #3A7040; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 4px 12px; }"
            "QPushButton:hover { background: #4A8050; }"
        )
        self._scan_btn.clicked.connect(self._start_scan)
        tb.addWidget(self._scan_btn)

        layout.addLayout(tb)

        # Filter
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("🔍"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("フォルダ名でフィルタ…")
        self._filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_edit)

        self._count_label = QLabel("0 件")
        self._count_label.setStyleSheet("color: #888; font-size: 11px;")
        filter_row.addWidget(self._count_label)
        layout.addLayout(filter_row)

        # ── Tree ──────────────────────────────────────────────────────
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["フォルダ名 / パス", "ファイル数", "サイズ"])
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.setAlternatingRowColors(True)

        from core.compat import QHeaderView
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self._tree)

        # Status
        self._status_label = QLabel("スキャンするディレクトリを指定してください")
        self._status_label.setStyleSheet("color: #888; font-size: 11px; padding: 2px;")
        layout.addWidget(self._status_label)

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _browse_root(self):
        d = QFileDialog.getExistingDirectory(self, "スキャン対象を選択",
                                             self._root_edit.text() or str(Path.home()))
        if d:
            self._root_edit.setText(d)

    def set_root(self, path: str):
        """Called externally (e.g. from browser panel context menu)."""
        self._root_edit.setText(path)

    def _start_scan(self):
        root = self._root_edit.text().strip()
        if not root or not os.path.isdir(root):
            QMessageBox.warning(self, "エラー", "有効なディレクトリを指定してください。")
            return

        if self._scanner and self._scanner.isRunning():
            self._scanner.cancel()
            self._scanner.wait()

        self._tree.clear()
        self._status_label.setText("スキャン中...")
        self._scan_btn.setEnabled(False)

        self._scanner = FolderScanner(
            root,
            max_depth=self._depth_spin.value(),
            min_duplicates=self._min_spin.value(),
        )
        self._scanner.finished.connect(self._on_scan_finished)
        self._scanner.progress.connect(lambda n: self._status_label.setText(f"スキャン中… {n} ディレクトリ"))
        self._scanner.start()

    def _on_scan_finished(self, data: Dict[str, List[str]]):
        self._data = data
        self._scan_btn.setEnabled(True)
        self._populate_tree(data)
        self._status_label.setText(
            f"スキャン完了: {len(data)} 件の重複フォルダ名を検出"
        )

    # ------------------------------------------------------------------
    # Tree population
    # ------------------------------------------------------------------

    def _populate_tree(self, data: Dict[str, List[str]], filter_text: str = ""):
        self._tree.clear()
        shown = 0

        for name in sorted(data.keys()):
            if filter_text and filter_text.lower() not in name.lower():
                continue

            paths = data[name]

            # Root item: folder name
            root_item = QTreeWidgetItem()
            root_item.setText(0, f"📁  {name}")
            root_item.setText(1, str(len(paths)))
            f = root_item.font(0)
            f.setBold(True)
            root_item.setFont(0, f)
            root_item.setForeground(0, QColor("#8AB4D4"))

            for path in paths:
                path_item = QTreeWidgetItem(root_item)
                path_item.setText(0, f"  {path}")
                path_item.setToolTip(0, path)
                path_item.setData(0, Qt.UserRole, path)

                # Count files
                try:
                    files = [f for f in os.listdir(path)
                             if os.path.isfile(os.path.join(path, f))]
                    total_size = sum(
                        os.path.getsize(os.path.join(path, f)) for f in files
                    )
                    path_item.setText(1, str(len(files)))
                    path_item.setText(2, format_size(total_size))
                except OSError:
                    path_item.setText(1, "?")
                    path_item.setText(2, "?")

                # Show up to 20 files as children
                try:
                    for fname in sorted(os.listdir(path))[:20]:
                        fpath = os.path.join(path, fname)
                        if os.path.isfile(fpath):
                            f_item = QTreeWidgetItem(path_item)
                            f_item.setText(0, f"    📄 {fname}")
                            f_item.setData(0, Qt.UserRole, fpath)
                            f_item.setForeground(0, QColor("#AAAAAA"))
                except OSError:
                    pass

            self._tree.addTopLevelItem(root_item)
            root_item.setExpanded(True)
            shown += 1

        self._count_label.setText(f"{shown} 件")

    def _apply_filter(self, text: str):
        self._populate_tree(self._data, filter_text=text)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _on_double_click(self, item: QTreeWidgetItem, _col: int):
        path = item.data(0, Qt.UserRole)
        if path and os.path.exists(path):
            self.navigate_requested.emit(
                os.path.dirname(path) if os.path.isfile(path) else path
            )

    def _get_selected_paths(self) -> List[str]:
        paths = []
        for item in self._tree.selectedItems():
            p = item.data(0, Qt.UserRole)
            if p:
                paths.append(p)
        return paths

    def _show_context_menu(self, pos):
        paths = self._get_selected_paths()
        if not paths:
            return

        menu = QMenu(self)
        nav_act = menu.addAction("🗂 ここへ移動")
        nav_act.triggered.connect(lambda: self.navigate_requested.emit(paths[0]))

        reveal_act = menu.addAction("📁 エクスプローラーで表示")
        reveal_act.triggered.connect(lambda: reveal_in_explorer(paths[0]))

        menu.addSeparator()

        copy_act = menu.addAction("📋 コピー...")
        copy_act.triggered.connect(lambda: self._copy_paths(paths))

        move_act = menu.addAction("✂ 移動...")
        move_act.triggered.connect(lambda: self._move_paths(paths))

        menu.exec_(self._tree.viewport().mapToGlobal(pos))

    def _copy_paths(self, paths: List[str]):
        dst = QFileDialog.getExistingDirectory(self, "コピー先を選択")
        if not dst:
            return
        try:
            copy_items(paths, dst)
            self._status_label.setText(f"{len(paths)} 件コピー完了")
        except FileOperationError as e:
            QMessageBox.critical(self, "エラー", str(e))

    def _move_paths(self, paths: List[str]):
        dst = QFileDialog.getExistingDirectory(self, "移動先を選択")
        if not dst:
            return
        try:
            move_items(paths, dst)
            self._status_label.setText(f"{len(paths)} 件移動完了")
        except FileOperationError as e:
            QMessageBox.critical(self, "エラー", str(e))
