# -*- coding: utf-8 -*-
"""Browser Panel
=============
ファイルブラウザ本体（BrowserPanel）。

r110 でモジュールを分割した。旧 browser_panel.* の名前は互換のため
ここで再エクスポートしているので、既存の import は変更不要。"""

import os
import struct
import threading
from pathlib import Path
from typing import List, Optional, Callable

from core.compat import (
    Qt, Signal, QObject,
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QLineEdit, QToolButton,
    QSplitter, QColumnView, QListView,
    QSizePolicy, QFrame, QAbstractItemView, QSlider,
    QFileSystemModel, QSortFilterProxyModel,
    QMenu, QAction, QMessageBox, QFileDialog, QInputDialog, QDialog,
    QStyledItemDelegate, QStyle, QModelIndex, QSize, QRect, QPixmap, QPainter, QColor, QDir, QFileInfo, QUrl, QMimeData, QPoint,
    QFontMetrics, QTimer, QKeySequence, QDrag, QCursor,
)
from core.compat import QtCore as _QtCore
try:  # PySide6: QtGui / PySide2: QtWidgets
    from core.compat import QIcon
except ImportError:  # pragma: no cover
    QIcon = None
try:
    from PySide6.QtGui import QFileIconProvider
except ImportError:
    try:
        from PySide6.QtWidgets import QFileIconProvider
    except ImportError:
        from PySide2.QtWidgets import QFileIconProvider
from core.path_guard import PathProber, DriveScanner, invalidate_cache
from core.file_operations import (
    open_with_default_app, reveal_in_explorer,
    copy_items, move_items, get_file_type_category, format_size,
    resolve_windows_shortcut,
    MAYA_EXTENSIONS
)
from core.thumbnail_generator import ThumbnailManager

from ui.browser_util import (  # noqa: F401  （再エクスポート）
    _cursor_over_maya_window, _cursor_over_dcc_window, _time_mod, _MFM_T0,
    _MFM_STARTUP_LOG, _MFM_FREEZE_LOG, _re_mod, _DRIVE_IN_LABEL_RE,
    _safe_file_path, _SafeIconProvider, _mfm_slow_note, _MFM_UI_LOG,
    _MFM_UILOG_ON, _mfm_warn, _mfm_uilog, _MFM_BLOCKING,
    mfm_blocking_begin, mfm_blocking_end, mfm_blocking_reason,
    _mfm_timeline, _MFM_LOG_PATH, _MFM_DEBUG, _mfm_log,
)
from ui.browser_delegates import (  # noqa: F401  （再エクスポート）
    _paint_expanded_mark, ThumbnailDelegate, _BADGE_STYLE, _P4_BADGE_PNG,
    _BADGE_ICON_DIR, _BADGE_PIXMAP_CACHE, _badge_pixmap, _badge_kind,
    _FileOpNotifier, _INTEG_RESULT_CONNECTED,
    _on_integration_action_finished, _badge_tooltip, StatusBadgeDelegate,
)
from ui.browser_models import (  # noqa: F401  （再エクスポート）
    FileFilterProxyModel,
)
from ui.browser_column_view import (  # noqa: F401  （再エクスポート）
    CappedColumnView, _DccAwareMime, _ColumnSizePopup,
    _ColumnResizeHandle, _SizeButtonHover,
)



