"""
Browser Panel
=============
The main file browser widget.  Uses a column-based view (QColumnView) with a
custom proxy model for filtering / sorting and a thumbnail delegate.

Features implemented here
--------------------------
* Column view with configurable max depth
* Auto-width columns to longest visible item
* Sort by name / type / timestamp
* Text filter
* Single/double click action switching
* Drag-and-drop (source: file paths)
* Context menu with all file actions
* Thumbnail display via ThumbnailDelegate
* Drive selector (top-left combo)
"""

import os
import struct
from pathlib import Path
from typing import List, Optional, Callable

from core.compat import (
    Qt, Signal, QObject,
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QToolButton,
    QSplitter, QTreeView, QColumnView, QListView,
    QSizePolicy, QFrame, QAbstractItemView, QHeaderView,
    QFileSystemModel, QSortFilterProxyModel,
    QMenu, QAction, QMessageBox, QFileDialog, QInputDialog,
    QStyledItemDelegate, QStyle, QStyleOption,
    QModelIndex, QSize, QPixmap, QPainter, QColor, QFont,
    QDir, QFileInfo, QUrl, QMimeData, QPoint,
    QFontMetrics, QTimer, QKeySequence
)
from core.compat import QtCore as _QtCore
from core.path_guard import PathProber, DriveScanner, invalidate_cache
from core.file_operations import (
    open_with_default_app, reveal_in_explorer,
    copy_items, move_items, delete_items,
    get_file_type_category, list_drives, format_size,
    resolve_windows_shortcut,
    FileOperationError, MAYA_EXTENSIONS, SCENE_EXTENSIONS
)
from core.thumbnail_generator import ThumbnailManager


# ---------------------------------------------------------------------------
# Thumbnail Delegate
# ---------------------------------------------------------------------------

class ThumbnailDelegate(QStyledItemDelegate):
    """
    Paints a thumbnail to the left of the file name in list/icon views.
    Falls back to system icon when thumbnail is not yet ready.
    """

    def __init__(self, thumb_mgr: ThumbnailManager, thumb_size: int = 64, parent=None):
        super().__init__(parent)
        self._mgr = thumb_mgr
        self._thumb_size = thumb_size

    def sizeHint(self, option, index) -> QSize:
        return QSize(self._thumb_size + 8, self._thumb_size + 8)

    def paint(self, painter: QPainter, option, index: QModelIndex):
        self.initStyleOption(option, index)

        # Draw selection background
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor("#2A5080"))

        # Thumbnail area
        thumb_rect = option.rect.adjusted(4, 4, -4, -4)
        thumb_rect.setWidth(self._thumb_size)
        thumb_rect.setHeight(self._thumb_size)

        model = index.model()
        source_model = model
        source_index = index
        # Unwrap proxy
        while hasattr(source_model, "sourceModel"):
            source_index = source_model.mapToSource(source_index)
            source_model = source_model.sourceModel()

        file_path = source_model.filePath(source_index) if hasattr(source_model, "filePath") else ""

        if file_path:
            pixmap = self._mgr.get(file_path)
            if pixmap and not pixmap.isNull():
                scaled = pixmap.scaled(self._thumb_size, self._thumb_size,
                                       Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = thumb_rect.x() + (self._thumb_size - scaled.width()) // 2
                y = thumb_rect.y() + (self._thumb_size - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)

        # File name
        text_rect = option.rect.adjusted(self._thumb_size + 8, 4, -4, -4)
        painter.setPen(QColor("#FFFFFF") if option.state & QStyle.State_Selected else QColor("#CCCCCC"))
        fm = QFontMetrics(option.font)
        display = index.data(Qt.DisplayRole) or ""
        elided = fm.elidedText(display, Qt.ElideMiddle, text_rect.width())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)


# ---------------------------------------------------------------------------
# Filter Proxy Model
# ---------------------------------------------------------------------------

class FileFilterProxyModel(QSortFilterProxyModel):
    """Filters by filename substring and controls sort column."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter = ""
        self._show_hidden = False
        # 隠し属性でも常に表示するパス集合（normcase済み）。
        # ショートカット先が AppData 等の隠しフォルダを経由する場合に、
        # その経路の祖先だけを表示してカラムチェーンを構築可能にする。
        self._force_visible = set()
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)

    def set_filter_string(self, text: str):
        self._filter = text.lower()
        self.invalidateFilter()

    def set_show_hidden(self, show: bool):
        self._show_hidden = show
        self.invalidateFilter()

    def set_force_visible(self, paths):
        """隠し属性でも表示する祖先パス集合を設定する。"""
        self._force_visible = set(os.path.normcase(os.path.normpath(p)) for p in paths)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)

        # Hidden files（隠し属性 or ドット名）。ただし現在ナビ中の経路の祖先は常に表示。
        if not self._show_hidden:
            try:
                fp = os.path.normcase(os.path.normpath(source_model.filePath(index)))
            except Exception:
                fp = ""
            if fp not in self._force_visible:
                name = source_model.fileName(index)
                is_hidden = name.startswith(".")
                if not is_hidden:
                    try:
                        is_hidden = source_model.fileInfo(index).isHidden()
                    except Exception:
                        is_hidden = False
                if is_hidden:
                    return False

        # Text filter
        if self._filter:
            name = source_model.fileName(index).lower()
            if source_model.isDir(index):
                return True  # Always show dirs so tree stays navigable
            return self._fuzzy_match(self._filter, name)

        return True

    @staticmethod
    def _fuzzy_match(pattern: str, name: str) -> bool:
        """
        部分一致 or 順序保存サブシーケンス一致 (N-3 ファジー検索)。
        例: 'chrahair' → 'chr_A_hair_sim_v012.ma' にヒット
        """
        if pattern in name:
            return True
        it = iter(name)
        return all(c in it for c in pattern)


# ---------------------------------------------------------------------------
# Column-capped View
# ---------------------------------------------------------------------------

class CappedColumnView(QColumnView):
    """
    QColumnView with configurable maximum column depth.

    仕様: 選択がmax_depthより深くなったら、ルートを1段下げて
    可視カラム数をmax_depth以内に保つ（macOS Finder相当の挙動）。

    実装注意: currentChanged の中で setRootIndex を呼ぶと
    QColumnView内部のカラム再構築と競合してクラッシュするため、
    QTimer.singleShot(0) でイベントループ一巡後に実行する。
    """

    def __init__(self, max_depth: int = 4, parent=None):
        super().__init__(parent)
        self._max_depth = max_depth
        self._go_up_cb = None

    def set_max_depth(self, depth: int):
        self._max_depth = depth

    def set_go_up_callback(self, cb):
        """◀ボタン押下時に呼ぶコールバック（1階層上げる）を登録。"""
        self._go_up_cb = cb

    def createColumn(self, index):
        """各カラム生成時に、左部中央へ『◀ 上の階層へ』ボタンを重ねる。"""
        view = super().createColumn(index)
        # 各カラム(子ビュー)へ Explorer 互換のD&D設定を適用。
        # これを怠るとカラム上でのドロップが効かない。
        try:
            view.setDragEnabled(True)
            view.setAcceptDrops(True)
            view.setDropIndicatorShown(True)
            view.setDragDropMode(QAbstractItemView.DragDrop)
            view.setDefaultDropAction(Qt.MoveAction)
            view.setDragDropOverwriteMode(False)
            view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        except Exception:
            pass
        # このカラムが表示しているフォルダ（◀でこの階層を1つ上へ）
        folder_path = self._path_for_index(index)
        btn = QToolButton(view.viewport())   # 項目の上に重ねて確実に見せる
        btn.setText("◀")
        btn.setToolTip("上の階層へ")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(24, 40)             # 幅を広げて見やすく
        btn.setStyleSheet(
            "QToolButton{background:rgba(40,40,40,215);"
            "border:1px solid rgba(130,130,130,190);border-radius:4px;"
            "color:#ffffff;font-size:13px;font-weight:bold;}"
            "QToolButton:hover{background:rgba(105,105,105,235);}"
        )
        btn.clicked.connect(lambda checked=False, p=folder_path: self._emit_go_up(p))
        view._mfm_up_btn = btn
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)
        self._reposition_up_button(view)
        btn.show()
        btn.raise_()
        return view

    def _emit_go_up(self, folder_path=None):
        if callable(self._go_up_cb):
            self._go_up_cb(folder_path)

    def _path_for_index(self, index):
        """そのカラムが表示しているフォルダのフルパスを返す（プロキシ→ソース解決）。"""
        m = self.model()
        if m is None or not index.isValid():
            return ""
        idx = index
        src = m
        while hasattr(src, "mapToSource"):
            idx = src.mapToSource(idx)
            src = src.sourceModel()
        return src.filePath(idx) if hasattr(src, "filePath") else ""

    def _reposition_up_button(self, view):
        btn = getattr(view, "_mfm_up_btn", None)
        if btn is None:
            return
        vp = view.viewport()
        y = max(0, (vp.height() - btn.height()) // 2)
        btn.move(2, y)
        btn.raise_()

    def eventFilter(self, obj, event):
        et = event.type()
        if et in (_QtCore.QEvent.Resize, _QtCore.QEvent.Show):
            view = obj if getattr(obj, "_mfm_up_btn", None) is not None else obj.parent()
            if view is not None and getattr(view, "_mfm_up_btn", None) is not None:
                self._reposition_up_button(view)
        elif et == _QtCore.QEvent.Wheel and (event.modifiers() & Qt.ShiftModifier):
            # Shift+ホイールでブラウジングエリアを横スクロール（カラム間移動）
            hbar = self.horizontalScrollBar()
            if hbar is not None:
                d = event.angleDelta().y() or event.angleDelta().x()
                hbar.setValue(hbar.value() - d)
                return True
        elif et == _QtCore.QEvent.MouseButtonPress:
            # カラムの何もない所をクリックしても選択を解除しない（無反応にする）
            view = obj.parent()
            if view is not None and hasattr(view, "indexAt"):
                try:
                    pos = event.position().toPoint()
                except AttributeError:
                    pos = event.pos()
                if not view.indexAt(pos).isValid():
                    return True
        return super().eventFilter(obj, event)

    def currentChanged(self, current: QModelIndex, previous: QModelIndex):
        super().currentChanged(current, previous)
        if self._max_depth <= 0 or not current.isValid():
            return
        # ルートから current までの祖先チェーンを構築
        root = self.rootIndex()
        chain = []
        idx = current
        while idx.isValid() and idx != root:
            chain.append(idx)
            idx = idx.parent()
        # 可視カラム数 = チェーン長（root直下=1カラム目）
        if len(chain) > self._max_depth:
            new_root = _QtCore.QPersistentModelIndex(chain[-1])
            QTimer.singleShot(0, lambda: self._apply_root_shift(new_root))

    def _apply_root_shift(self, persistent_root):
        if not persistent_root.isValid() or self.model() is None:
            return
        idx = self.model().index(persistent_root.row(),
                                 persistent_root.column(),
                                 persistent_root.parent())
        if idx.isValid():
            self.setRootIndex(idx)


# ---------------------------------------------------------------------------
# Browser Panel
# ---------------------------------------------------------------------------

class BrowserPanel(QWidget):
    """
    Full-featured file browser widget.

    Signals
    -------
    file_activated(path)      : user performed the primary action on a file
    directory_changed(path)   : user navigated to a new directory
    selection_changed(paths)  : current selection changed
    status_message(text)      : short status for the status bar
    """

    file_activated = Signal(str)
    directory_changed = Signal(str)
    selection_changed = Signal(list)
    status_message = Signal(str)
    bookmark_requested = Signal(list)   # paths to add to bookmarks

    def __init__(self, settings_manager, thumb_manager: ThumbnailManager, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        self._thumb_mgr = thumb_manager
        self._thumb_mgr.thumbnail_ready.connect(self._on_thumbnail_ready)

        self._current_path = str(Path.home())
        # 前回パス復元モードが有効なら前回終了時のパスから開始
        if self._sm.get("restore_last_path", False):
            _last = self._sm.get("last_path", "")
            if _last:
                self._current_path = _last
        # フルパス保持（深度キャップ廃止）。保存設定に関係なく無制限。
        self._max_depth = 0
        self._click_action = self._sm.get("single_click_action", "preview")
        self._dbl_click_action = self._sm.get("double_click_action", "open")

        # External callbacks
        self._on_open: Optional[Callable[[str], None]] = None
        self._on_import: Optional[Callable[[str], None]] = None
        self._on_reference: Optional[Callable[[str], None]] = None

        # N-1: 非同期パスプローブ＆ドライブ隔離（UIフリーズ対策）
        self._prober = PathProber(self)
        self._prober.probed.connect(self._on_probe_result)
        self._drive_scanner = DriveScanner(self)
        self._drive_scanner.drives_ready.connect(self._on_drives_ready)
        self._pending_nav = None
        # 深いパス(遅延ロード)へ setCurrentIndex を再適用するための保留先
        self._pending_current = None

        # N-2: Quick Look（遅延生成）
        self._quick_look = None

        self._build_ui()
        self._navigate(self._current_path)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Toolbar ──────────────────────────────────────────────────
        toolbar = QFrame()
        toolbar.setFrameShape(QFrame.StyledPanel)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(4, 4, 4, 4)
        tb_layout.setSpacing(4)

        # Drive selector
        self._drive_combo = QComboBox()
        self._drive_combo.setFixedWidth(80)
        self._drive_combo.currentTextChanged.connect(self._on_drive_changed)
        tb_layout.addWidget(self._drive_combo)
        self._refresh_drives()

        # Back / forward / up
        # 視認性向上: 大きめ・太字・はっきりした矢印グリフ＋ホバー強調
        _nav_style = (
            "QToolButton{"
            "  background:#3a3a3a; color:#f0f0f0;"
            "  border:1px solid #555; border-radius:4px;"
            "  font-size:18px; font-weight:bold; padding:0px;"
            "}"
            "QToolButton:hover{ background:#4A90D9; color:#ffffff; border-color:#4A90D9; }"
            "QToolButton:pressed{ background:#2A5080; }"
            "QToolButton:disabled{ color:#777; background:#2c2c2c; border-color:#3a3a3a; }"
        )
        for icon_text, tip, slot in [
            ("◀", "戻る", self._go_back),    # ◀ 黒塗り三角（小さな←より視認性が高い）
            ("▶", "進む", self._go_forward),  # ▶
            ("▲", "上へ", self._go_up),       # ▲
        ]:
            btn = QToolButton()
            btn.setText(icon_text)
            btn.setToolTip(tip)
            btn.setFixedSize(36, 30)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_nav_style)
            btn.clicked.connect(slot)
            tb_layout.addWidget(btn)

        # Address bar
        self._addr_bar = QLineEdit()
        self._addr_bar.setPlaceholderText("パスを入力...")
        self._addr_bar.returnPressed.connect(lambda: self._navigate(self._addr_bar.text()))
        tb_layout.addWidget(self._addr_bar)

        # Filter
        tb_layout.addWidget(QLabel("🔍"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("フィルター...")
        self._filter_edit.setFixedWidth(140)
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        tb_layout.addWidget(self._filter_edit)

        # View mode
        self._view_mode_combo = QComboBox()
        self._view_mode_combo.addItems(["カラム", "リスト", "サムネイル"])
        self._view_mode_combo.currentIndexChanged.connect(self._on_view_mode_changed)
        tb_layout.addWidget(self._view_mode_combo)

        # Sort
        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["名前", "種類", "更新日時"])
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        tb_layout.addWidget(self._sort_combo)

        # ツールバー(アドレスバー行)は本来の高さに固定する。
        # これを怠ると縦方向にも伸びてビューの空間を奪う（上部に巨大な空白が出る）。
        toolbar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(toolbar, 0)

        # ── Main view stack ───────────────────────────────────────────
        self._view_stack = QSplitter(Qt.Horizontal)

        # Shared filesystem model
        self._fs_model = QFileSystemModel()
        self._fs_model.setRootPath("")
        # QDir.Hidden を含めることで、AppData 等の隠しフォルダもモデルに載せる。
        # 表示の可否は proxy 側で制御（既定は非表示、ナビ経路の祖先のみ強制表示）。
        self._fs_model.setFilter(
            QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Files | QDir.Hidden
        )
        # 読み取り専用を解除 → Qt標準のファイルD&D（Explorerとの相互コピー/移動）を有効化。
        # これにより各アイテムに Drag/Drop フラグが付与される。
        self._fs_model.setReadOnly(False)
        # 深いパス(遅延ロード)のカラム構築を確実にするためのリトライ
        self._fs_model.directoryLoaded.connect(self._on_fs_dir_loaded)

        # Proxy model
        self._proxy = FileFilterProxyModel()
        self._proxy.setSourceModel(self._fs_model)
        self._proxy.setSortRole(Qt.DisplayRole)

        # Column view
        self._column_view = CappedColumnView(self._max_depth)
        self._column_view.set_go_up_callback(self._column_go_up)
        self._column_view.setModel(self._proxy)
        self._column_view.activated.connect(self._on_item_activated)
        self._column_view.clicked.connect(self._on_item_clicked)
        self._column_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._column_view.customContextMenuRequested.connect(self._show_context_menu)
        self._configure_dnd(self._column_view)
        self._column_view.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # Thumbnail list view
        self._thumb_view = QListView()
        self._thumb_view.setModel(self._proxy)
        # （Quick Look 用イベントフィルタは _build_ui 末尾で両ビューに設置）
        self._thumb_view.setViewMode(QListView.IconMode)
        self._thumb_view.setResizeMode(QListView.Adjust)
        self._thumb_view.setSpacing(4)
        self._thumb_delegate = ThumbnailDelegate(
            self._thumb_mgr,
            self._sm.get("thumbnail_size", 128)
        )
        self._thumb_view.setItemDelegate(self._thumb_delegate)
        self._thumb_view.setGridSize(QSize(
            self._sm.get("thumbnail_size", 128) + 16,
            self._sm.get("thumbnail_size", 128) + 32
        ))
        self._thumb_view.activated.connect(self._on_item_activated)
        self._thumb_view.clicked.connect(self._on_item_clicked)
        self._thumb_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._thumb_view.customContextMenuRequested.connect(self._show_context_menu)
        self._configure_dnd(self._thumb_view)
        self._thumb_view.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self._view_stack.addWidget(self._column_view)
        self._view_stack.addWidget(self._thumb_view)
        self._thumb_view.hide()

        # stretch=1 で残りの縦空間をすべてビューに割り当てる
        layout.addWidget(self._view_stack, 1)

        # ── History navigation ────────────────────────────────────────
        self._history: List[str] = []
        self._history_index: int = -1

        # ファイルのD&Dはビュー(モデル)側で一括処理するため、パネル自身は
        # ドロップを受け取らない（横取りして「移動だけ」になるのを防ぐ）。
        self.setAcceptDrops(False)

        # N-2: Space Quick Look 用イベントフィルタ
        self._column_view.installEventFilter(self)
        self._thumb_view.installEventFilter(self)

        # Ctrl+C / Ctrl+X / Ctrl+V / Delete（子ビューにフォーカスがあっても発火）
        self._install_clipboard_actions()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Quick Look (N-2)
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if (event.type() == _QtCore.QEvent.KeyPress
                and event.key() == Qt.Key_Space
                and obj in (self._column_view, self._thumb_view)):
            self._toggle_quick_look()
            return True
        return super().eventFilter(obj, event)

    def _toggle_quick_look(self):
        paths = self._get_selected_paths()
        if not paths:
            return
        if self._quick_look is None:
            from ui.quick_look import QuickLookWindow
            self._quick_look = QuickLookWindow(self)
        self._quick_look.toggle_for(paths[0])

    def _sync_quick_look(self, path: str):
        """選択変更時、Quick Look が開いていれば内容を追従させる。"""
        if self._quick_look and self._quick_look.isVisible() and os.path.isfile(path):
            self._quick_look.show_for(path)

    def _navigate(self, path: str, add_to_history: bool = True):
        """到達可能性を非同期確認してから移動する（N-1: UIを止めない）。
        ショートカット/リンクは実体(ターゲット)へ解決し、ターゲットのドライブ最上位から
        全カラムで再表示する。"""
        path = os.path.normpath(path)
        try:
            real = os.path.realpath(path)
            if os.path.normcase(real) != os.path.normcase(path):
                path = real
        except OSError:
            pass
        self._pending_nav = (path, add_to_history)
        self.status_message.emit(f"確認中: {path}")
        self._prober.probe(path)

    def _on_probe_result(self, path: str, reachable: bool):
        if not self._pending_nav or self._pending_nav[0] != path:
            return  # 古いプローブ結果は無視（最後の要求のみ有効）
        _, add_to_history = self._pending_nav
        self._pending_nav = None
        if not reachable:
            self.status_message.emit(
                f"パスに到達できません（不存在または応答なし）: {path}")
            return
        self._navigate_now(path, add_to_history)

    def _set_current_path(self, path: str, add_to_history: bool = True):
        """ビューのルートを変えずに現在地の状態だけ更新する（カラム展開用）。"""
        self._current_path = path
        self._addr_bar.setText(path)
        self._sync_drive_combo(path)
        self.directory_changed.emit(path)
        self.status_message.emit(path)
        if add_to_history:
            self._history = self._history[:self._history_index + 1]
            self._history.append(path)
            self._history_index = len(self._history) - 1
            self._sm.add_to_history(path)
        try:
            self._thumb_mgr.prefetch(self._list_visible_files(path))
        except OSError:
            pass

    def _navigate_now(self, path: str, add_to_history: bool = True):
        self._current_path = path
        self._addr_bar.setText(path)
        self._sync_drive_combo(path)

        is_dir = os.path.isdir(path)
        target_dir = path if is_dir else str(Path(path).parent)

        # 経路上の隠しフォルダ(AppData等)を強制表示にしてカラムチェーンを構築可能にする
        self._apply_force_visible(path)

        # カラムのルート: パス途中(または自身)にリンクがあれば最上位リンク、無ければドライブ最上位
        col_root = self._column_root_for(path)
        self._column_view.setRootIndex(
            self._proxy.mapFromSource(self._fs_model.index(col_root)))
        self._column_view.setCurrentIndex(
            self._proxy.mapFromSource(self._fs_model.index(path)))
        # 深いパスは遅延ロードのため、directoryLoaded で再適用する
        self._pending_current = path

        # サムネ/リストビューは対象フォルダ単体を表示
        self._thumb_view.setRootIndex(
            self._proxy.mapFromSource(self._fs_model.index(target_dir)))

        if is_dir:
            self.directory_changed.emit(path)

        if add_to_history:
            # Prune forward history
            self._history = self._history[:self._history_index + 1]
            self._history.append(path)
            self._history_index = len(self._history) - 1
            self._sm.add_to_history(path)

        self.status_message.emit(path)  # 「確認中」表示を現在地に更新

        # Prefetch thumbnails for visible items（到達確認済みだが念のため保護）
        try:
            self._thumb_mgr.prefetch(self._list_visible_files(path))
        except OSError:
            pass

    def _column_go_up(self, folder_path=None):
        """◀: そのカラムを消して1つ上の階層へ（ルート不変のcurrentIndex移動）。"""
        base = folder_path or self._current_path
        if not base:
            return
        parent = str(Path(base).parent)
        if parent and os.path.normpath(parent) != os.path.normpath(base):
            self._select_in_columns(parent)

    def _column_root_for(self, path: str) -> str:
        """カラムビューのルートを決める。パス途中(または自身)に symlink/ジャンクションが
        あると、その配下はドライブ最上位ルートでは列挙できない環境があるため、
        最上位のリンク祖先をルートにする。リンクが無ければドライブ最上位。"""
        p = os.path.normpath(os.path.abspath(path))
        drive = os.path.splitdrive(p)[0]
        top = (drive + os.sep) if drive else os.sep
        comps = []
        cur = p
        while True:
            comps.append(cur)
            parent = os.path.dirname(cur)
            if not parent or parent == cur:
                break
            cur = parent
        comps.reverse()
        for comp in comps:
            try:
                if os.path.isdir(comp) and self._is_symlink_or_junction(comp):
                    return comp
            except OSError:
                pass
        return top

    def _select_in_columns(self, path: str):
        """ルートは _column_root_for で決め、currentIndex を path に移す。
        上位を選ぶと深いカラムが自動的に消える。"""
        if not path:
            return
        self._apply_force_visible(path)
        col_root = self._column_root_for(path)
        root_idx = self._column_view.rootIndex()
        cur_root = (self._fs_model.filePath(self._proxy.mapToSource(root_idx))
                    if root_idx.isValid() else "")
        if os.path.normcase(os.path.normpath(cur_root or "")) != \
                os.path.normcase(os.path.normpath(col_root)):
            self._column_view.setRootIndex(
                self._proxy.mapFromSource(self._fs_model.index(col_root)))
        src = self._fs_model.index(path)
        if src.isValid():
            self._column_view.setCurrentIndex(self._proxy.mapFromSource(src))
        self._pending_current = path
        self._current_path = path
        self._addr_bar.setText(path)
        self._sync_drive_combo(path)
        self.directory_changed.emit(path)
        self.status_message.emit(path)

    @staticmethod
    def _ancestors_of(path: str):
        """path 自身からドライブ最上位までの祖先パス一覧（normpath）。"""
        out = []
        cur = os.path.normpath(os.path.abspath(path))
        while True:
            out.append(cur)
            parent = os.path.dirname(cur)
            if not parent or parent == cur:
                break
            cur = parent
        return out

    def _apply_force_visible(self, path: str):
        """ナビ対象の経路上にある隠しフォルダ(AppData等)だけを強制表示にする。"""
        try:
            self._proxy.set_force_visible(self._ancestors_of(path))
        except Exception:
            pass

    def _on_fs_dir_loaded(self, loaded_path: str):
        """遅延ロード完了時、保留中の現在地がそのフォルダ配下なら
        setCurrentIndex を再適用してカラムチェーンを最後まで構築する。"""
        target = self._pending_current
        if not target:
            return
        try:
            t = os.path.normcase(os.path.normpath(target))
            ld = os.path.normcase(os.path.normpath(loaded_path))
        except Exception:
            return
        # ロードされたのが対象の祖先(または対象自身)のときのみ再適用
        if not (t == ld or t.startswith(ld + os.sep)):
            return
        src = self._fs_model.index(target)
        if src.isValid():
            self._column_view.setCurrentIndex(self._proxy.mapFromSource(src))
        # 対象フォルダ自体がロードされたら保留解除
        if t == ld:
            self._pending_current = None

    def _go_back(self):
        if self._history_index > 0:
            self._history_index -= 1
            self._navigate(self._history[self._history_index], add_to_history=False)

    def _go_forward(self):
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self._navigate(self._history[self._history_index], add_to_history=False)

    def _go_up(self):
        # 表示中の最下層(current_path)を1つ上へ。ルート不変で深いカラムが消える。
        parent = str(Path(self._current_path).parent)
        if parent and parent != self._current_path:
            self._select_in_columns(parent)

    def navigate_to(self, path: str):
        """Public API – called by bookmark/history panels."""
        self._navigate(path)

    # ------------------------------------------------------------------
    # Drive selector
    # ------------------------------------------------------------------

    def _refresh_drives(self):
        """ドライブ走査を別スレッドで実行（N-1: 死んだNASマッピングで固まらない）。"""
        invalidate_cache()
        self._drive_scanner.scan()

    def _on_drives_ready(self, results):
        current = self._drive_combo.currentData()
        self._drive_combo.blockSignals(True)
        self._drive_combo.clear()
        for root, ok in results:
            self._drive_combo.addItem(root if ok else f"{root} (応答なし)", root)
            if not ok:
                # 応答しないドライブは選択不可にして隔離
                model_item = self._drive_combo.model().item(
                    self._drive_combo.count() - 1)
                if model_item is not None:
                    model_item.setEnabled(False)
        if current:
            for i in range(self._drive_combo.count()):
                if self._drive_combo.itemData(i) == current:
                    self._drive_combo.setCurrentIndex(i)
                    break
        self._drive_combo.blockSignals(False)

    def _on_drive_changed(self, drive: str):
        root = self._drive_combo.currentData() or drive
        if root:
            self._navigate(root)  # 到達確認は _navigate 側で非同期に行う

    def _sync_drive_combo(self, path: str):
        """アドレスのドライブレターに合わせてドライブセレクタの選択を同期する。"""
        drive = os.path.splitdrive(path)[0]
        if not drive:
            return
        root = drive + os.sep
        combo = self._drive_combo
        combo.blockSignals(True)
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and os.path.normcase(os.path.normpath(str(data))) == \
                    os.path.normcase(os.path.normpath(root)):
                combo.setCurrentIndex(i)
                break
        combo.blockSignals(False)

    # ------------------------------------------------------------------
    # View mode / sort / filter
    # ------------------------------------------------------------------

    def _on_view_mode_changed(self, idx: int):
        self._column_view.setVisible(idx == 0)
        self._thumb_view.setVisible(idx in (1, 2))
        if idx == 1:  # List
            self._thumb_view.setViewMode(QListView.ListMode)
        elif idx == 2:  # Thumbnail
            self._thumb_view.setViewMode(QListView.IconMode)

    def _on_sort_changed(self, idx: int):
        col_map = {0: 0, 1: 2, 2: 3}  # name, type (kind), lastModified
        col = col_map.get(idx, 0)
        self._proxy.sort(col, Qt.AscendingOrder)
        self._sm.set("sort_by", ["name", "type", "timestamp"][idx])

    def _on_filter_changed(self, text: str):
        self._proxy.set_filter_string(text)
        self._sm.set("filter_string", text, save=False)

    # ------------------------------------------------------------------
    # Item interaction
    # ------------------------------------------------------------------

    def _resolve_path(self, proxy_index: QModelIndex) -> str:
        source_index = self._proxy.mapToSource(proxy_index)
        return self._fs_model.filePath(source_index)

    @staticmethod
    def _is_symlink_or_junction(path: str) -> bool:
        """
        「最終コンポーネント自身がリンクか」だけを判定する。
        symlink(mklink /D)とWindowsジャンクション(mklink /J)の両方に対応。

        注意: realpath比較で判定すると、祖先にリンクがある場合に配下の
        通常フォルダまで常にTrueになり、リンク配下へ入る度にビューが
        折りたたまれる。islink / readlink は最終要素のみを見るため安全。
        """
        try:
            if os.path.islink(path):      # symlink（最終要素のみ・ターゲット未接続でもTrue）
                return True
        except OSError:
            pass
        try:
            os.readlink(path)             # ジャンクションも検出。リンクでなければOSError
            return True
        except OSError:
            return False

    def _follow_link(self, path: str):
        """
        リンク(symlink/ジャンクション)を実体ドライブへ飛ばさず、
        リンクのパス(例: C:\\...\\MM-SA)のまま中身を表示する。
        通常フォルダのネイティブ列展開ではプロキシが子を返さないため、
        リンクは setRootIndex 方式の _navigate で開く（パスは維持される）。
        リンク先が未接続で到達不能な場合は _navigate 側がステータスに通知する。
        """
        self._navigate(path)

    def _on_item_clicked(self, proxy_index: QModelIndex):
        path = self._resolve_path(proxy_index)
        # リンク(symlink/ジャンクション)を最優先で処理。
        # リンク先が未接続ドライブ等だと os.path.isdir が False になり、
        # ファイル扱いで「開く」が誤発火するため isdir 判定より前に捌く。
        if self._is_symlink_or_junction(path):
            self._follow_link(path)
            return
        # Windows .lnk / .url ショートカット → 参照先をドライブ最上位から全カラム再表示
        if self._maybe_follow_shortcut(path):
            return
        if os.path.isdir(path):
            if self._column_view.isVisible():
                # 通常フォルダ: QColumnViewのネイティブ列展開に任せ、
                # ルートは変更しない（変更すると1カラム表示になり、
                # 内部カラム再構築との競合でクラッシュもする）
                self._set_current_path(path)
            else:
                self._navigate(path)
            return
        action = self._sm.get("single_click_action", "preview")
        self._dispatch_action(action, path)
        self._sync_quick_look(path)
        self.selection_changed.emit([path])

    def _on_item_activated(self, proxy_index: QModelIndex):
        path = self._resolve_path(proxy_index)
        if self._is_symlink_or_junction(path):
            self._follow_link(path)
            return
        # Windows .lnk / .url ショートカット → 参照先をドライブ最上位から全カラム再表示
        if self._maybe_follow_shortcut(path):
            return
        if os.path.isdir(path):
            if self._column_view.isVisible():
                self._set_current_path(path)  # カラム展開を維持（ルート不変）
            else:
                self._navigate(path)
            return
        # ダブルクリックは関連付けアプリ（OS既定）で開く
        open_with_default_app(path)

    def _maybe_follow_shortcut(self, path: str) -> bool:
        """Windowsショートカット(.lnk/.url)なら参照先を解決して移動する。
        解決先がフォルダ/ファイルどちらでも _navigate がドライブ最上位から
        全カラムで再表示する（ファイルは親カラム上で選択表示）。"""
        low = (path or "").lower()
        if not (low.endswith(".lnk") or low.endswith(".url")):
            return False
        target = resolve_windows_shortcut(path)
        if target and os.path.exists(target):
            self.status_message.emit(f"ショートカット解決: {path} → {target}")
            self._navigate(target)
            return True
        if target:
            self.status_message.emit(f"ショートカット参照先が見つかりません: {target}")
        return False

    def _dispatch_action(self, action: str, path: str):
        if action == "open" and self._on_open:
            self._on_open(path)
        elif action == "import" and self._on_import:
            self._on_import(path)
        elif action == "reference" and self._on_reference:
            self._on_reference(path)
        else:
            self.file_activated.emit(path)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _get_selected_paths(self) -> List[str]:
        active_view = self._column_view if self._column_view.isVisible() else self._thumb_view
        return [self._resolve_path(idx) for idx in active_view.selectedIndexes()
                if idx.column() == 0]

    def _show_context_menu(self, pos: QPoint):
        paths = self._get_selected_paths()
        if not paths:
            return

        menu = QMenu(self)
        is_maya = all(Path(p).suffix.lower() in MAYA_EXTENSIONS for p in paths)
        is_single = len(paths) == 1
        is_dir = is_single and os.path.isdir(paths[0])

        # ── Maya actions ──────────────────────────────────────────────
        if is_maya and is_single:
            open_act = menu.addAction("🗂  Maya で開く")
            open_act.triggered.connect(lambda: self._dispatch_action("open", paths[0]))
            import_act = menu.addAction("⬇  Maya にインポート")
            import_act.triggered.connect(lambda: self._dispatch_action("import", paths[0]))
            ref_act = menu.addAction("🔗  Maya にリファレンス")
            ref_act.triggered.connect(lambda: self._dispatch_action("reference", paths[0]))
            menu.addSeparator()

        # ── General actions ───────────────────────────────────────────
        if is_single:
            open_ext_act = menu.addAction("🖥  関連付けアプリで開く")
            open_ext_act.triggered.connect(lambda: open_with_default_app(paths[0]))

            reveal_act = menu.addAction("📁  エクスプローラーで表示")
            reveal_act.triggered.connect(lambda: reveal_in_explorer(paths[0]))
            menu.addSeparator()

        # ── パスをコピー（単数/複数対応） ─────────────────────────────
        copy_path_act = menu.addAction("📋  ファイルパスをコピー")
        copy_path_act.triggered.connect(lambda: self._copy_paths_to_clipboard(paths))
        menu.addSeparator()

        # ── Bookmark ─────────────────────────────────────────────────
        bm_act = menu.addAction("⭐  ブックマークに追加")
        bm_act.triggered.connect(lambda: self._add_to_bookmarks(paths))
        menu.addSeparator()

        # ── File ops ─────────────────────────────────────────────────
        copy_act = menu.addAction("📋  コピー...")
        copy_act.triggered.connect(lambda: self._copy_dialog(paths))

        move_act = menu.addAction("✂  移動...")
        move_act.triggered.connect(lambda: self._move_dialog(paths))

        rename_act = menu.addAction("✏  名前変更...")
        rename_act.triggered.connect(lambda: self._rename_dialog(paths))
        rename_act.setEnabled(is_single)

        menu.addSeparator()

        del_act = menu.addAction("🗑  削除")
        del_act.triggered.connect(lambda: self._delete_confirm(paths))
        del_act.setShortcut("Delete")

        menu.addSeparator()

        # ── Properties ───────────────────────────────────────────────
        if is_single:
            prop_act = menu.addAction("ℹ  プロパティ")
            prop_act.triggered.connect(lambda: self._show_properties(paths[0]))

        menu.exec_(self.sender().viewport().mapToGlobal(pos)
                   if hasattr(self.sender(), "viewport")
                   else self.mapToGlobal(pos))

    # ------------------------------------------------------------------
    # File operations (UI wrappers)
    # ------------------------------------------------------------------

    def _add_to_bookmarks(self, paths: List[str]):
        # 実際の登録は MainWindow 側の BookmarkManager で行う（signalで依頼）
        if paths:
            self.bookmark_requested.emit(list(paths))
            self.status_message.emit(f"ブックマークに追加: {len(paths)} 件")

    def _copy_paths_to_clipboard(self, paths: List[str]):
        """選択中のフルパスをクリップボードへコピー（複数は改行区切り）。"""
        from core.compat import QApplication
        if not paths:
            return
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText("\n".join(paths))
        self.status_message.emit(f"パスをコピー: {len(paths)} 件")

    def _copy_dialog(self, paths: List[str]):
        dst = QFileDialog.getExistingDirectory(self, "コピー先を選択")
        if dst:
            try:
                results = copy_items(paths, dst)
                self.status_message.emit(f"コピー完了: {len(results)} 件")
            except FileOperationError as e:
                QMessageBox.critical(self, "エラー", str(e))

    def _move_dialog(self, paths: List[str]):
        dst = QFileDialog.getExistingDirectory(self, "移動先を選択")
        if dst:
            try:
                results = move_items(paths, dst)
                self.status_message.emit(f"移動完了: {len(results)} 件")
            except FileOperationError as e:
                QMessageBox.critical(self, "エラー", str(e))

    def _rename_dialog(self, paths: List[str]):
        if len(paths) != 1:
            return
        old = Path(paths[0])
        new_name, ok = QInputDialog.getText(
            self, "名前変更", "新しい名前:", text=old.name
        )
        if ok and new_name:
            new_path = old.parent / new_name
            try:
                old.rename(new_path)
                self.status_message.emit(f"名前変更: {old.name} → {new_name}")
            except Exception as e:
                QMessageBox.critical(self, "エラー", str(e))

    def _delete_confirm(self, paths: List[str]):
        msg = f"{len(paths)} 件を削除しますか？"
        ret = QMessageBox.warning(self, "削除の確認", msg,
                                  QMessageBox.Yes | QMessageBox.Cancel)
        if ret == QMessageBox.Yes:
            failed = delete_items(paths)
            if failed:
                QMessageBox.warning(self, "削除エラー",
                                    f"{len(failed)} 件の削除に失敗しました:\n" +
                                    "\n".join(failed))
            else:
                self.status_message.emit(f"{len(paths)} 件を削除しました")

    def _show_properties(self, path: str):
        info = Path(path)
        stat = info.stat()
        import datetime
        msg = (
            f"名前: {info.name}\n"
            f"パス: {path}\n"
            f"サイズ: {format_size(stat.st_size)}\n"
            f"更新日時: {datetime.datetime.fromtimestamp(stat.st_mtime)}\n"
            f"種類: {get_file_type_category(path)}"
        )
        QMessageBox.information(self, "プロパティ", msg)

    # ------------------------------------------------------------------
    # Drag & Drop / Clipboard （Explorer 互換）
    # ------------------------------------------------------------------

    @staticmethod
    def _configure_dnd(view):
        """ビューに Explorer 互換のドラッグ&ドロップ設定を適用する。"""
        view.setDragEnabled(True)             # アプリ → Explorer 等へドラッグ可
        view.setAcceptDrops(True)             # Explorer 等 → アプリへドロップ可
        view.setDropIndicatorShown(True)
        view.setDragDropMode(QAbstractItemView.DragDrop)
        view.setDefaultDropAction(Qt.MoveAction)   # 既定は移動（Ctrlでコピー）
        view.setDragDropOverwriteMode(False)
        view.setEditTriggers(QAbstractItemView.NoEditTriggers)  # 誤リネーム防止

    # ---- クリップボード (Ctrl+C / Ctrl+X / Ctrl+V / Delete) ----

    # Windows の "Preferred DropEffect" 値（CF_PREFERREDDROPEFFECT）
    _DROPEFFECT_COPY = 5   # コピー（Explorerが受理する慣用値）
    _DROPEFFECT_MOVE = 2   # 移動（切り取り）

    def _install_clipboard_actions(self):
        """子ビューにフォーカスがあっても効くショートカットを登録する。"""
        for seq, slot in [
            (QKeySequence.Copy,  self._clipboard_copy),
            (QKeySequence.Cut,   self._clipboard_cut),
            (QKeySequence.Paste, self._clipboard_paste),
            (QKeySequence.Delete, self._clipboard_delete),
        ]:
            act = QAction(self)
            act.setShortcut(seq)
            act.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            act.triggered.connect(slot)
            self.addAction(act)

    def _set_clipboard(self, paths: List[str], move: bool):
        """選択ファイルをクリップボードへ。Explorer と相互にペースト可能な形式で格納。"""
        paths = [p for p in paths if p and os.path.exists(p)]
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        effect = self._DROPEFFECT_MOVE if move else self._DROPEFFECT_COPY
        mime.setData("Preferred DropEffect",
                     _QtCore.QByteArray(struct.pack("<I", effect)))
        QApplication.clipboard().setMimeData(mime)
        self.status_message.emit(
            ("切り取り" if move else "コピー") + f": {len(paths)} 件")

    def _clipboard_copy(self):
        self._set_clipboard(self._get_selected_paths(), move=False)

    def _clipboard_cut(self):
        self._set_clipboard(self._get_selected_paths(), move=True)

    def _paste_target_dir(self) -> Optional[str]:
        """貼り付け先: 単一フォルダ選択中ならそのフォルダ、無ければ現在地。"""
        sel = self._get_selected_paths()
        if len(sel) == 1 and os.path.isdir(sel[0]):
            return sel[0]
        return self._current_path if os.path.isdir(self._current_path) else None

    def _clipboard_paste(self):
        mime = QApplication.clipboard().mimeData()
        if not mime or not mime.hasUrls():
            return
        paths = [u.toLocalFile() for u in mime.urls() if u.toLocalFile()]
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return

        # 移動/コピー判定（Explorer の Preferred DropEffect を尊重）
        move = False
        if mime.hasFormat("Preferred DropEffect"):
            data = bytes(mime.data("Preferred DropEffect"))
            if len(data) >= 4:
                effect = struct.unpack("<I", data[:4])[0]
                move = bool(effect & self._DROPEFFECT_MOVE)

        target = self._paste_target_dir()
        if not target:
            QMessageBox.warning(self, "貼り付け", "貼り付け先フォルダを特定できません。")
            return

        # 同一フォルダへの移動は無意味なのでコピーへ降格
        if move and all(os.path.normpath(os.path.dirname(p)) ==
                        os.path.normpath(target) for p in paths):
            move = False

        try:
            if move:
                results = move_items(paths, target)
                QApplication.clipboard().clear()  # 切り取りは1回限り
            else:
                results = copy_items(paths, target)
            self.status_message.emit(
                ("移動" if move else "貼り付け") + f"完了: {len(results)} 件")
        except FileOperationError as e:
            QMessageBox.critical(self, "エラー", str(e))

    def _clipboard_delete(self):
        paths = self._get_selected_paths()
        if paths:
            self._delete_confirm(paths)

    # ------------------------------------------------------------------
    # Thumbnail refresh
    # ------------------------------------------------------------------

    def _on_thumbnail_ready(self, path: str, pixmap: "QPixmap"):
        """Force repaint when a thumbnail arrives."""
        if self._thumb_view.isVisible():
            self._thumb_view.viewport().update()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _list_visible_files(self, directory: str) -> List[str]:
        try:
            return [
                os.path.join(directory, f)
                for f in os.listdir(directory)
                if os.path.isfile(os.path.join(directory, f))
            ][:64]  # Prefetch at most 64 files
        except OSError:
            return []

    # ------------------------------------------------------------------
    # Public setters (called by main window)
    # ------------------------------------------------------------------

    def set_open_callback(self, cb: Callable[[str], None]):
        self._on_open = cb

    def set_import_callback(self, cb: Callable[[str], None]):
        self._on_import = cb

    def set_reference_callback(self, cb: Callable[[str], None]):
        self._on_reference = cb

    def set_max_depth(self, depth: int):
        self._max_depth = depth
        self._column_view.set_max_depth(depth)
        self._sm.set("column_max_depth", depth)

    def set_thumb_size(self, size: int):
        self._thumb_delegate._thumb_size = size
        self._thumb_view.setGridSize(QSize(size + 16, size + 32))
        self._thumb_view.setItemDelegate(self._thumb_delegate)
        self._thumb_mgr.set_thumb_size(size)
        self._sm.set("thumbnail_size", size)

    def current_path(self) -> str:
        return self._current_path