from ui.browser_models import FileFilterProxyModel  # noqa: F401


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
    # r101: 右クリック → バッチリネーム（起動時の選択をそのまま対象にする）
    batch_rename_requested = Signal(list)
    # r107: 現在地の消失判定（ワーカー → UI スレッド）(調べたパス, 移動先)
    _gone_result = Signal(str, str)

    def __init__(self, settings_manager, thumb_manager: ThumbnailManager, parent=None):
        super().__init__(parent)
        _mfm_log("=== BrowserPanel init (build: r110 split 2026-10-01) ===")
        _mfm_timeline("BrowserPanel init (r110)")
        self._sm = settings_manager
        self._thumb_mgr = thumb_manager
        self._thumb_mgr.thumbnail_ready.connect(self._on_thumbnail_ready)

        self._current_path = str(Path.home())
        # 前回パス復元モードが有効なら前回終了時のパスから開始
        if self._sm.get("restore_last_path", True):   # 既定ON（編集メニューで切替）
            _last = self._sm.get("last_path", "")
            if _last:
                self._current_path = _last
        # フルパス保持（深度キャップ廃止）。保存設定に関係なく無制限。
        self._max_depth = 0
        self._click_action = self._sm.get("single_click_action", "open")
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
        # 視認性の階層（r56）: ブラウジング面は «一段明るい面＋強いヘアライン枠»
        # （theme_engine の #mfmBrowserPanel）。枠を描くため 4px の内側余白を取る。
        self.setObjectName("mfmBrowserPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        # ── Toolbar ──────────────────────────────────────────────────
        toolbar = QFrame()
        toolbar.setObjectName("mfmBrowserToolbar")
        toolbar.setFrameShape(QFrame.NoFrame)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(4, 4, 4, 4)
        tb_layout.setSpacing(4)

        # Up / back / forward
        # 「◀▶▲」だとカラム移動と誤読される、との指摘によりモチーフ変更:
        #   上の階層 = 左矢印(←) / 戻る = Undo(↶) / 進む = Redo(↷)
        # 並び順も [上へ][戻る][進む] に変更（ドライブセレクタはパス欄の左隣へ）。
        # Mercury: ピルボタン＋ヘアライン境界（ホバーは面をわずかに明るく）
        from core.theme_engine import qss_vars
        _nav_style = (
            "QToolButton{"
            "  background:%(fill_subtle)s; color:%(on_surface)s;"
            "  border:1px solid %(hairline)s; border-radius:15px;"
            "  font-size:19px; font-weight:%(w_strong)s; padding:0px 0px 3px 0px;"
            "}"
            "QToolButton:hover{ background:%(fill_subtle_hover)s;"
            "  border-color:%(hairline_strong)s; }"
            "QToolButton:pressed{ background:%(fill_subtle_pressed)s; }"
            "QToolButton:disabled{ color:%(on_surface_dim)s; background:%(overlay_fill)s;"
            "  border-color:%(hairline)s; }"
            % qss_vars()
        )
        from core.i18n import tr as _tr
        for icon_text, tip, slot in [
            ("←", _tr("上の階層へ", "Up one level"), self._go_up),
            ("↶", _tr("前のディレクトリへ戻る", "Back"), self._go_back),
            ("↷", _tr("先のディレクトリへ進む", "Forward"), self._go_forward),
        ]:
            btn = QToolButton()
            btn.setText(icon_text)
            btn.setToolTip(tip)
            btn.setFixedSize(36, 30)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_nav_style)
            btn.clicked.connect(slot)
            tb_layout.addWidget(btn)

        # Drive selector（パス欄の左隣）
        self._drive_combo = QComboBox()
        self._drive_combo.setFixedWidth(80)
        self._drive_combo.currentTextChanged.connect(self._on_drive_changed)
        tb_layout.addWidget(self._drive_combo)
        self._refresh_drives()

        # Address bar
        self._addr_bar = QLineEdit()
        self._addr_bar.setPlaceholderText("パスを入力...")
        self._addr_bar.returnPressed.connect(lambda: self._navigate(self._addr_bar.text()))
        tb_layout.addWidget(self._addr_bar)

        # 旧グローバルの「フィルター入力／カラム(ビューモード)／名前(ソート)」は
        # カラム別フィルタ・ソート・表示切替へ移行したため撤去。

        # ツールバー(アドレスバー行)は本来の高さに固定する。
        # これを怠ると縦方向にも伸びてビューの空間を奪う（上部に巨大な空白が出る）。
        toolbar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(toolbar, 0)

        # ── Main view stack ───────────────────────────────────────────
        self._view_stack = QSplitter(Qt.Horizontal)
        self._view_stack.setChildrenCollapsible(False)  # 平坦カラムが幅0に潰れないように
        self._view_stack.setHandleWidth(1)

        # Shared filesystem model
        self._fs_model = QFileSystemModel()
        # r77: ファイルアイコンは «拡張子ごと» にレジストリ既定のアイコンだけを
        # 使う（_SafeIconProvider）。Qt 標準はファイル毎に SHGetFileInfo（シェル）を
        # 描画時に遅延実行するため、P4EXP / TortoiseSVN 等のシェル拡張がこの
        # プロセス内で走り、mayapy で「描画中に 1.5 秒停止 → access violation」
        # で起動直後に落ちた（2026-09-16、mfm_freeze.log: data() 内で AV）。
        # r64 のネイティブダイアログ禁止と同じ理由。exe/lnk/url は個別の
        # アイコン抽出・リンク解決を伴うため汎用アイコンにする。
        try:
            self._fs_model.setIconProvider(_SafeIconProvider())
        except Exception as e:
            _mfm_log("icon provider install failed: %r" % (e,))
        # 注意: ここで setRootPath("") を呼んではならない。
        # "" は «全ドライブレターの情報収集» を QFileSystemModel の単一の
        # 収集スレッドへ即座に投入する。切断済み/不調のドライブマッピングが
        # 1つでもあると収集スレッドが数十秒ブロックし、ローカルフォルダの
        # directoryLoaded までもがその後ろに並んで「起動後1分ブラウズ不能」になる
        # （2026-09 実機で発生）。rootPath はナビゲーション時に対象ディレクトリへ
        # 設定する（_navigate_now 内の setRootPath(target_dir)）。
        # QDir.Hidden を含めることで、AppData 等の隠しフォルダもモデルに載せる。
        # 表示の可否は proxy 側で制御（既定は非表示、ナビ経路の祖先のみ強制表示）。
        self._fs_model.setFilter(
            QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Files | QDir.Hidden
        )
        # 読み取り専用を解除 → Qt標準のファイルD&D（Explorerとの相互コピー/移動）を有効化。
        # これにより各アイテムに Drag/Drop フラグが付与される。
        self._fs_model.setReadOnly(False)
        # F2 のインライン名前変更（EditRole → QFileSystemModel::setData）の反映
        try:
            self._fs_model.fileRenamed.connect(self._on_fs_file_renamed)
        except Exception:
            pass
        # フォルダ列挙の高速化: desktop.ini によるカスタムフォルダアイコンの
        # シェル照会を無効化（フォルダ毎のシェルI/Oが減り、大きなツリーや
        # ネットワーク先で初回列挙が大幅に速くなる。表示は標準アイコンになる）
        try:
            self._fs_model.setOption(
                QFileSystemModel.DontUseCustomDirectoryIcons, True)
        except Exception:
            pass
        # 【最重要】リンク解決の無効化。Qt の filePath()/列挙は、行が
        # symlink/ジャンクションだと canonicalFilePath でリンク先を «実際に»
        # 解決する。リンク先が到達不能なネットワークだと SMBタイムアウト
        # （約21秒）がUIスレッド/列挙スレッドで発生し、ナビ毎に21秒フリーズ・
        # 起動63秒停滞の直接原因になった（mfm_freeze.log で特定。2026-09）。
        # 本ツールは設計上リンクを実体へ解決しない（リンクのパスのまま扱う）
        # ため、この解決処理自体が不要。
        try:
            self._fs_model.setResolveSymlinks(False)
        except Exception:
            pass
        # 深いパス(遅延ロード)のカラム構築を確実にするためのリトライ
        self._fs_model.directoryLoaded.connect(self._on_fs_dir_loaded)

        # Proxy model
        self._proxy = FileFilterProxyModel()
        self._proxy.setSourceModel(self._fs_model)
        self._proxy.setSortRole(Qt.DisplayRole)
        # カラム別ソート(lessThan)を有効化（フォルダ優先＋name昇順が既定）
        self._proxy.sort(0, Qt.AscendingOrder)

        # Column view
        self._column_view = CappedColumnView(self._max_depth)
        self._column_view.set_go_up_callback(self._column_go_up)
        # ゆっくり2回クリック → インライン名前変更（r67）
        self._column_view._rename_cb = lambda p: self._rename_inline(p)
        self._column_view.set_thumb_mgr(self._thumb_mgr)
        self._column_view.set_merge_callback(self._on_flat_request)
        # Qt標準のリサイズグリップは「両スクロールバー表示時の角」にしか現れず
        # 実用不可のため無効化し、自前のハンドル（_ColumnResizeHandle）を使う。
        try:
            self._column_view.setResizeGripsVisible(False)
        except Exception:
            pass
        # 保存済みのカラム幅を適用（未保存なら既定幅300）
        self._column_view.set_settings(self._sm)
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
        # 【重要】ビューのルートを «マイコンピュータ（全ドライブ）» のままに
        # しない。rootIndex が無効のまま表示されると Qt が全ドライブを列挙し、
        # 切断済みのドライブマッピング（W:, X: 等）ごとに列挙スレッドが約21秒
        # ブロック → その後ろに並ぶ通常フォルダの読み込みまで遅れ「起動時に
        # 数十秒Loading」の元凶になった（2026-09）。ローカルのホームを仮の
        # ルートにしておき、直後のパス復元/ナビゲーションで置き換える。
        try:
            _home_idx = self._proxy.mapFromSource(
                self._fs_model.index(os.path.expanduser("~")))
            if _home_idx.isValid():
                self._column_view.setRootIndex(_home_idx)
                self._thumb_view.setRootIndex(_home_idx)
        except Exception:
            pass
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

        # インライン平坦カラム（複数選択時に自動／単一は平坦トグル時に、
        # 通常のカラムビューの右隣に «次のカラム» として表示。ビューは切り替えない）
        from ui.flat_column import FlatColumn
        self._flat_col = FlatColumn(self)
        self._flat_col.set_recursive(bool(self._sm.get("flat_recursive", True)))
        self._flat_col.file_activated.connect(open_with_default_app)
        self._flat_col.file_selected.connect(self._sync_quick_look)
        # 平坦ビューで単一ファイル選択時もパス欄にファイル名まで表示
        self._flat_col.file_selected.connect(
            lambda p: self._addr_bar.setText(p) if p else None)
        self._flat_col.closed.connect(self._flat_col.hide)
        # r74: 平坦ビューからの D&D も落下先が DCC なら通常カラムと同じ通知経路へ
        # r90: mime も同じもの（DCC への落下は Manager が引き受ける）を使う
        self._flat_col._view._mime_factory = self._column_view.make_drag_mime
        self._flat_col.drag_finished.connect(
            lambda paths: self._column_view._notify_drag_finished(
                list(paths), getattr(self._flat_col._view, "last_mime", None)))
        # 平坦ビューにも通常カラムと同じ右クリックメニューを付ける
        self._flat_col._view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._flat_col._view.customContextMenuRequested.connect(
            self._show_flat_context_menu)
        self._view_stack.addWidget(self._flat_col)
        self._flat_col.hide()
        # カラムビューが主、平坦カラムは右に従。初期サイズ配分。
        self._view_stack.setStretchFactor(0, 1)
        self._column_view.set_flat_callback(self._on_flat_request)
        self._column_view.set_thumb_prefetch_callback(
            lambda folder: self._prefetch_thumbs_async(folder, force=True))
        self._column_view.set_file_drop_callback(self._on_files_dropped)
        # r102: 表示サイズのスライダーは «全体» に効かせる（カラム以外も）
        self._column_view.set_item_size_callback(self._apply_item_size_to_views)

        # 共通フォルダのドリルダウン・カラム群（複数選択時に平坦カラムの左へ挿入）
        self._common_cols = []
        self._common_srcs = []

        # r97: 圧縮ファイルの中身カラム（zip/tar）。面の色を琥珀寄りに振って
        # «通常のフォルダではない» ことを一目で分かるようにしている。
        from ui.archive_column import ArchiveColumn
        self._archive_col = ArchiveColumn(self)
        self._archive_col.closed.connect(lambda: self._on_archive_request(""))
        self._archive_col.status_message.connect(self.status_message.emit)
        self._archive_col.file_activated.connect(open_with_default_app)
        self._view_stack.addWidget(self._archive_col)
        self._archive_col.hide()

        # 平坦カラムの右に置くスペーサー。平坦カラムを«次のカラム»として
        # カラム幅で見せ、残りは通常の空き背景にする（巨大パネル化を防ぐ）
        self._flat_spacer = QWidget()
        self._flat_spacer.setObjectName("mfmFlatSpacer")
        self._view_stack.addWidget(self._flat_spacer)
        self._flat_spacer.hide()

        # 旧マージ専用パネル（互換のため保持。通常は不使用）
        from ui.merge_view import MergePanel
        self._merge_panel = MergePanel(self)
        # インライン表示なので、閉じる時はマージビューを隠すだけ（メインは常に表示）
        self._merge_panel.closed.connect(self._merge_panel.hide)
        self._merge_panel.file_selected.connect(self._sync_quick_look)
        self._view_stack.addWidget(self._merge_panel)
        self._merge_panel.hide()

        # stretch=1 で残りの縦空間をすべてビューに割り当てる
        layout.addWidget(self._view_stack, 1)

        # ── History navigation ────────────────────────────────────────
        # r98: 表示中のフォルダが（外部操作も含め）消えたら、実在する一番近い
        # 親へ自動で移動する。ネットワーク先を叩かないよう間隔は長めに取る。
        self._gone_watch = QTimer(self)
        self._gone_watch.setInterval(8000)
        # r107: **UI スレッドで os.path.isdir を呼ばない**。ネットワーク/
        # Perforce のワークスペースでは、同期中や応答待ちの時に stat が
        # 10 秒以上返らず、そのまま UI が固まる（mfm_freeze.log で確定。
        # 「p4 で最新取得中にフリーズする」の正体）。判定はワーカーで行う。
        self._gone_watch.timeout.connect(self._check_current_gone_async)
        self._gone_watch.start()
        self._gone_probe_busy = False
        self._gone_result.connect(self._on_gone_result)

        self._history: List[str] = []
        self._history_index: int = -1

        # サムネ先読みのファイル列挙結果をワーカー→UIへ運ぶ通知
        class _PrefetchNotifier(_QtCore.QObject):
            files_ready = Signal(list)
        self._prefetch_notifier = _PrefetchNotifier(self)
        self._prefetch_notifier.files_ready.connect(
            lambda files: self._thumb_mgr.prefetch(files))

        # ファイルのD&Dはビュー(モデル)側で一括処理するため、パネル自身は
        # ドロップを受け取らない（横取りして「移動だけ」になるのを防ぐ）。
        self.setAcceptDrops(False)

        # N-2: Space Quick Look 用イベントフィルタ
        self._column_view.installEventFilter(self)
        self._thumb_view.installEventFilter(self)

        # Ctrl+C / Ctrl+X / Ctrl+V / Delete（子ビューにフォーカスがあっても発火）
        self._install_clipboard_actions()

        # 外部サービス連携（Git/SVN/Perforce/クラウド）。検出はワーカーで1回。
        # 設定 integrations_enabled=False なら完全に無効（コードパスに入らない）。
        try:
            from core.integrations import get_manager
            _mgr = get_manager()
            _mgr.start(bool(self._sm.get("integrations_enabled", True)))
            # 操作結果の通知は «プロセスで1回だけ» 接続（エリアが複数あっても
            # ダイアログを重複表示しない。受け手はモジュール関数）
            global _INTEG_RESULT_CONNECTED
            if not _INTEG_RESULT_CONNECTED:
                _mgr.action_finished.connect(_on_integration_action_finished)
                _INTEG_RESULT_CONNECTED = True
        except Exception as _e:
            _mfm_log("integrations start error: %r" % (_e,))

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
            # 選択追従ではクラウドの実体をダウンロードさせない（r83）
            self._quick_look.show_for(path, allow_download=False)

    def _navigate(self, path: str, add_to_history: bool = True):
        """到達可能性を非同期確認してから移動する（N-1: UIを止めない）。

        重要: ここでは os.path.realpath による実体解決を行わない。
        - シンボリックリンク/ジャンクション: リンクのパスを維持して中身を表示する
          （実体側ドライブへ飛ばさない）。クリックは _follow_link 経由でこの関数に来る。
        - .lnk/.url ショートカット: 呼び出し元(_maybe_follow_shortcut)が既に
          参照先(実体パス)へ解決済みのため、ここで再解決する必要はない。
        以前ここに realpath を入れていたためシンボリックリンクが実体へ遷移していた（回帰）。"""
        path = os.path.normpath(path)
        # ナビゲーションは標準挙動どおり複数選択をリセットする
        # （ブックマーク・履歴・▲・ショートカット追従すべて共通）
        try:
            self._column_view._clear_multi_state()
            if self._flat_col.isVisible():
                self._on_flat_request([])
            if self._archive_col.isVisible():
                self._on_archive_request("")        # r97
        except Exception:
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
        self._prefetch_thumbs_async(path)

    def _navigate_now(self, path: str, add_to_history: bool = True):
        _mfm_timeline("navigate_now: %r" % path)
        # r83: 新しい移動なので «選択維持ガード» を解除し、自己修復を許可する
        try:
            self._column_view._mfm_user_selected = False
        except Exception:
            pass
        # 各段階の所要時間を計測（0.5秒超は mfm_freeze.log に SLOW 記録）。
        # PySideのC++呼び出しはGILを離さないためサンプラでは特定できない。
        _pc = _time_mod.perf_counter

        def _timed(label, fn):
            t0 = _pc()
            try:
                return fn()
            finally:
                dt = _pc() - t0
                if dt > 0.5:
                    _mfm_slow_note("navigate_now %s=%.2fs path=%r"
                                   % (label, dt, path))

        self._current_path = path
        self._addr_bar.setText(path)
        _timed("sync_drive_combo", lambda: self._sync_drive_combo(path))

        is_dir = _timed("isdir", lambda: os.path.isdir(path))
        target_dir = path if is_dir else str(Path(path).parent)

        # 経路上の隠しフォルダ(AppData等)を強制表示にしてカラムチェーンを構築可能にする
        _timed("apply_force_visible", lambda: self._apply_force_visible(path))

        # QFileSystemModel に対象ディレクトリまでのチェーンを能動的にロードさせる。
        # これが無いと、別ドライブ/深い/隠し経由のパスは index が無効のままで
        # カラムが構築されない（クリックしてもカラムが伸びない原因）。
        try:
            _timed("setRootPath", lambda: self._fs_model.setRootPath(target_dir))
        except Exception:
            pass

        # カラムのルート: パス途中(または自身)にリンクがあれば最上位リンク、無ければドライブ最上位
        col_root = _timed("column_root_for", lambda: self._column_root_for(path))
        _root_src = _timed("index(col_root)", lambda: self._fs_model.index(col_root))
        _timed("setRootIndex", lambda: self._column_view.setRootIndex(
            self._proxy.mapFromSource(_root_src)))
        _init_src = _timed("index(path)", lambda: self._fs_model.index(path))
        _init_idx = self._proxy.mapFromSource(_init_src)
        _timed("setCurrentIndex", lambda: self._column_view.setCurrentIndex(_init_idx))
        # 深い/別ドライブ/隠し経由のパスは遅延ロードのため、
        # 最上位から1段ずつ読み込みを起動してカラムを伸ばす
        self._pending_current = path
        _timed("prime_path_loading", lambda: self._prime_path_loading(path))
        try:
            _src = self._fs_model.index(path)
            _rc = self._fs_model.rowCount(self._fs_model.index(target_dir))
            _mfm_log("navigate_now: path=%r col_root=%r src_valid=%s target_dir_rowcount=%d"
                     % (path, col_root, _src.isValid(), _rc))
        except Exception as _e:
            _mfm_log("navigate_now: log error %r" % _e)

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

        # Prefetch thumbnails for visible items（リストアップはワーカーで行う）
        self._prefetch_thumbs_async(path)

    def _any_thumb_column(self) -> bool:
        """サムネイル表示になっているカラムがあるか（既定はリスト表示）。"""
        try:
            for v in self._column_view.findChildren(QListView):
                if getattr(v, "_mfm_view_mode", "list") == "thumb":
                    return True
        except Exception:
            pass
        return False

    def _prefetch_thumbs_async(self, path: str, force: bool = False):
        """サムネ先読み用のファイル列挙をワーカースレッドで行い、結果だけを
        UIスレッドへ戻す。os.listdir/isfile はネットワーク先ではブロックする
        ため、UIスレッドで直接呼んではならない（path_guard の設計原則）。

        r83: **リスト表示のときは先読みしない**。リスト表示のデリゲートは
        サムネイルを使わないので完全な無駄であり、クラウド（オンラインのみ）
        では «フォルダを開いただけで 64 個ダウンロード» という実害が出る。
        サムネイル表示に切り替えたカラムからは force=True で呼ぶ。"""
        import threading
        if not force and not self._any_thumb_column():
            return

        def _run():
            try:
                files = self._list_visible_files(path)
            except OSError:
                files = []
            if files:
                # UIスレッドへ引き渡し（prefetch はキュー投入のみで軽い）
                notifier = self._prefetch_notifier
                if notifier is not None:
                    notifier.files_ready.emit(files)

        threading.Thread(target=_run, daemon=True,
                         name="mfm-thumb-list").start()

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
                # 注意: os.path.isdir はリンクを «辿る» ため、ネットワーク先の
                # ジャンクションが経路にあるとUIスレッドがブロックする
                # （戻る/進む毎にフリーズした実例あり）。判定は lstat ベース
                # （辿らない）に限定する。
                if self._is_symlink_or_junction(comp):
                    import stat as _stat
                    st = os.lstat(comp)
                    if _stat.S_ISDIR(st.st_mode) or \
                            _stat.S_ISLNK(st.st_mode):
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
        cur_root = (_safe_file_path(self._fs_model, self._proxy.mapToSource(root_idx))
                    if root_idx.isValid() else "")
        if os.path.normcase(os.path.normpath(cur_root or "")) != \
                os.path.normcase(os.path.normpath(col_root)):
            self._column_view.setRootIndex(
                self._proxy.mapFromSource(self._fs_model.index(col_root)))
        src = self._fs_model.index(path)
        if src.isValid():
            self._column_view.setCurrentIndex(self._proxy.mapFromSource(src))
        self._pending_current = path
        self._prime_path_loading(path)
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

    def _prime_path_loading(self, target: str):
        """深い/別ドライブ/隠しフォルダ経由のパスでも確実にカラムを構築するため、
        ドライブ最上位から対象まで各階層の読み込みを起動する。
        QFileSystemModel は遅延ロードのため、最上位を fetchMore して
        directoryLoaded 連鎖（_on_fs_dir_loaded）で1段ずつ降りていく。"""
        try:
            ancestors = self._ancestors_of(target)  # deep→top
        except Exception:
            return
        if not ancestors:
            return
        top = ancestors[-1]
        # 全階層の読み込みを«一括で»収集キューへ投入する。
        # 従来は directoryLoaded 連鎖で1段ずつ降りており、深いパスでは
        # 階層数ぶんの直列待ちが起動時間に直結していた（10階層なら10往復）。
        # index(パス) は親の列挙前でもノードを明示生成するため一括投入できる。
        # 先頭=target（deep側）から入れ、目的地のカラムが最初に埋まるようにする。
        queued = 0
        for p in ancestors:
            try:
                idx = self._fs_model.index(p)
                if idx.isValid() and self._fs_model.canFetchMore(idx):
                    self._fs_model.fetchMore(idx)
                    queued += 1
            except Exception:
                pass
        _mfm_timeline("prime: %d/%d 階層を一括投入 target=%r"
                      % (queued, len(ancestors), target))
        # 既にロード済みの階層がある場合に備え、最初の再適用も試す
        self._advance_pending_load(top)
        # 保険: 対象が«既にロード済み»だと QFileSystemModel は directoryLoaded を
        # 再発火しない環境がある（pip版Qt。MayaのQtは再発火する）。その場合
        # advance が永遠に完了せず「クリックしても何も起きない」になるため、
        # シグナルに依存しない完了判定を遅延で数回試す。
        for _ms in (120, 400, 900):
            QTimer.singleShot(_ms, lambda p=target: self._maybe_finalize_navigation(p))

    def _maybe_finalize_navigation(self, target: str):
        """directoryLoaded 非発火時のフォールバック完了判定。
        対象 index が有効で子情報が読める状態なら到達扱いで仕上げる。
        （シンボリックリンク/ジャンクションを«2回目以降»に開く時、モデルが
        キャッシュ済みでシグナルが来ない事象への対策。EXE版で実際に発生）"""
        if not self._pending_current:
            return
        try:
            if os.path.normcase(os.path.normpath(self._pending_current)) != \
               os.path.normcase(os.path.normpath(target)):
                return
        except Exception:
            return
        tgt_idx = self._fs_model.index(target)
        if not tgt_idx.isValid():
            return
        try:
            ready = (self._fs_model.rowCount(tgt_idx) > 0
                     or not self._fs_model.canFetchMore(tgt_idx))
        except Exception:
            ready = False
        if not ready:
            return
        _mfm_log("finalize_nav(fallback): target=%r（directoryLoaded未発火対策）"
                 % target)
        _mfm_timeline("navigate完了(fallback): %r" % target)
        self._pending_current = None
        pidx = self._proxy.mapFromSource(tgt_idx)
        if pidx.isValid():
            self._column_view.setCurrentIndex(pidx)
        # 注意: r31の目的地優先ロードにより、祖先の列挙は完了«後»に届く。
        # 短いリトライだけだと祖先カラムが再アンカーされず「カラムがズレて
        # 見えない階層ができる」ため、遅めのリトライも入れる（rebuildは
        # already-correct スキップ付きなので余分な再構築コストは無い）。
        for _ms in (90, 320, 1200, 2600):
            QTimer.singleShot(_ms, lambda p=target: self._force_column_rebuild(p))

    def _advance_pending_load(self, loaded_path: str):
        """loaded_path（ロード完了済み階層）から対象へ向けて次の階層の
        読み込みを起動し、可能なら setCurrentIndex を再適用する。"""
        target = self._pending_current
        if not target:
            return
        try:
            t = os.path.normcase(os.path.normpath(target))
            ld = os.path.normcase(os.path.normpath(loaded_path))
        except Exception:
            return
        if not (t == ld or t.startswith(ld + os.sep)):
            return
        # 対象まで未達なら、対象へ向かう「次の1階層」をロード起動する
        if t != ld:
            rel = os.path.normpath(target)[len(os.path.normpath(loaded_path)):].lstrip("\\/")
            next_comp = rel.replace("\\", "/").split("/", 1)[0]
            if next_comp:
                next_path = os.path.join(loaded_path, next_comp)
                nidx = self._fs_model.index(next_path)
                if nidx.isValid() and self._fs_model.canFetchMore(nidx):
                    self._fs_model.fetchMore(nidx)
        # 現時点で対象インデックスが有効なら選択を再適用（カラムが伸びる）
        src = self._fs_model.index(target)
        proxy_idx = self._proxy.mapFromSource(src)
        _mfm_log("advance: loaded=%r reached=%s src_valid=%s proxy_valid=%s"
                 % (loaded_path, t == ld, src.isValid(), proxy_idx.isValid()))
        if t == ld and _MFM_DEBUG:
            # 到達時に祖先チェーンを診断（どこで proxy が切れるか）
            try:
                for anc in reversed(self._ancestors_of(target)):
                    si = self._fs_model.index(anc)
                    pi = self._proxy.mapFromSource(si)
                    hid = False
                    try:
                        hid = self._fs_model.fileInfo(si).isHidden()
                    except Exception:
                        pass
                    fv = os.path.normcase(os.path.normpath(anc)) in self._proxy._force_visible
                    _mfm_log("  chain anc=%r fs=%s proxy=%s hidden=%s force_visible=%s"
                             % (anc, si.isValid(), pi.isValid(), hid, fv))
            except Exception as _e:
                _mfm_log("  chain diag error %r" % _e)
        if proxy_idx.isValid():
            self._column_view.setCurrentIndex(proxy_idx)
        if t == ld:
            cur = self._column_view.currentIndex()
            curpath = ""
            if cur.isValid():
                curpath = _safe_file_path(self._fs_model, self._proxy.mapToSource(cur))
            _mfm_log("advance(after setCurrent): colview_current=%r" % curpath)
            _mfm_timeline("navigate完了: %r" % target)
            self._pending_current = None
            # 内部状態は正しいが表示カラムが古いブランチのまま残る QColumnView の
            # 描画バグ対策。ロード完全後に「ルート＋現在地」を遅延再適用して
            # カラムスタックを作り直す（シグナル内での setRootIndex はクラッシュ
            # するため QTimer で次のイベントループへ逃がす）。
            tgt = target
            # ロード/カラム除去が落ち着いた後にチェーンを強制再展開し、
            # 深いカラムの欠落とスクロール範囲の空白の両方を解消する。
            # （遅めのリトライは、目的地優先ロードで後から届く祖先の
            #  列挙に対する再アンカー用。r31のカラムズレ対策）
            for _ms in (90, 320, 1200, 2600):
                QTimer.singleShot(_ms, lambda p=tgt: self._force_column_rebuild(p))

    def _force_column_rebuild(self, target: str):
        """ルートと現在地を再適用して QColumnView のカラムを末端まで作り直す。
        既に current==target だと setCurrentIndex が no-op になり深い階層が
        展開されないため、一旦 current を無効化してから target を設定し直す。"""
        # ── r83: «ユーザーの選択を絶対に壊さない» ガード ─────────────────
        # この再構築は最大2.6秒後まで遅延実行される «見た目の自己修復» に過ぎない。
        # クラウドストレージのように列挙が遅いフォルダでは、ユーザーが
        # Ctrl/Shift でファイルを選んでいる最中にこれが発火し、
        # setCurrentIndex(無効)→再設定で選択が全消去される
        # （＝「クラウドストレージで複数選択ができない」の原因。2026-09-17 報告）。
        # 選択が2件以上ある、またはナビゲーション後にユーザーが選択操作を
        # 始めているなら、自己修復より «選択の維持» を優先して何もしない。
        try:
            if len(self._get_selected_paths()) > 1:
                _mfm_log("force_rebuild skip: 複数選択中のため中止 target=%r" % target)
                return
            if getattr(self._column_view, "_mfm_user_selected", False):
                _mfm_log("force_rebuild skip: ユーザー選択操作後のため中止 target=%r"
                         % target)
                return
        except Exception:
            pass
        # 既に別パスへ移動済みなら、古い再構築でカラムを壊さないようスキップ（高速クリック対策）。
        try:
            if os.path.normcase(os.path.normpath(target)) != \
               os.path.normcase(os.path.normpath(self._current_path or "")):
                return
        except Exception:
            return
        # 既に正しく表示できているなら何もしない。
        # force_rebuild は current 無効化→再設定でカラムを全て組み直すため、
        # 複数経路（advance / finalize_nav / 二段タイマー）から重複実行されると
        # カラムがガクガク動く。root・current・対象カラムの可視性まで一致して
        # いれば再構築は不要（リンククリック時の見た目を通常フォルダと同等にする）。
        try:
            cv = self._column_view
            nt = os.path.normcase(os.path.normpath(target))
            cur = cv.currentIndex()
            root = cv.rootIndex()
            cur_path = (_safe_file_path(self._fs_model, self._proxy.mapToSource(cur))
                        if cur.isValid() else "")
            root_path = (_safe_file_path(self._fs_model, self._proxy.mapToSource(root))
                         if root.isValid() else "")
            want_root = self._column_root_for(target)
            same = (cur_path and
                    os.path.normcase(os.path.normpath(cur_path)) == nt and
                    os.path.normcase(os.path.normpath(root_path or "")) ==
                    os.path.normcase(os.path.normpath(want_root)))
            if same:
                for v in cv.findChildren(QListView):
                    if v.isHidden() or v.model() is None:
                        continue
                    fp = cv._path_for_index(v.rootIndex())
                    if fp and os.path.normcase(os.path.normpath(fp)) == nt:
                        _mfm_log("force_rebuild skip: already correct target=%r"
                                 % target)
                        return
        except Exception:
            pass
        try:
            col_root = self._column_root_for(target)
            ridx = self._proxy.mapFromSource(self._fs_model.index(col_root))
            cidx = self._proxy.mapFromSource(self._fs_model.index(target))
            if ridx.isValid():
                self._column_view.setRootIndex(ridx)
            if cidx.isValid():
                # 無効→target で current 変更を強制し、深い階層まで再展開させる
                self._column_view.setCurrentIndex(QModelIndex())
                self._column_view.setCurrentIndex(cidx)
            try:
                self._column_view.updateGeometries()
            except Exception:
                pass
            # ── 診断＋自己修復 ─────────────────────────────────────
            # Windows実機のpip版Qtで «リンクをルートにすると直下がproxy越しに
            # 見えない／カラムが構築されない» 事象への対策。
            rrows = self._proxy.rowCount(ridx) if ridx.isValid() else -1
            n_cols = 0
            try:
                n_cols = sum(1 for v in self._column_view.findChildren(QListView)
                             if not v.isHidden() and v.model() is not None)
            except Exception:
                pass
            _mfm_log("force_rebuild: target=%r root_valid=%s cur_valid=%s "
                     "hbar_max=%d proxy_root_rows=%s visible_cols=%d"
                     % (target, ridx.isValid(), cidx.isValid(),
                        self._column_view.horizontalScrollBar().maximum(),
                        rrows, n_cols))
            if ridx.isValid() and rrows == 0:
                # 1) プロキシのフィルタ再評価で子のマッピングを作り直す
                try:
                    self._proxy.invalidateFilter()
                except Exception:
                    pass
                rrows2 = self._proxy.rowCount(ridx)
                _mfm_log("force_rebuild retry(invalidateFilter): rows=%s" % rrows2)
                if rrows2 == 0:
                    # 2) ドライブ最上位ルートへフォールバック（リンクルートを諦め、
                    #    最上位からのチェーン表示で target まで開く）
                    drive = os.path.splitdrive(os.path.normpath(target))[0]
                    top = (drive + os.sep) if drive else os.sep
                    tidx = self._proxy.mapFromSource(self._fs_model.index(top))
                    ci = self._proxy.mapFromSource(self._fs_model.index(target))
                    if tidx.isValid():
                        self._column_view.setRootIndex(tidx)
                        if ci.isValid():
                            self._column_view.setCurrentIndex(QModelIndex())
                            self._column_view.setCurrentIndex(ci)
                    _mfm_log("force_rebuild fallback(drive-top): root=%r cur_valid=%s"
                             % (top, ci.isValid()))
        except Exception as _e:
            _mfm_log("force_rebuild error %r" % _e)

    def _on_fs_dir_loaded(self, loaded_path: str):
        """遅延ロード完了時、保留中の対象へ向けて1段ずつ降りる。"""
        # 列挙完了のタイムライン（起動直後の停滞調査用）
        _mfm_log("directoryLoaded: %r" % loaded_path)
        try:
            _rc = self._fs_model.rowCount(self._fs_model.index(loaded_path))
        except Exception:
            _rc = -1
        _mfm_timeline("directoryLoaded: %r (%d件)" % (loaded_path, _rc))
        # スピナー消灯の根拠として記録（「空フォルダ」と「列挙中」の見分け用）
        self._column_view.note_dir_loaded(loaded_path)
        self._advance_pending_load(loaded_path)

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
            # 履歴に積む（↶で元の階層へ戻れるように。従来は積み漏れていた）
            self._history = self._history[:self._history_index + 1]
            self._history.append(parent)
            self._history_index = len(self._history) - 1
            self._sm.add_to_history(parent)

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
        return _safe_file_path(self._fs_model, source_index)

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
        # 通常クリックで下階層へ進んでも、複数選択の平坦結果は維持する。
        # 下位カラムで Ctrl/Shift 選択された時だけ _multi_select から対象を絞り直す。
        self._merge_panel.hide()
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
            if self._column_view.isVisible() and self._is_native_expandable(proxy_index, path):
                # 通常フォルダ(クリックしたカラムの子): ネイティブ列展開に任せルート不変
                _mfm_log("click dir: native expand path=%r" % path)
                self._set_current_path(path)
            else:
                # 別ブランチへのジャンプ(ショートカット先など) → トップから全カラム再構築
                _mfm_log("click dir: cross-branch -> _navigate path=%r current=%r"
                         % (path, self._current_path))
                self._navigate(path)
            if self._flat_col.isVisible():
                fv = self._column_view._flatten_view
                if fv is not None and getattr(fv, "_mfm_flatten", False):
                    # 平坦トグルON中は閉じずに、クリックしたフォルダへ追従
                    # （単一選択でも配下の階層を平坦で見続けたい、の仕様）
                    self._on_flat_request([path])
                else:
                    # 通常クリック＝単一選択（標準挙動）。複数選択の平坦カラムは
                    # 閉じて、通常のカラム展開に戻す。
                    self._on_flat_request([])
            return
        # 単一ファイル選択: パス欄にファイル名まで表示する
        try:
            self._addr_bar.setText(path)
        except Exception:
            pass
        # r97: 圧縮ファイルは «フォルダのように» 中身カラムを開く
        # （中身の操作範囲はエクスプローラーと同じ＝読み取り専用）
        from core.archive_browse import is_archive as _is_archive
        if _is_archive(path):
            self._on_archive_request(path)
            self._sync_quick_look(path)
            self.selection_changed.emit([path])
            return
        self._on_archive_request("")
        action = self._sm.get("single_click_action", "open")
        if action == "preview":
            action = "open"   # 「プレビュー」は「開く」へ統合（旧設定の移行）
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
            if self._column_view.isVisible() and self._is_native_expandable(proxy_index, path):
                self._set_current_path(path)  # カラム展開を維持（ルート不変）
                # r72: 子カラムの生成は本体 current に依存する。実機の
                # ダブルクリックは直前のプレスで current が動くが、QTest 実装
                # （Qt6 の QTest::mouseDClick は press を伴わない）や経路によって
                # は動かないため、ここで確実に current を合わせる（フォルダのみ。
                # ファイルは列スライド防止のため触らない）。
                try:
                    if self._column_view.currentIndex() != proxy_index:
                        self._column_view.setCurrentIndex(proxy_index)
                except Exception:
                    pass
            else:
                self._navigate(path)  # 別ブランチへのジャンプはトップから全カラム再構築
            return
        # ダブルクリックは関連付けアプリ（OS既定）で開く
        open_with_default_app(path)

    def _is_native_expandable(self, proxy_index, path: str) -> bool:
        """クリックした項目の «実際の親カラム» のフォルダが、解決後パスの親と
        一致すればネイティブ列展開でそのまま表示できる（ルート不変・リセット無し）。
        不一致＝ジャンクション/ショートカットで別ブランチに解決された場合のみ
        トップから再構築する。

        従来は self._current_path の祖先で判定していたが、複数選択時は
        current_path が表示中カラムとズレてしまい、通常クリックまで誤って
        cross-branch 扱い＝全カラムリセットになっていた（その回帰修正）。"""
        try:
            pidx = proxy_index.parent() if proxy_index is not None else None
            if pidx is not None and pidx.isValid():
                parent_path = self._resolve_path(pidx)
                if parent_path:
                    return (os.path.normcase(os.path.normpath(parent_path))
                            == os.path.normcase(os.path.normpath(os.path.dirname(path))))
        except Exception:
            pass
        # 親インデックスが取れない（ルート直下等）→ 通常クリック扱いでネイティブ
        return True

    def _maybe_follow_shortcut(self, path: str) -> bool:
        """Windowsショートカット(.lnk/.url)なら参照先を解決して移動する。
        解決先がフォルダ/ファイルどちらでも _navigate がドライブ最上位から
        全カラムで再表示する（ファイルは親カラム上で選択表示）。"""
        low = (path or "").lower()
        if not (low.endswith(".lnk") or low.endswith(".url")):
            _mfm_log("follow_shortcut: 非ショートカット path=%r" % path)
            return False
        target = resolve_windows_shortcut(path)
        _mfm_log("follow_shortcut: path=%r -> target=%r exists=%s"
                 % (path, target, (os.path.exists(target) if target else None)))
        if target and os.path.exists(target):
            self.status_message.emit(f"ショートカット解決: {path} → {target}")
            self._navigate(target)
            return True
        if target:
            self.status_message.emit(f"ショートカット参照先が見つかりません: {target}")
        return False

    def _dispatch_action(self, action: str, path: str):
        if action == "none":
            return   # クリック動作「None」= 何もしない
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

    # ------------------------------------------------------------------
    # 複数選択＋マージ表示
    # ------------------------------------------------------------------
    def _inline_column_base_width(self, total: int, exclude_norm=None) -> int:
        """右隣ペインを «次のカラム» に見せるため、既存カラム実幅に寄せる。

        QColumnView は広い表示領域を持つと末尾に空白を残す。平坦/統合ビューを
        Splitter の右側に置く場合でも、左側を実カラム幅へ詰めれば視覚的には
        選択列の直後に続く。
        """
        main_w = 0
        try:
            # 内部の columnWidths() は幻の予約エントリを含むことがあるため、
            # 実際に見えているカラムウィジェットの幅を合計する。
            # exclude_norm（間もなく畳まれる冗長子カラムのルート群）は除外し、
            # 後からの再リサイズ（揺れの原因）を不要にする
            for v in self._column_view.findChildren(QListView):
                if not (v.isVisible() and v.model() is not None and v.width() > 1):
                    continue
                if exclude_norm:
                    fp = self._column_view._path_for_index(v.rootIndex())
                    if fp and os.path.normcase(os.path.normpath(fp)) in exclude_norm:
                        continue
                main_w += v.width()
        except Exception:
            main_w = 0
        if main_w <= 0:
            try:
                widths = list(self._column_view.columnWidths() or [])
                main_w = sum(int(w) for w in widths if int(w) > 1)
            except Exception:
                main_w = 0
        if main_w <= 0:
            main_w = int(total * 0.60)
        # 実カラム幅ちょうどへ寄せて隙間を作らない（下限の強制は廃止。
        # 広いモニターで total*0.42 を強制すると巨大な空白が出る＝動画の症状）。
        return min(main_w + 4, int(total * 0.72))

    def _set_inline_panel_sizes(self, inline_index: int, preferred_width: int,
                                exclude_norm=None):
        """Splitter上の補助ビューを、カラムビュー直後へ詰めて表示する。"""
        try:
            total = max(self._view_stack.width(), 700)
            main_w = self._inline_column_base_width(total, exclude_norm)
            inline_w = min(max(int(preferred_width), 220), total - main_w)
            if inline_w < 220:
                inline_w = min(260, int(total * 0.35))
                main_w = max(240, total - inline_w)
            # 平坦カラムは «次のカラム» らしくカラム幅程度にし、
            # 余りは末尾スペーサー（空き背景）へ渡す
            inline_w = max(240, min(int(preferred_width), max(240, total - main_w)))
            sizes = [0] * self._view_stack.count()
            sizes[0] = main_w
            used = main_w
            # 共通フォルダのドリルダウン・カラムにも1本ずつ幅を配る
            for c in getattr(self, "_common_cols", []):
                try:
                    ci = self._view_stack.indexOf(c)
                    if ci >= 0 and c.isVisible():
                        sizes[ci] = 190
                        used += 190
                except Exception:
                    pass
            sizes[inline_index] = inline_w
            used += inline_w
            try:
                sp = self._view_stack.indexOf(self._flat_spacer)
                rest = max(0, total - used)
                if sp >= 0:
                    self._flat_spacer.setVisible(rest > 0)
                    sizes[sp] = rest
            except Exception:
                pass
            self._view_stack.setSizes(sizes)
            # 注意: ここで hbar を右端へ送る処理は行わない。視点が飛んで
            # 「元々のターゲットを見失う」ため、スクロール位置の調整は
            # _anchor_column_x / _restore_anchor_column_x（操作カラム固定）に任せる
        except Exception:
            pass

    def _align_columns_right_edge(self):
        """最後の«実幅»カラムの右端をビューポート右端へ合わせる。

        単純に hbar を最大へ送ると、幅0に畳んだ抑制カラムや QColumnView が
        内部予約する余白まで見えてしまい、選択列と平坦カラムの間に
        «大きな隙間» が出る（動画指摘の症状）。実幅カラムの右端で止める。"""
        try:
            cv = self._column_view
            vp = cv.viewport()
            hb = cv.horizontalScrollBar()
            if hb is None:
                return
            right = None
            for v in cv.findChildren(QListView):
                if not v.isVisible() or v.model() is None or v.width() <= 1:
                    continue
                edge = v.x() + v.width()   # viewport 座標系
                right = edge if right is None else max(right, edge)
            if right is None:
                hb.setValue(hb.maximum())
                return
            target = hb.value() + (right - vp.width())
            hb.setValue(max(0, min(int(target), hb.maximum())))
        except Exception:
            try:
                hb = self._column_view.horizontalScrollBar()
                if hb is not None:
                    hb.setValue(hb.maximum())
            except Exception:
                pass

    def _request_flat_integration_status(self, cap: int = 300):
        """平坦ビューに並ぶファイルのフォルダの連携状態をワーカーへ要求（r72）。
        従来は createColumn（カラム表示）でしか要求しておらず、平坦ビューの
        右クリックに Perforce の Revert 等が出なかった。フォルダ数は cap で
        打ち切る（判定は manager のワーカー直列、UI はキュー投入のみ）。"""
        try:
            fc = self._flat_col
            dirs = []
            seen = set()
            for p in fc.all_paths():
                d = os.path.dirname(p)
                k = os.path.normcase(d)
                if k not in seen:
                    seen.add(k)
                    dirs.append(d)
                    if len(dirs) >= cap:
                        break
            mgr = self._integrations()
            for d in dirs:
                mgr.request_status(d)
        except Exception as e:
            _mfm_log("flat integration status error: %r" % (e,))

    def _apply_item_size_to_views(self, px: int, mode: str = None):
        """カラム以外のビュー（独立サムネビュー・平坦カラム）へも表示サイズを配る。

        スライダーは «全体的な表示サイズ» を変えるもの、というユーザー指示（r102）。"""
        px = max(8, int(px))
        if mode in (None, "list"):
            # 平坦カラムは常にリスト表示なので «リストの寸法» だけを反映する
            try:
                self._flat_col._view.setIconSize(QSize(px, px))
                self._flat_col._view.viewport().update()
            except Exception:
                pass
        if mode not in (None, "thumb"):
            return
        try:
            self._thumb_view.setIconSize(QSize(px, px))
            self._thumb_view.setGridSize(
                QSize(px + 18, px + ThumbnailDelegate._TEXT_H + 8))
            if self._thumb_mgr is not None:
                self._thumb_delegate = ThumbnailDelegate(
                    self._thumb_mgr, px, self._thumb_view,
                    is_expanded_index=self._column_view._is_expanded_index)
                self._thumb_view.setItemDelegate(self._thumb_delegate)
            self._thumb_view.doItemsLayout()
            self._thumb_view.viewport().update()
        except Exception as e:
            _mfm_log("apply item size to views error: %r" % (e,))

    def _on_archive_request(self, path):
        """圧縮ファイルの中身カラムの表示／非表示（r97）。

        path が書庫ならその中身を «次のカラム» として出す。空文字・書庫でない・
        読めない場合は閉じる。書庫の中身は読み取り専用（エクスプローラー同様）。"""
        from core.archive_browse import is_archive as _is_archive
        try:
            if not path or not _is_archive(path):
                self._archive_col.hide()
                return
            if self._archive_col.archive_path() == path and \
                    self._archive_col.isVisible():
                return                      # 同じ書庫を開き直さない
            if not self._archive_col.set_archive(path):
                self._archive_col.hide()
                return
            self._merge_panel.hide()
            self._archive_col.show()
            col_w = 320
            for v in self._column_view.findChildren(QListView):
                if v.isVisible() and v.model() is not None and v.width() > 1:
                    col_w = max(260, v.width() + 20)
            self._set_inline_panel_sizes(
                self._view_stack.indexOf(self._archive_col), col_w)
            _mfm_log("archive: %s visible=%s"
                     % (os.path.basename(path), self._archive_col.isVisible()))
        except Exception as e:
            _mfm_log("archive request error: %r" % (e,))
            try:
                self._archive_col.hide()
            except Exception:
                pass

    def _on_flat_request(self, dirs):
        """平坦カラムの表示要求。dirs があれば選択カラムの直後へ詰めて表示する。"""
        dirs = [d for d in (dirs or []) if d and os.path.isdir(d)]
        if dirs:
            self._merge_panel.hide()
            self._on_archive_request("")        # r97: 書庫カラムとは排他
            self._flat_col.set_sources(dirs)
            self._request_flat_integration_status()
            self._flat_col.show()
            # «次のカラム» らしく、実カラムに近い幅で出す
            col_w = 320
            try:
                for v in self._column_view.findChildren(QListView):
                    if v.isVisible() and v.model() is not None and v.width() > 1:
                        col_w = max(260, v.width() + 20)
            except Exception:
                pass
            # リサイズは1回だけ行う（多段リサイズは視点が飛ぶ＝揺れの原因）。
            # 間もなく畳まれる冗長子カラム（選択フォルダ自身のカラム）は
            # 幅計算から除外して、後からの再詰めを不要にする
            excl = {os.path.normcase(os.path.normpath(d)) for d in dirs}
            # 共通フォルダのドリルダウン・カラムを（あれば）構築
            self._rebuild_common_columns(0, dirs)
            self._set_inline_panel_sizes(
                self._view_stack.indexOf(self._flat_col), col_w, exclude_norm=excl)
            _mfm_log("flat_request: dirs=%d visible=%s rows=%s w=%s"
                     % (len(dirs), self._flat_col.isVisible(),
                        self._flat_col._proxy.rowCount(), self._flat_col.width()))
            # 結果をステータスバーに必ず出す（動作したかどうかを画面上で判断できる）
            self.status_message.emit(
                "平坦表示: %d フォルダ → %d ファイル"
                % (len(dirs), self._flat_col._proxy.rowCount()))
            # 複数選択時: パス欄は «選択の直前のディレクトリ» まで表示
            if len(dirs) >= 2:
                try:
                    self._addr_bar.setText(os.path.dirname(dirs[0]))
                except Exception:
                    pass
        else:
            self._flat_col.hide()
            self._rebuild_common_columns(0, [])
            try:
                self._flat_spacer.hide()
            except Exception:
                pass
            # 平坦トグルの状態を表示と同期（ONのまま非表示を防ぐ）
            try:
                self._column_view._reset_flatten_toggle()
            except Exception:
                pass
            # 選択が無くなった時: パス欄を現在のディレクトリへ戻す
            try:
                self._addr_bar.setText(self._current_path or "")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 共通フォルダのドリルダウン（複数選択のフィルタリング）
    # ------------------------------------------------------------------

    def _rebuild_common_columns(self, level, sources):
        """level 番目以降の共通フォルダカラムを sources から再構築する。
        共通名（2ソース以上に存在する同名フォルダ）が無ければ打ち切り＝再帰終端。"""
        for w in self._common_cols[level:]:
            try:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            except Exception:
                pass
        del self._common_cols[level:]
        del self._common_srcs[level:]
        sources = [s for s in (sources or []) if os.path.isdir(s)]
        # レベル0（最初のカラム）は複数選択時のみ。深い階層は単一ソースでも掘れる
        if not sources or (level == 0 and len(sources) < 2):
            return
        try:
            from core.merge_browse import merge_children
            merged, _files = merge_children(sources)
        except Exception:
            merged = []
        # 重複していない（1ソースにしか無い）フォルダも含めて全てリストする。
        # 同名フォルダは1項目に統合（sources に各実パスを保持）
        common = list(merged)
        if not common:
            return
        from ui.common_columns import CommonFolderColumn
        col = CommonFolderColumn(level, self)
        col.set_entries(common)
        col.selection_changed.connect(
            lambda lv=level: self._on_common_selection(lv))
        # 平坦化の深さ（全階層/直下のみ）: 設定値で初期化し、切替は全カラム連動
        col.set_recursive(bool(self._sm.get("flat_recursive", True)))
        col.depth_mode_changed.connect(self._on_flat_depth_changed)
        idx = self._view_stack.indexOf(self._flat_col)
        self._view_stack.insertWidget(idx, col)
        col.show()
        self._common_cols.append(col)
        self._common_srcs.append(list(sources))

    def _on_flat_depth_changed(self, recursive: bool):
        """緑バーのスイッチ: 平坦表示を「全階層」⇔「選択フォルダ直下のみ」で切替。
        全ての子フォルダカラムのスイッチを同期し、平坦一覧を作り直す。"""
        recursive = bool(recursive)
        self._sm.set("flat_recursive", recursive)
        for c in self._common_cols:
            try:
                c.set_recursive(recursive)
            except Exception:
                pass
        try:
            self._flat_col.set_recursive(recursive)
        except Exception as e:
            _mfm_log("flat depth change error: %r" % (e,))

    def _on_common_selection(self, level):
        """共通フォルダカラムの選択変更 → 平坦ビューを絞り込み、次の階層を再帰構築。"""
        if level >= len(self._common_cols):
            return
        col = self._common_cols[level]
        sel = col.selected_sources()
        eff = sel if sel else list(self._common_srcs[level])
        # 平坦ビューを絞り込み（選択なし＝この階層の元ソース全体へ戻す）
        try:
            self._flat_col.set_sources(eff)
            self._request_flat_integration_status()
        except Exception:
            pass
        # 選択がある時のみ、さらに深い共通階層を掘る
        self._rebuild_common_columns(level + 1, sel if sel else [])
        col_w = 320
        excl = {os.path.normcase(os.path.normpath(d)) for d in eff}
        self._set_inline_panel_sizes(
            self._view_stack.indexOf(self._flat_col), col_w, exclude_norm=excl)

    def _show_inline_merge(self, dirs):
        """旧マージ入口。結果表示はツリーにせず、必ず平坦結果へ送る。"""
        self._on_flat_request(dirs)

    def _start_merge(self, dirs, mode="tree"):
        """旧マージ専用ビュー入口。現在は結果を常に平坦表示へ送る。"""
        self._on_flat_request(dirs)

    def _exit_merge(self):
        """マージ表示を閉じ、通常のカラムビューへ戻る。"""
        self._merge_panel.hide()
        self._thumb_view.hide()
        self._column_view.show()
        self.status_message.emit(self._current_path or "")

    def _show_context_menu(self, pos: QPoint):
        paths = self._get_selected_paths()
        sender = self.sender()
        # Explorer準拠の対象決定。各カラムの選択モデルは本体と別物のため、
        # 右クリック時点で本体側の選択が同期済みとは限らない。従来は
        # 「本体の選択リスト」をそのまま使っており、.ma を右クリックしても
        # 対象が古い選択やパンくず（祖先フォルダ）になり、Mayaメニューが
        # 出たり出なかったりした。
        # カーソル直下の項目は «カラム（子QListView）の座標系» で解決する。
        # QColumnView.indexAt() は各カラムのヘッダ分のビューポートマージン
        # （_COL_HEADER_H=52px）を考慮せず、1〜2行ズレた項目/無効を返すため、
        # 「.ma を右クリックしてもMayaメニューが出ない（再選択で出る）」の
        # 原因になっていた（r34の実装ミス、2026-09-07）。
        cursor_path = ""
        column_dir = ""          # r70: 空白を右クリックした時のカラムのフォルダ
        gpos = (sender.viewport().mapToGlobal(pos)
                if hasattr(sender, "viewport") else self.mapToGlobal(pos))
        try:
            # グローバル座標を含むビューポートを持つカラムを探し、そのカラムの
            # 座標系で indexAt する（カーソル位置APIに依存しない決定的な方法）
            lv_hit = None
            for lv in self._column_view.findChildren(QListView):
                if not lv.isVisible() or lv.model() is None:
                    continue
                vp = lv.viewport()
                top_left = vp.mapToGlobal(QPoint(0, 0))
                if (top_left.x() <= gpos.x() < top_left.x() + vp.width()
                        and top_left.y() <= gpos.y() < top_left.y() + vp.height()):
                    lv_hit = lv
                    break
            if lv_hit is not None:
                idx = lv_hit.indexAt(lv_hit.viewport().mapFromGlobal(gpos))
                if idx.isValid():
                    cursor_path = self._resolve_path(idx)
                else:
                    root = lv_hit.rootIndex()
                    if root.isValid():
                        column_dir = self._resolve_path(root)
            elif hasattr(sender, "indexAt"):
                idx = sender.indexAt(pos)
                if idx.isValid():
                    cursor_path = self._resolve_path(idx)
        except Exception:
            cursor_path = ""
        if cursor_path:
            if cursor_path not in paths:
                # 未選択の項目を右クリック → その項目だけを対象にする
                paths = [cursor_path]
            else:
                # 選択済み項目を右クリック → 選択全体を対象にするが、
                # パンくず（他の選択の祖先フォルダ）は対象から外す
                # （_drop_ancestor_dirs は CappedColumnView 側の静的メソッド）
                paths = CappedColumnView._drop_ancestor_dirs(paths)
        elif column_dir:
            # r70: カラムの空白を右クリック → そのフォルダのメニュー
            # （DCC からの保存／書き出し・貼り付け）。右ボタンのプレスで
            # カラムの選択は解除済み（r55）なので本体の選択は使わない。
            self._popup_folder_context_menu(column_dir, gpos)
            return
        if not paths:
            return
        self._popup_context_menu(paths, gpos)

    # ── r70: DCC からの保存／書き出し ─────────────────────────────────
    def _add_dcc_save_actions(self, menu, target: str, exts_hint=None):
        """«シーンを保存»«選択を書き出し» を «1つの DCC 分だけ» メニューへ
        （ユーザー指示: 4つに分けず、ヘッダで選択中の DCC を出す）。
        DCC の決定は dcc_for_path: .ma/.mb → Maya、.blend → Blender、
        フォルダ／共通形式 → 選択中の DCC（_dcc_target_provider）。
        target はフォルダ（空白右クリック）または単一ファイル（ダイアログの
        ファイル名欄に入る）。実行は _dcc_callback(app, action, [target])
        → MainWindow 側でダイアログ→DCC へ送信。"""
        from core.i18n import tr
        from core import dcc_save
        from core.file_operations import dcc_for_path
        dcc_cb = getattr(self, "_dcc_callback", None)
        if not callable(dcc_cb) or not target:
            return False
        ext = "" if os.path.isdir(target) else Path(target).suffix.lower()
        app = self.current_dcc_target()
        if ext:
            app = dcc_for_path(target, app)
            if ext not in dcc_save.exts_for(app):
                return False
        label = "Blender" if app == "blender" else "Maya"
        a = menu.addAction(tr("💾  %s: シーンをここに保存...", "💾  %s: Save Scene here...") % label)
        a.triggered.connect(lambda _c=False, ap=app: dcc_cb(ap, "save_scene", [target]))
        a = menu.addAction(tr("📤  %s: 選択を書き出し...", "📤  %s: Export Selection...") % label)
        a.triggered.connect(lambda _c=False, ap=app: dcc_cb(ap, "export_selection", [target]))
        return True

    def current_dcc_target(self) -> str:
        """ヘッダのスイッチで選択中の DCC（MainWindow が provider を登録。既定 maya）。"""
        fn = getattr(self, "_dcc_target_provider", None)
        try:
            v = fn() if callable(fn) else None
        except Exception:
            v = None
        return "blender" if v == "blender" else "maya"

    def set_dcc_target_provider(self, fn):
        self._dcc_target_provider = fn

    def _popup_folder_context_menu(self, folder: str, global_pos):
        """カラムの空白（項目の無い所）の右クリックメニュー（r70）。"""
        from core.i18n import tr
        menu = QMenu(self)
        # r73: 新規作成（作成後はそのまま名前変更モード）
        nf = menu.addAction(tr("📁  新規フォルダ", "📁  New folder") + "\tCtrl+Shift+N")
        nf.triggered.connect(lambda _c=False: self._create_new_item("folder", folder))
        nt = menu.addAction(tr("📄  新規テキスト ドキュメント", "📄  New text document") + "\tCtrl+Shift+T")
        nt.triggered.connect(lambda _c=False: self._create_new_item("text", folder))
        menu.addSeparator()
        if self._add_dcc_save_actions(menu, folder):
            menu.addSeparator()
        paste_act = menu.addAction(tr("📋  貼り付け", "📋  Paste") + "\tCtrl+V")
        mime = QApplication.clipboard().mimeData()
        paste_act.setEnabled(bool(mime and mime.hasUrls()))
        paste_act.triggered.connect(lambda _c=False: self._clipboard_paste_into(folder))
        menu.addSeparator()
        reveal_act = menu.addAction(tr("📁  エクスプローラーで表示", "📁  Show in Explorer"))
        reveal_act.triggered.connect(lambda _c=False: reveal_in_explorer(folder))
        copy_path_act = menu.addAction(tr("📋  フォルダパスをコピー", "📋  Copy folder path"))
        copy_path_act.triggered.connect(lambda _c=False: self._copy_paths_to_clipboard([folder]))
        try:
            menu.exec_(global_pos)
        except AttributeError:
            menu.exec(global_pos)

    def _clipboard_paste_into(self, folder: str):
        """右クリックしたカラムのフォルダへ貼り付け（_clipboard_paste の貼り付け先を差し替え）。"""
        self._paste_override_dir = folder
        try:
            self._clipboard_paste()
        finally:
            self._paste_override_dir = None

    def _run_integration_action(self, cb, extra=None, label="", paths=None):
        """連携操作の実行（外部GUIの起動は即返る／CLIは内部でワーカー化済み）。
        r58: extra["confirm"] があれば実行前に Yes/No 確認。結果は manager の
        action_finished（→ _on_integration_action_finished）で通知される。
        数秒後に状態を再取得して表示へ反映する。"""
        from core.i18n import tr
        extra = extra or {}
        if extra.get("confirm"):
            ret = QMessageBox.question(
                self, "⎇ " + (label or tr("連携", "Integration")), extra["confirm"],
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        try:
            cb()
        except Exception as e:
            _mfm_log("integration action error: %r" % (e,))
        try:
            # r72: 対象ファイルのフォルダ全部（平坦ビューは複数階層）を再取得
            targets = list(paths or []) + [self._current_path]
            QTimer.singleShot(3000, lambda: self._refresh_integration_status(targets))
        except Exception:
            pass


    def _refresh_integration_status(self, paths):
        try:
            from core.integrations import get_manager
            mgr = get_manager()
            dirs = set()
            for p in paths or []:
                if p:
                    dirs.add(os.path.dirname(p))
                    dirs.add(p)
            for d in dirs:
                mgr.refresh(d)
        except Exception as e:
            _mfm_log("integration refresh error: %r" % (e,))

    def _show_flat_context_menu(self, pos: QPoint):
        """平坦ビューの右クリックメニュー（通常カラムと同仕様）。"""
        try:
            paths = self._flat_col.selected_paths()
        except Exception:
            paths = []
        if not paths:
            return
        gpos = self._flat_col._view.viewport().mapToGlobal(pos)
        self._popup_context_menu(paths, gpos)

    def _popup_context_menu(self, paths, global_pos):
        from core.i18n import tr
        menu = QMenu(self)
        is_maya = all(Path(p).suffix.lower() in MAYA_EXTENSIONS for p in paths)
        is_single = len(paths) == 1
        is_dir = is_single and os.path.isdir(paths[0])

        # ── 複数フォルダの平坦結果表示（2つ以上フォルダ選択時） ─────────
        sel_dirs = [p for p in paths if os.path.isdir(p)]
        if len(sel_dirs) >= 2:
            flat_act = menu.addAction(tr("▤  選択フォルダを平坦結果表示",
                                         "▤  Show selection as flat list"))
            flat_act.triggered.connect(lambda: self._on_flat_request(sel_dirs))
            menu.addSeparator()

        # ── DCC actions（Maya / Blender、r65） ─────────────────────────
        # 送り先を明示するため _dcc_callback(app, action, paths) を使う。
        # 「開く」はネイティブ形式かつ単一のみ。インポート/リファレンス(リンク)は
        # 複数対応（リファレンスはデフォルトNamespaceでダイアログ無し）。
        from core.file_operations import (BLENDER_EXTENSIONS)
        exts = {Path(p).suffix.lower() for p in paths}
        dcc_cb = getattr(self, "_dcc_callback", None)
        n = len(paths)
        cnt = ("（%d 件）" % n) if n > 1 else ""
        cnt_en = (" (%d files)" % n) if n > 1 else ""
        added_dcc = False
        # r80: 交換形式（.fbx/.obj/.abc/.usd…）はヘッダで選択中の DCC 分だけ出す。
        # ネイティブ形式（.ma/.mb → Maya、.blend → Blender）は選択中 DCC に関係なく出す。
        # 以前は交換形式で Maya と Blender の両方が並んでいた（仕様外）。
        dcc_target = self.current_dcc_target()
        show_maya = bool(exts & MAYA_EXTENSIONS) or dcc_target == "maya"
        show_blender = bool(exts & BLENDER_EXTENSIONS) or dcc_target == "blender"
        # r91: 各項目は «そのコマンドの対象形式» のときだけ出す（core/dcc_caps.py）。
        # 送信側（MainWindow._dcc_accepts）と同じ表なので «見えるのに送れない» は無い。
        from core import dcc_caps
        if callable(dcc_cb) and exts and show_maya:
            m_added = False
            if is_single and exts <= dcc_caps.exts("maya", "open"):
                a = menu.addAction(tr("🗂  Maya で開く", "🗂  Open in Maya"))
                a.triggered.connect(lambda _c=False: dcc_cb("maya", "open", list(paths)))
                m_added = True
            if exts <= dcc_caps.exts("maya", "import"):
                a = menu.addAction(tr("⬇  Maya にインポート" + cnt, "⬇  Import into Maya" + cnt_en))
                a.triggered.connect(lambda _c=False: dcc_cb("maya", "import", list(paths)))
                m_added = True
            if exts <= dcc_caps.exts("maya", "reference"):
                a = menu.addAction(tr("🔗  Maya にリファレンス" + cnt,
                                      "🔗  Reference into Maya" + cnt_en))
                # 単一は Namespace を確認（従来どおり）。複数は一括（ダイアログ無し）
                ref_action = "reference_ask" if is_single else "reference"
                a.triggered.connect(lambda _c=False: dcc_cb("maya", ref_action, list(paths)))
                m_added = True
            added_dcc = added_dcc or m_added
        if callable(dcc_cb) and exts and show_blender:
            b_items = []
            if is_single and exts <= dcc_caps.exts("blender", "open"):
                b_items.append((tr("🗂  Blender で開く", "🗂  Open in Blender"), "open"))
            if exts <= dcc_caps.exts("blender", "import"):
                b_items.append((tr("⬇  Blender にインポート（Append）" + cnt,
                                   "⬇  Import into Blender (Append)" + cnt_en), "import"))
            if exts <= dcc_caps.exts("blender", "reference"):
                b_items.append((tr("🔗  Blender にリンク（Link）" + cnt,
                                   "🔗  Link into Blender" + cnt_en), "reference"))
            if b_items and added_dcc:
                menu.addSeparator()
            for text, act in b_items:
                a = menu.addAction(text)
                a.triggered.connect(lambda _c=False, ac=act: dcc_cb("blender", ac, list(paths)))
            added_dcc = added_dcc or bool(b_items)
        if added_dcc:
            menu.addSeparator()
        # r70: 単一のシーン/書き出し形式ファイル → その名前で保存／書き出し
        if is_single and not is_dir and self._add_dcc_save_actions(menu, paths[0]):
            menu.addSeparator()

        # ── General actions ───────────────────────────────────────────
        if is_single:
            open_ext_act = menu.addAction(tr("🖥  関連付けアプリで開く",
                                             "🖥  Open with default app"))
            open_ext_act.triggered.connect(lambda: open_with_default_app(paths[0]))

            reveal_act = menu.addAction(tr("📁  エクスプローラーで表示",
                                           "📁  Show in Explorer"))
            reveal_act.triggered.connect(lambda: reveal_in_explorer(paths[0]))
            menu.addSeparator()

        # ── 外部サービス連携（該当ワークスペースのプロバイダだけ表示） ────
        try:
            from core.integrations import get_manager
            mgr = get_manager()
            groups = mgr.actions_for(paths) if mgr.enabled else []
        except Exception:
            groups = []
        if groups:
            for label, acts in groups:
                sub = menu.addMenu("⎇  " + label)
                sub.setToolTipsVisible(True)
                for item in acts:
                    text, cb = item[0], item[1]
                    extra = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
                    if text is None:
                        sub.addSeparator()
                        continue
                    a = sub.addAction(text)
                    # r58: 事前チェック（実行不可＝灰色＋理由 / 確認文）
                    if extra.get("enabled") is False:
                        a.setEnabled(False)
                        if extra.get("tooltip"):
                            a.setToolTip(extra["tooltip"])
                            a.setText(text + tr("（不可）", " (unavailable)"))
                        continue
                    a.triggered.connect(
                        lambda _c=False, f=cb, e=extra, l=label, ps=list(paths):
                        self._run_integration_action(f, e, l, ps))
            refresh_act = menu.addAction(tr("🔄  連携状態を更新",
                                            "🔄  Refresh integration status"))
            refresh_act.triggered.connect(
                lambda: self._refresh_integration_status(paths))
            menu.addSeparator()

        # ── パスをコピー（単数/複数対応） ─────────────────────────────
        copy_path_act = menu.addAction(tr("📋  ファイルパスをコピー",
                                          "📋  Copy file path"))
        copy_path_act.triggered.connect(lambda: self._copy_paths_to_clipboard(paths))
        menu.addSeparator()

        # ── Bookmark ─────────────────────────────────────────────────
        bm_act = menu.addAction(tr("⭐  ブックマークに追加", "⭐  Add to bookmarks"))
        bm_act.triggered.connect(lambda: self._add_to_bookmarks(paths))
        menu.addSeparator()

        # ── File ops ─────────────────────────────────────────────────
        copy_act = menu.addAction(tr("📋  コピー...", "📋  Copy..."))
        copy_act.triggered.connect(lambda: self._copy_dialog(paths))

        move_act = menu.addAction(tr("✂  移動...", "✂  Move..."))
        move_act.triggered.connect(lambda: self._move_dialog(paths))

        dup_act = menu.addAction(tr("⧉  複製...", "⧉  Duplicate...") + "\tCtrl+D")
        dup_act.triggered.connect(lambda _c=False: self._duplicate_selected(list(paths)))

        rename_act = menu.addAction(tr("✏  名前変更", "✏  Rename") + "\tF2")
        rename_act.triggered.connect(
            lambda _c=False: self._rename_inline(paths[0]) if paths else None)
        rename_act.setEnabled(is_single)

        # r101: 選択したものだけをバッチリネームの対象にする
        # （ツールメニューからだと «現在の選択» を拾い直すため、右クリックした
        #  対象と食い違うことがあった）
        batch_act = menu.addAction(tr("✏✏  バッチリネーム...", "✏✏  Batch Rename..."))
        batch_act.triggered.connect(
            lambda _c=False, ps=list(paths): self.batch_rename_requested.emit(ps))

        menu.addSeparator()

        del_act = menu.addAction(tr("🗑  削除", "🗑  Delete"))
        del_act.triggered.connect(lambda: self._delete_confirm(paths))
        del_act.setShortcut("Delete")

        menu.addSeparator()

        # ── Properties ───────────────────────────────────────────────
        if is_single:
            prop_act = menu.addAction(tr("ℹ  プロパティ", "ℹ  Properties"))
            prop_act.triggered.connect(lambda: self._show_properties(paths[0]))

        try:
            menu.exec_(global_pos)
        except AttributeError:
            menu.exec(global_pos)

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

    _DIR_DLG_OPTS = QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog

    def _copy_dialog(self, paths: List[str]):
        dst = QFileDialog.getExistingDirectory(self, "コピー先を選択", self._current_path,
                                               self._DIR_DLG_OPTS)
        if dst:
            self._transfer_with_progress(paths, dst, False, "コピー")

    def _move_dialog(self, paths: List[str]):
        dst = QFileDialog.getExistingDirectory(self, "移動先を選択", self._current_path,
                                               self._DIR_DLG_OPTS)
        if dst:
            self._transfer_with_progress(paths, dst, True, "移動")

    # ── r73: 新規フォルダ / 新規テキスト（Ctrl+Shift+N / Ctrl+Shift+T、空白右クリック） ──
    def _active_column_folder(self) -> Optional[str]:
        """«アクティブなカラム» のフォルダ: フォーカスのあるカラム（子 QListView）の
        rootIndex → 無ければ単一選択フォルダ／現在地（_paste_target_dir と同じ規則）。"""
        try:
            fw = QApplication.focusWidget()
            for lv in self._column_view.findChildren(QListView):
                if fw is None:
                    break
                if lv.isVisible() and lv.rootIndex().isValid() and \
                        (lv is fw or lv.isAncestorOf(fw)):
                    p = self._resolve_path(lv.rootIndex())
                    if p and os.path.isdir(p):
                        return p
        except Exception:
            pass
        return self._paste_target_dir()

    @staticmethod
    def _unique_new_name(folder: str, base: str, ext: str = "") -> str:
        """Explorer 風: «新しいフォルダ» → «新しいフォルダ (2)» …"""
        name = base + ext
        n = 2
        while os.path.lexists(os.path.join(folder, name)):
            name = "%s (%d)%s" % (base, n, ext)
            n += 1
        return name

    def _create_new_item(self, kind: str, folder: str = None):
        """kind: "folder" / "text"。作成後はそのままインライン名前変更モードへ。
        QFileSystemModel が新項目を表示するまで（ウォッチャ経由・非同期）少し
        待ってから _rename_inline を開く。"""
        folder = folder or self._active_column_folder()
        if not folder or not os.path.isdir(folder):
            self.status_message.emit("作成先フォルダを特定できません")
            return
        try:
            if kind == "folder":
                name = self._unique_new_name(folder, "新しいフォルダ")
                path = os.path.join(folder, name)
                os.mkdir(path)
            else:
                name = self._unique_new_name(folder, "新しいテキスト ドキュメント", ".txt")
                path = os.path.join(folder, name)
                with open(path, "x", encoding="utf-8"):
                    pass
        except Exception as e:
            QMessageBox.critical(self, "新規作成", str(e))
            return
        self.status_message.emit("作成: %s" % name)
        self._last_created_path = path
        self._refresh_flat_view()
        self._rename_when_visible(path)

    def _rename_when_visible(self, path: str, tries: int = 25):
        """新規項目がカラムに現れたらインライン名前変更を開く（最大 ~2.5 秒待つ）。"""
        def attempt(left):
            try:
                idx = self._column_view._proxy_index_for_path(path)
                if idx.isValid():
                    views = [v for v in self._column_view.findChildren(QListView)
                             if getattr(v, "_mfm_header", None) is not None and v.isVisible()
                             and v.rootIndex() == idx.parent()
                             and v.visualRect(idx).isValid() and not v.visualRect(idx).isEmpty()]
                    if views:
                        # 見えるように選択してから編集（列はスライドさせない）
                        try:
                            self._column_view._prune_selection_to_single(idx)
                            views[0].scrollTo(idx)
                        except Exception:
                            pass
                        self._rename_inline(path)
                        return
            except Exception as e:
                _mfm_log("rename_when_visible error: %r" % (e,))
            if left > 0:
                QTimer.singleShot(100, lambda: attempt(left - 1))
            else:
                # カラムに出てこない（フィルタ等）→ ダイアログで名前変更
                self._rename_dialog([path])
        attempt(tries)

    def _release_fs_handles(self, folder: str, level: int = 1):
        """リネーム／移動の前に «Manager 自身が掴んでいるもの» を手放す（r98）。

        QFileSystemModel（QFileInfoGatherer）は fetch したディレクトリを
        監視スレッドで掴み続けるため、Windows では «Manager で開いている
        フォルダ» のリネーム／移動が [WinError 5] で弾かれる。
        level 1 = rootPath を親へ逃がす＋サムネイル停止、
        level 2 以上 = モデルごと作り直して監視ハンドルを完全に手放す。"""
        try:
            parent = os.path.dirname(folder.rstrip("/\\")) or folder
            tm = getattr(self, "_thumb_mgr", None)
            if tm is not None:
                for name in ("cancel_all", "clear_queue", "stop"):
                    fn = getattr(tm, name, None)
                    if callable(fn):
                        try:
                            fn()
                        except Exception:
                            pass
                        break
            if level <= 1:
                if parent and os.path.isdir(parent):
                    self._fs_model.setRootPath(parent)
                QApplication.processEvents()
                return
            self._recreate_fs_model(parent)
        except Exception as e:
            _mfm_log("release_fs_handles error: %r" % (e,))

    def _recreate_fs_model(self, root_hint: str = ""):
        """QFileSystemModel を作り直して «監視スレッドのハンドル» を完全に手放す。

        Qt には «このディレクトリの監視だけ外す» API が無いため、掴みっぱなしを
        確実に解くにはモデルごと捨てるしかない（r98）。重い操作なので
        リネーム／移動が権限エラーで弾かれた時だけ通る道。"""
        old = self._fs_model
        new = QFileSystemModel()
        try:
            new.setIconProvider(_SafeIconProvider())
        except Exception:
            pass
        new.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Files | QDir.Hidden)
        new.setReadOnly(False)
        for setter, arg in ((new.setResolveSymlinks, False),):
            try:
                setter(arg)
            except Exception:
                pass
        try:
            new.setOption(QFileSystemModel.DontUseCustomDirectoryIcons, True)
        except Exception:
            pass
        try:
            new.fileRenamed.connect(self._on_fs_file_renamed)
            new.directoryLoaded.connect(self._on_fs_dir_loaded)
        except Exception:
            pass
        self._fs_model = new
        try:
            self._proxy.setSourceModel(new)
        except Exception as e:
            _mfm_log("recreate model: setSourceModel failed %r" % (e,))
        try:
            old.directoryLoaded.disconnect()
        except Exception:
            pass
        try:
            old.fileRenamed.disconnect()
        except Exception:
            pass
        try:
            old.setParent(None)
            old.deleteLater()
        except Exception:
            pass
        QApplication.processEvents()
        _mfm_log("fs model recreated (root_hint=%s)" % (root_hint,))

    @staticmethod
    def nearest_existing_dir(path: str) -> str:
        """path から «実在する一番近い親» を返す（r98）。

        Manager で開いているフォルダがリネーム／移動／削除で消えた時の
        行き先。どこまで遡っても無ければ空文字。"""
        p = (path or "").rstrip("/\\")
        seen = set()
        while p and p not in seen:
            seen.add(p)
            if os.path.isdir(p):
                return p
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        return ""

    def _check_current_gone_async(self):
        """現在地が消えていないかを **ワーカースレッドで** 確かめる（r107）。

        stat が返らないパス（切断された共有・同期中の Perforce ワークスペース）
        でも UI を止めない。消えていた時だけ UI スレッドへ移動先を渡す。"""
        cur = self._current_path or ""
        if not cur or self._gone_probe_busy:
            return
        # 進行中のファイル操作・統合コマンドがある時は触らない（無駄な I/O）
        if getattr(self, "_file_op_running", False):
            return
        self._gone_probe_busy = True

        def _work():
            dest = ""
            try:
                if not os.path.isdir(cur):
                    dest = self.nearest_existing_dir(
                        os.path.dirname(cur.rstrip("/\\")) or cur)
            except Exception:
                dest = ""
            try:
                self._gone_result.emit(cur, dest)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True,
                         name="mfm-gone-probe").start()

    def _on_gone_result(self, checked_path: str, dest: str):
        """ワーカーの判定結果を UI スレッドで受ける（r107）。"""
        self._gone_probe_busy = False
        if not dest:
            return
        if (self._current_path or "") != checked_path:
            return          # 判定中に別の場所へ移動していた
        _mfm_log("current path gone(async): %s -> %s" % (checked_path, dest))
        self.status_message.emit(
            "表示中のフォルダが無くなったため、%s へ移動しました" % dest)
        self.navigate_to(dest)

    def _fallback_to_existing_ancestor(self) -> bool:
        """現在地が消えていたら、実在する一番近い親フォルダへ移動する（r98）。"""
        cur = self._current_path or ""
        if not cur or os.path.isdir(cur):
            return False
        dest = self.nearest_existing_dir(os.path.dirname(cur.rstrip("/\\")) or cur)
        if not dest:
            return False
        _mfm_log("current path gone: %s -> %s" % (cur, dest))
        self.status_message.emit(
            "表示中のフォルダが無くなったため、%s へ移動しました" % dest)
        self.navigate_to(dest)
        return True

    def _rename_path(self, src: str, dst: str):
        """リネーム本体（r98）。Windows の «アクセスが拒否されました» は
        監視ハンドル／OneDrive 同期が原因のことが多いので、手放し＋再試行＋
        シェル経由フォールバックまで面倒を見る。"""
        from core.file_operations import rename_path as _rn
        ok, err = _rn(src, dst,
                      release_cb=lambda lv=1: self._release_fs_handles(src, lv))
        if not ok:
            _mfm_warn("rename failed: %s -> %s : %r" % (src, dst, err))
        return ok, err

    @staticmethod
    def _rename_error_text(src: str, err) -> str:
        werr = getattr(err, "winerror", None)
        if werr in (5, 32):
            return ("名前を変更できませんでした（アクセスが拒否されました）。\n\n"
                    "%s\n\n"
                    "このフォルダ／ファイルを他のプログラムが使用中の可能性が"
                    "あります。よくある原因:\n"
                    "・OneDrive が同期中（同期の完了を待つ／一時停止する）\n"
                    "・中のファイルをアプリ（Maya 等）が開いている\n"
                    "・エクスプローラーや端末が同じフォルダを開いている"
                    % (err,))
        return str(err)

    def _select_when_visible(self, path: str, tries: int = 25):
        """path がカラム（または平坦／サムネビュー）に現れたら選択する（r98）。

        QFileSystemModel の反映はウォッチャ経由で非同期なので、出てくるまで
        最大 ~2.5 秒リトライする。インライン編集中は選択を奪わない
        （Tab 送りの連続リネームを壊さないため）。"""
        def attempt(left):
            try:
                if getattr(self, "_inline_editor", None) is not None:
                    return          # 編集中＝別の項目を編集している。触らない
                if self._select_path_now(path):
                    return
            except Exception as e:
                _mfm_log("select_when_visible error: %r" % (e,))
            if left > 0:
                QTimer.singleShot(100, lambda: attempt(left - 1))
        attempt(tries)

    def _select_path_now(self, path: str) -> bool:
        """path を «今» 選択できるなら選択して True。まだ出ていなければ False。"""
        if not path:
            return False
        idx = self._column_view._proxy_index_for_path(path)
        if idx.isValid():
            self._column_view._prune_selection_to_single(idx)
            for v in self._column_view.findChildren(QListView):
                try:
                    if v.isVisible() and v.rootIndex() == idx.parent():
                        v.scrollTo(idx)
                        break
                except Exception:
                    continue
            self.selection_changed.emit([path])
            return True
        # 平坦カラム／サムネビューで見ている場合
        for view in (self._thumb_view, getattr(self._flat_col, "_view", None)):
            if view is None or not view.isVisible():
                continue
            m = view.model()
            if m is None:
                continue
            for row in range(m.rowCount()):
                i = m.index(row, 0)
                p = self._resolve_path(i) if view is self._thumb_view \
                    else i.data(_QtCore.Qt.UserRole + 1)
                if p and os.path.normcase(os.path.abspath(p)) == \
                        os.path.normcase(os.path.abspath(path)):
                    sm = view.selectionModel()
                    if sm is not None:
                        QISM = _QtCore.QItemSelectionModel
                        sm.select(i, QISM.ClearAndSelect | QISM.Rows)
                        sm.setCurrentIndex(i, QISM.NoUpdate)
                    view.scrollTo(i)
                    self.selection_changed.emit([path])
                    return True
        return False

    def _rename_inline(self, path: str = None) -> bool:
        """F2 / 右クリック「名前変更」: カラムの項目をその場で編集する（r61）。
        QFileSystemModel は readOnly=False なので EditRole の setData がそのまま
        リネームになる（カラムは NoEditTriggers のためクリックでは開かない）。
        カラム内に見つからない時（平坦ビュー等）はダイアログへ退避。"""
        try:
            if path is None:
                targets = self._operation_targets()
                if len(targets) != 1:
                    self.status_message.emit("名前変更は1件だけ選択してください")
                    return False
                path = targets[0]
            idx = self._column_view._proxy_index_for_path(path)
            if idx.isValid():
                views = [v for v in self._column_view.findChildren(QListView)
                         if getattr(v, "_mfm_header", None) is not None and v.isVisible()
                         and v.rootIndex() == idx.parent()]
                if not views and self._thumb_view.isVisible():
                    views = [self._thumb_view]
                if views:
                    # current は動かさない（ファイルを current にすると
                    # プレビュー列生成＝カラムのスライドを誘発する）。
                    # Qt の view.edit() は使わない: QFileSystemModel::flags は
                    # 書き込み権限の無いファイル（Perforce 同期の read-only）に
                    # ItemIsEditable を付けず、edit() が黙って失敗する（実機で
                    # 「F2 が効かない」原因）。自前の QLineEdit をその場に重ねる。
                    self._open_inline_editor(views[0], idx, path)
                    return True
        except Exception as e:
            _mfm_log("rename inline error: %r" % (e,))
        self._rename_dialog([path] if path else [])
        return False

    def _open_inline_editor(self, view, idx, path: str):
        """項目の矩形に QLineEdit を重ねて名前を編集する。Enter=確定 / Esc=取消 /
        フォーカス喪失=確定（Explorer 準拠）。確定は os.rename（モデルの flags に
        依存しない）。"""
        old_editor = getattr(self, "_inline_editor", None)
        if old_editor is not None:
            try:
                old_editor.close()
            except Exception:
                pass
        rect = view.visualRect(idx)
        if not rect.isValid():
            self._rename_dialog([path])
            return
        vp = view.viewport()
        ed = QLineEdit(vp)
        ed.setObjectName("mfmInlineRename")
        name = os.path.basename(path.rstrip("/\\"))
        ed.setText(name)
        # アイコン分を空けて項目の上に重ねる。エディタは «不透明»（QSS
        # #mfmInlineRename）にして下の項目名が透けないようにする。
        try:
            isz = view.iconSize().width()
        except Exception:
            isz = -1
        dec = (isz if isz > 0 else 16) + 6
        ed.setAutoFillBackground(True)
        ed.setGeometry(rect.left() + dec, rect.top(), max(80, rect.width() - dec), rect.height())
        ed.show()
        ed.raise_()
        ed.setFocus(Qt.OtherFocusReason)
        # Explorer 同様、拡張子を除いた部分を選択
        stem_len = len(name) if os.path.isdir(path) else len(os.path.splitext(name)[0])
        ed.setSelection(0, stem_len if stem_len > 0 else len(name))
        self._inline_editor = ed
        state = {"done": False}

        def finish(commit: bool):
            if state["done"]:
                return
            state["done"] = True
            new_name = ed.text().strip()
            try:
                ed.hide()
                ed.deleteLater()
            except Exception:
                pass
            self._inline_editor = None
            if not commit or not new_name or new_name == name:
                return
            if any(c in new_name for c in '\\/:*?"<>|'):
                QMessageBox.warning(self, "名前変更", "ファイル名に使えない文字が含まれています。")
                return
            new_path = os.path.join(os.path.dirname(path), new_name)
            if os.path.lexists(new_path):
                QMessageBox.warning(self, "名前変更", "同名の項目が既にあります:\n%s" % new_name)
                return
            from core.undo_stack import RenameOp
            ok, err = self._rename_path(path, new_path)
            if not ok:
                QMessageBox.critical(self, "名前変更",
                                     self._rename_error_text(path, err))
                return
            try:
                self._undo_stack().push(RenameOp(path, new_path))
                self._on_fs_file_renamed(os.path.dirname(path), name, new_name, record=False)
            except Exception as e:
                _mfm_log("rename post error: %r" % (e,))

        ed.returnPressed.connect(lambda: finish(True))
        ed.editingFinished.connect(lambda: finish(True))   # フォーカス喪失=確定（Explorer準拠）

        # Tab / Shift+Tab: 確定して同じカラムの次／前の項目の名前変更へ（r67）
        def _neighbor_path(step: int):
            try:
                m = view.model()
                row = idx.row() + step
                if row < 0 or row >= m.rowCount(idx.parent()):
                    return None
                return self._resolve_path(m.index(row, 0, idx.parent()))
            except Exception:
                return None

        def _commit_and_move(step: int):
            nxt = _neighbor_path(step)
            finish(True)
            if nxt:
                QTimer.singleShot(60, lambda: self._rename_inline(nxt))

        class _KeyFilter(_QtCore.QObject):
            def eventFilter(_s, obj, ev):
                if ev.type() == _QtCore.QEvent.KeyPress:
                    k = ev.key()
                    if k == Qt.Key_Escape:
                        finish(False)
                        return True
                    if k in (Qt.Key_Return, Qt.Key_Enter):
                        # r72: ここで消費する。QLineEdit の returnPressed に任せると
                        # キーが親の QListView へ伝播して activated が発火し、
                        # _on_item_activated → _set_current_path でパス欄が
                        # フォルダへ戻る（テストで確定）。実機ではさらに
                        # 「Enter でファイルが開く／フォルダに入る」事故になる。
                        finish(True)
                        return True
                    if k == Qt.Key_Backtab or (k == Qt.Key_Tab and
                                               ev.modifiers() & Qt.ShiftModifier):
                        _commit_and_move(-1)
                        return True
                    if k == Qt.Key_Tab:
                        _commit_and_move(+1)
                        return True
                return False
        ed._mfm_keys = _KeyFilter(ed)
        ed.installEventFilter(ed._mfm_keys)

    def _on_fs_file_renamed(self, folder: str, old_name: str, new_name: str,
                            record: bool = True):
        """リネームの反映: パス欄を追従し、必要なら Undo 記録を積む。
        QFileSystemModel.fileRenamed（setData 経由）からは record=True、
        自前のインライン編集（os.rename 済み・記録済み）からは record=False。"""
        try:
            old_p = os.path.join(folder, old_name).replace("\\", "/")
            new_p = os.path.join(folder, new_name).replace("\\", "/")
            if self._addr_bar.text().replace("\\", "/") == old_p:
                self._addr_bar.setText(new_p)
            if record:
                from core.undo_stack import RenameOp
                self._undo_stack().push(RenameOp(old_p, new_p))
            self.status_message.emit(f"名前変更: {old_name} → {new_name}")
            # r98: 表示中のフォルダ自身／その祖先を変更した場合は新しい場所へ追従
            cur = (self._current_path or "").replace("\\", "/").rstrip("/")
            o = old_p.rstrip("/")
            if cur == o or cur.startswith(o + "/"):
                self.navigate_to(new_p + cur[len(o):])
                return
            # r98: リネーム後は «新しい名前の項目» を選択状態にする
            self._select_when_visible(os.path.join(folder, new_name))
        except Exception:
            pass

    def _rename_dialog(self, paths: List[str]):
        if len(paths) != 1:
            return
        old = Path(paths[0])
        new_name, ok = QInputDialog.getText(
            self, "名前変更", "新しい名前:", text=old.name
        )
        if ok and new_name:
            new_path = old.parent / new_name
            from core.undo_stack import RenameOp
            ok, err = self._rename_path(str(old), str(new_path))
            if not ok:
                QMessageBox.critical(self, "名前変更",
                                     self._rename_error_text(str(old), err))
                return
            try:
                self._undo_stack().push(RenameOp(str(old), str(new_path)))
                # r98: 選択・パス欄追従・ステータス表示は共通の後処理へ
                self._on_fs_file_renamed(str(old.parent), old.name, new_name,
                                         record=False)
            except Exception as e:
                _mfm_log("rename post error: %r" % (e,))

    def _delete_confirm(self, paths: List[str]):
        # 何を消すかを必ず見せる（誤対象＝パンくず等の事故防止）
        names = [os.path.basename(p.rstrip("/\\")) or p for p in paths]
        shown = "\n".join("  " + n for n in names[:10])
        if len(names) > 10:
            shown += "\n  …他 %d 件" % (len(names) - 10)
        msg = f"{len(paths)} 件を削除しますか？\n\n{shown}"
        ret = QMessageBox.warning(self, "削除の確認", msg,
                                  QMessageBox.Yes | QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return
        # r62: 削除は «同一ボリュームの作業ごみ箱» へ移動して Undo 可能にする
        # （作業ごみ箱へ移せないものは OS のごみ箱へ直接＝Undo 不可）。
        # ワーカー＋進捗ダイアログ。キャンセル時も完了分は Undo 記録に残す。
        from core.undo_stack import delete_to_work_trash, make_delete_op
        pairs = []

        def work(cb):
            return delete_to_work_trash(paths, progress_cb=cb, pairs=pairs)

        def done(res, cancelled, err):
            failed, direct = [], []
            if res is not None:
                _op, failed, direct = res
            op = make_delete_op(pairs)
            if op is not None:
                self._undo_stack().push(op)
            if err:
                QMessageBox.critical(self, "削除", err)
            elif failed:
                QMessageBox.warning(self, "削除エラー",
                                    f"{len(failed)} 件の削除に失敗しました:\n" +
                                    "\n".join(failed))
            elif cancelled:
                self.status_message.emit(f"削除をキャンセルしました（完了 {len(pairs) + len(direct)} 件）")
            else:
                hint = "（Ctrl+Z で元に戻せます）" if op is not None else ""
                self.status_message.emit(f"{len(paths)} 件を削除しました{hint}")
        self._run_file_op("削除", work, done, total=len(paths))

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
            (QKeySequence(Qt.Key_F2), lambda _c=False: self._rename_inline()),
            (QKeySequence.Undo, lambda _c=False: self._undo_op()),
            (QKeySequence("Ctrl+Y"), lambda _c=False: self._redo_op()),
            (QKeySequence("Ctrl+D"), lambda _c=False: self._duplicate_selected()),
            (QKeySequence("Ctrl+Shift+N"), lambda _c=False: self._create_new_item("folder")),
            (QKeySequence("Ctrl+Shift+T"), lambda _c=False: self._create_new_item("text")),
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

    # ── 長時間になり得るファイル操作の共通経路（r62） ─────────────────────
    # 方針: コピー/移動/削除/Undo/Redo は必ずワーカースレッドで実行し、
    # 0.4 秒を超えたら進捗ダイアログ（キャンセル付き）を出す。UI スレッドで
    # shutil を回してフリーズさせない。キャンセル時も «完了した分» は
    # Undo 記録に残す（部分完了を黙って捨てない）。
    class _OpCancelled(Exception):
        pass

    def _run_file_op(self, title: str, work, on_done=None, total: int = 0):
        """work(progress_cb) をワーカーで実行。progress_cb(i, n) はワーカーから
        呼ばれ、キャンセル済みなら _OpCancelled を投げて中断させる。
        on_done(result, cancelled: bool, error: str) は UI スレッドで呼ばれる。"""
        from core.compat import QProgressDialog
        notifier = _FileOpNotifier(self)
        dlg = QProgressDialog(title, "キャンセル", 0, max(0, int(total)), self)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(400)      # 短い操作ではダイアログを出さない
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        state = {"cancel": False, "done": False}
        dlg.canceled.connect(lambda: state.__setitem__("cancel", True))
        t0 = _time_mod.monotonic()

        def cb(i, n):
            notifier.progress.emit(int(i), int(n))
            if state["cancel"]:
                raise BrowserPanel._OpCancelled()

        def run():
            try:
                res = work(cb)
                notifier.finished.emit(res, "")
            except BrowserPanel._OpCancelled:
                notifier.finished.emit(None, "__cancel__")
            except Exception as e:
                notifier.finished.emit(None, str(e) or e.__class__.__name__)

        def on_progress(i, n):
            if n > 0 and dlg.maximum() != n:
                dlg.setMaximum(n)
            dlg.setValue(i)
            dlg.setLabelText("%s  (%d / %d)" % (title, i, n) if n > 0 else title)

        # 項目数が少なく1件が重い（巨大ファイル）時も、0.4秒経ったら必ず出す
        tick = QTimer(self)
        tick.setInterval(200)

        def on_tick():
            if state["done"]:
                return
            if _time_mod.monotonic() - t0 > 0.4 and not dlg.isVisible():
                dlg.show()
        tick.timeout.connect(on_tick)
        tick.start()

        def done(res, err):
            state["done"] = True
            tick.stop()
            try:
                dlg.close()
                dlg.deleteLater()
                notifier.deleteLater()
            except Exception:
                pass
            cancelled = (err == "__cancel__")
            if on_done:
                try:
                    on_done(res, cancelled, "" if cancelled else err)
                except Exception as e:
                    _mfm_log("file op on_done error: %r" % (e,))
            elif err and not cancelled:
                QMessageBox.critical(self, title, err)
            # r72: 平坦ビューは静的モデル（QStandardItemModel）なので、削除/移動/
            # Undo 後に自分で作り直す（放置すると「削除できない」ように見える）
            self._refresh_flat_view()
        notifier.progress.connect(on_progress)
        notifier.finished.connect(done)
        threading.Thread(target=run, daemon=True, name="mfm-file-op").start()

    def _refresh_flat_view(self):
        try:
            fc = self._flat_col
            if fc.isVisible():
                fc.set_sources(list(fc._sources))
                self._request_flat_integration_status()
        except Exception as e:
            _mfm_log("flat refresh error: %r" % (e,))

    # ── Undo / Redo（r62、Explorer 相当: 名前変更・移動・コピー・削除） ─────
    def _undo_stack(self):
        from core.undo_stack import get_undo_stack
        return get_undo_stack()

    def _undo_op(self):
        st = self._undo_stack()
        if not st.can_undo():
            self.status_message.emit("元に戻す操作はありません")
            return
        label = st.undo_label()

        def done(res, cancelled, err):
            if err:
                QMessageBox.warning(self, "元に戻す", "元に戻せませんでした:\n%s" % err)
            else:
                self.status_message.emit("元に戻しました: " + label)
        self._run_file_op("元に戻す: " + label, lambda cb: st.undo(), done)

    def _redo_op(self):
        st = self._undo_stack()
        if not st.can_redo():
            self.status_message.emit("やり直す操作はありません")
            return
        label = st.redo_label()

        def done(res, cancelled, err):
            if err:
                QMessageBox.warning(self, "やり直す", "やり直せませんでした:\n%s" % err)
            else:
                self.status_message.emit("やり直しました: " + label)
        self._run_file_op("やり直す: " + label, lambda cb: st.redo(), done)

    # ------------------------------------------------------------------
    # カラムへのファイルドロップ（r87）
    # ------------------------------------------------------------------

    def _on_files_dropped(self, paths: List[str], dest_dir: str, move: bool):
        """カラム間 D&D の実行。移動（既定）／コピー（Ctrl）。
        同名が既にある時は «上書き / 名前を変えて / スキップ» を選ばせる。"""
        from core.i18n import tr
        from core.file_operations import (move_items, copy_items,
                                          CONFLICT_RENAME, CONFLICT_SKIP)
        from core.undo_stack import MoveOp, CopyOp

        dest_dir = os.path.normpath(dest_dir)
        if not os.path.isdir(dest_dir):
            self.status_message.emit(tr("移動先が見つかりません", "Destination not found"))
            return
        # 自分自身・自分の中への移動、同じ場所への移動は捨てる
        srcs = []
        dn = os.path.normcase(dest_dir)
        for p in paths:
            if not p or not os.path.lexists(p):
                continue
            pn = os.path.normcase(os.path.normpath(p))
            if pn == dn:
                continue
            if os.path.isdir(p) and dn.startswith(pn + os.sep):
                self.status_message.emit(
                    tr("フォルダを自分の中へは移動できません: %s",
                       "Cannot move a folder into itself: %s") % os.path.basename(p))
                continue
            if move and os.path.normcase(os.path.dirname(pn)) == dn:
                continue      # 同じフォルダ内への移動は何もしない
            srcs.append(p)
        srcs = CappedColumnView._drop_ancestor_dirs(srcs)
        if not srcs:
            return

        # 衝突の解決。ダイアログはワーカーから呼べないので «先に» まとめて聞く。
        from ui.conflict_dialog import ConflictDialog
        decisions = {}
        sticky = None
        pending = [p for p in srcs
                   if os.path.lexists(os.path.join(dest_dir, os.path.basename(p)))]
        for i, p in enumerate(pending):
            if sticky is not None:
                decisions[os.path.normcase(p)] = sticky
                continue
            dlg = ConflictDialog(p, os.path.join(dest_dir, os.path.basename(p)),
                                 remaining=len(pending) - i - 1, parent=self.window())
            ok = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
            if not ok or dlg.choice() is None:
                self.status_message.emit(tr("移動を中止しました", "Move cancelled"))
                return
            decisions[os.path.normcase(p)] = dlg.choice()
            if dlg.apply_to_all():
                sticky = dlg.choice()

        def conflict_cb(src, dest):
            return decisions.get(os.path.normcase(src), CONFLICT_RENAME)

        # 全部スキップなら何もしない
        if pending and all(v == CONFLICT_SKIP for v in decisions.values()) \
           and len(pending) == len(srcs):
            self.status_message.emit(tr("移動する項目がありません", "Nothing to move"))
            return

        pairs: List[tuple] = []
        results: List[str] = []
        label = tr("移動", "Move") if move else tr("コピー", "Copy")

        def work(cb):
            if move:
                move_items(srcs, dest_dir, progress_cb=cb, results=results,
                           conflict_cb=conflict_cb, pairs=pairs)
            else:
                copy_items(srcs, dest_dir, progress_cb=cb, results=results)
            return results

        def done(res, cancelled, err):
            if move and pairs:
                self._undo_stack().push(MoveOp(pairs))
            elif not move and results:
                self._undo_stack().push(
                    CopyOp([(s, d) for s, d in zip(srcs, results)]))
            if err:
                QMessageBox.critical(self, label, err)
            elif cancelled:
                self.status_message.emit(
                    tr("%s をキャンセルしました（完了 %d 件）",
                       "%s cancelled (%d done)") % (label, len(results)))
            else:
                self.status_message.emit(
                    tr("%s: %d 件 → %s（Ctrl+Z で元に戻せます）",
                       "%s: %d item(s) → %s (Ctrl+Z to undo)")
                    % (label, len(results), dest_dir))
            self._refresh_flat_view()

        self._run_file_op(label, work, done, total=len(srcs))

    def _duplicate_selected(self, paths: List[str] = None):
        """Ctrl+D / 右クリック「複製」: 同じ場所へ «元名_Copy» で複製（r67）。
        ダイアログで名前 or Search/Replace（＋サブフォルダ以下へ適用）を指定。
        ワーカー＋進捗、Undo は CopyOp（複製先を消す）。"""
        paths = list(paths) if paths else self._operation_targets()
        paths = [p for p in paths if p and os.path.lexists(p)]
        if not paths:
            self.status_message.emit("複製する項目を選択してください")
            return
        from ui.duplicate_dialog import DuplicateDialog
        from core.file_operations import duplicate_items
        from core.undo_stack import CopyOp
        dlg = DuplicateDialog(paths, parent=self.window())
        if (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()) != QDialog.Accepted:
            return
        specs = dlg.specs()
        inside = dlg.rename_inside()
        results: List[str] = []

        def work(cb):
            duplicate_items(specs, progress_cb=cb, results=results, rename_inside=inside)
            return results

        def done(res, cancelled, err):
            pairs = [(s, d) for (s, d) in specs if d in results]
            if pairs:
                self._undo_stack().push(CopyOp(pairs))
            if err:
                QMessageBox.critical(self, "複製", err)
            elif cancelled:
                self.status_message.emit("複製をキャンセルしました（完了 %d 件）" % len(results))
            else:
                self.status_message.emit("複製: %d 件（Ctrl+Z で元に戻せます）" % len(results))
        self._run_file_op("複製", work, done, total=len(specs))

    def _operation_targets(self) -> List[str]:
        """キーボード操作（Ctrl+C/X/V, Delete）の対象。
        本体の選択にはパンくず（上位カラムで選択表示されている祖先フォルダ）が
        含まれるため、右クリックと同じく _drop_ancestor_dirs で除外する
        （r59。除外しないと Delete キーで祖先フォルダごと削除対象になる）。"""
        try:
            # r72: 平坦ビュー／共通子フォルダカラムにフォーカスがある時は、
            # その «最下層の選択物» だけを対象にする。従来は本体（カラム）の
            # 選択＝平坦表示の元になった上位フォルダが対象になり、平坦ビューで
            # Delete を押すと上のフォルダが削除対象になっていた（指摘）。
            deep = self._deep_view_targets()
            if deep is not None:
                return deep
            return CappedColumnView._drop_ancestor_dirs(self._get_selected_paths())
        except Exception:
            return []

    def _deep_view_targets(self) -> Optional[List[str]]:
        """フォーカスが平坦ビュー／共通子フォルダカラムにあれば、その選択パス
        （共通カラムは選択項目の実フォルダ群）。無ければ None（本体の選択へ）。"""
        try:
            fw = QApplication.focusWidget()
            if fw is None:
                return None
            fc = getattr(self, "_flat_col", None)
            if fc is not None and fc.isVisible() and (fc is fw or fc.isAncestorOf(fw)):
                return [p for p in fc.selected_paths() if p]
            for col in getattr(self, "_common_cols", []) or []:
                if col.isVisible() and (col is fw or col.isAncestorOf(fw)):
                    return [p for p in col.selected_sources() if p]
        except Exception:
            pass
        return None

    def _clipboard_copy(self):
        self._set_clipboard(self._operation_targets(), move=False)

    def _clipboard_cut(self):
        self._set_clipboard(self._operation_targets(), move=True)

    def _paste_target_dir(self) -> Optional[str]:
        """貼り付け先: 単一フォルダ選択中ならそのフォルダ、無ければ現在地。
        r70: 空白右クリックの「貼り付け」はそのカラムのフォルダ。"""
        ov = getattr(self, "_paste_override_dir", None)
        if ov and os.path.isdir(ov):
            return ov
        sel = self._operation_targets()
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

        if move:
            QApplication.clipboard().clear()  # 切り取りは1回限り
        self._transfer_with_progress(paths, target, move,
                                     "移動" if move else "貼り付け")

    def _transfer_with_progress(self, paths: List[str], target: str, move: bool, verb: str):
        """コピー/移動をワーカー＋進捗ダイアログで実行し、Undo 記録を積む（r62）。
        キャンセル時も完了した分は Undo 可能な記録として残す。"""
        from core.undo_stack import MoveOp, CopyOp
        results: List[str] = []
        fn = move_items if move else copy_items
        # r98: «Manager で開いているフォルダ» は監視ハンドルのせいで Windows では
        # 移動できない。フォルダが対象に含まれる移動の前に手放しておく。
        if move:
            try:
                for pth in paths:
                    if os.path.isdir(pth):
                        self._release_fs_handles(pth, level=1)
                        break
            except Exception:
                pass

        def work(cb):
            fn(paths, target, progress_cb=cb, results=results)
            return results

        def done(res, cancelled, err):
            pairs = list(zip(paths, results))
            if pairs:
                self._undo_stack().push(MoveOp(pairs) if move else CopyOp(pairs))
            if err:
                QMessageBox.critical(self, verb, err)
            elif cancelled:
                self.status_message.emit(f"{verb}をキャンセルしました（完了 {len(results)} 件）")
            else:
                self.status_message.emit(f"{verb}完了: {len(results)} 件")
            self._check_current_gone_async()          # r98/r107
        self._run_file_op(verb, work, done, total=len(paths))

    def _clipboard_delete(self):
        paths = self._operation_targets()
        if paths:
            self._delete_confirm(paths)

    # ------------------------------------------------------------------
    # Thumbnail refresh
    # ------------------------------------------------------------------

    def _on_thumbnail_ready(self, path: str, pixmap: "QPixmap"):
        """Force repaint when a thumbnail arrives.
        r84: カラムをサムネイル表示にした場合も再描画する（従来は
        _thumb_view だけで、カラム側は差し替わらなかった）。"""
        if self._thumb_view.isVisible():
            self._thumb_view.viewport().update()
        try:
            for v in self._column_view.findChildren(QListView):
                if getattr(v, "_mfm_view_mode", "list") == "thumb":
                    v.viewport().update()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _list_visible_files(self, directory: str) -> List[str]:
        """先読み対象のファイル一覧。クラウドの «オンラインのみ» は
        除外する（読むとダウンロードが走るため。r83）。"""
        from core.cloud_state import is_online_only
        try:
            out = []
            for f in os.listdir(directory):
                p = os.path.join(directory, f)
                if not os.path.isfile(p):
                    continue
                if is_online_only(p):
                    continue
                out.append(p)
                if len(out) >= 64:      # Prefetch at most 64 files
                    break
            return out
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

    def set_dnd_callback(self, cb):
        """DCC（Maya/Blender）へのD&D検出時コールバック cb(action, paths, app) を
        登録し、カラムビューのドロップ検出を有効化する。"""
        self._dnd_callback = cb
        self._column_view._maya_drop_cb = self._handle_maya_drop

    def set_dcc_callback(self, cb):
        """右クリックの「Maya へ…」「Blender へ…」用 cb(app, action, paths)（r65）。"""
        self._dcc_callback = cb

    def _handle_maya_drop(self, paths, app: str = "maya"):
        """Maya/Blender のウィンドウへD&Dされた → 設定されたD&D動作を実行する。
        r90: Maya へ落とした .py / .mel は «Maya と同じくスクリプトとして実行»
        （install.py 等。ブリッジ経由なので Manager は固まらない）。"""
        action = self._sm.get("dnd_action", "none")
        _mfm_log("dcc-drop: app=%s action=%s paths=%d" % (app, action, len(paths)))
        cb = getattr(self, "_dnd_callback", None)
        if not callable(cb):
            return
        scripts, others = [], []
        for p in paths:
            (scripts if (app == "maya" and os.path.splitext(p)[1].lower()
                         in (".py", ".mel")) else others).append(p)

        def _call(act, ps):
            try:
                cb(act, ps, app)
            except TypeError:
                cb(act, ps)
        if scripts:
            _call("run_script", scripts)
        if others and action != "none":
            _call(action, others)

    def set_dcc_takeover_callback(self, cb):
        """DCC への落下を Manager が引き受けるかの判定 cb(paths, app)（r90）。"""
        self._column_view.set_dcc_takeover_callback(cb)

    def set_max_depth(self, depth: int):
        """深度キャップ（ルート自動シフト）は廃止済み（init参照）。
        設定ダイアログ適用時に旧設定値で再有効化されると、setRootIndex による
        全カラム再構築がクリック毎に走り「カラムが1本だけになる／半端な位置で
        切れる」原因になった（r54 で確定）。値は保存するが動作には反映しない。"""
        self._sm.set("column_max_depth", depth)

    def set_thumb_size(self, size: int):
        self._thumb_delegate._thumb_size = size
        self._thumb_view.setGridSize(QSize(size + 16, size + 32))
        self._thumb_view.setItemDelegate(self._thumb_delegate)
        self._thumb_mgr.set_thumb_size(size)
        self._sm.set("thumbnail_size", size)

    def current_path(self) -> str:
        return self._current_path


# ファイル末尾センチネル: 起動ログにこの行が出れば、このファイルは
# 末尾まで欠損なく読み込まれている（ファイル同期の切り詰め検出用）。
_mfm_log("browser_panel.py loaded to EOF (r110 complete)")
