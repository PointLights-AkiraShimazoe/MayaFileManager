# -*- coding: utf-8 -*-
"""Browser Panel
=============
ファイルブラウザ本体（BrowserPanel）。

r110 でモジュールを分割した。旧 browser_panel.* の名前は互換のため
ここで再エクスポートしているので、既存の import は変更不要。"""
from core.diag import swallow as _swallow  # r112
from core.diag import diag_off as _diag_off  # r146 切り分け用
from core.i18n import tr  # r118: モジュールレベルで提供（ローカル import 不要）

import os
from core import fast_stat as _fs  # r146: UI スレッドで待たない stat
import struct
import threading
from pathlib import Path
from typing import List, Optional, Callable

from core.compat import (
    Qt, Signal, QObject,
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QLineEdit, QToolButton,
    QSplitter, QListView,
    QSizePolicy, QFrame, QAbstractItemView, QFileSystemModel, QMenu, QAction, QMessageBox, QFileDialog, QInputDialog, QDialog,
    QModelIndex, QSize, QRect, QPixmap, QDir, QFileInfo, QUrl, QMimeData, QPoint,
    QTimer, QKeySequence, QDrag, )
from core.compat import QtCore as _QtCore
from core.compat import is_alive
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
# r146: ここを «メソッドの中だけ» で import していたため、_copy_dialog /
# _move_dialog が NameError で無反応になっていた（r145 の取りこぼし）。
# 共通ダイアログのヘルパはモジュール先頭で取り込む。
from ui.dialog_util import pick_directory, pick_open_file  # noqa: F401
from core.file_operations import (
    open_with_default_app, reveal_in_explorer,
    copy_items, move_items, get_file_type_category, format_size,
    resolve_windows_shortcut,
    is_read_only, set_read_only,
    OperationCancelled as _OperationCancelled,
    MAYA_EXTENSIONS
)
from core.thumbnail_generator import ThumbnailManager

from ui.dcc_header import glyph_icon, integration_icon  # r142: アイコン列を揃える
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


from ui.browser_fileops_mixin import FileOpsMixin
from ui.browser_rename_mixin import RenameMixin
from ui.browser_context_menu_mixin import ContextMenuMixin


class BrowserPanel(FileOpsMixin, RenameMixin, ContextMenuMixin, QWidget):
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
        try:
            from core.version import version_string
            _mfm_timeline("MayaFileManager %s" % version_string())
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:103 __init__")
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
        # r146（ユーザー指示 2026-10-14）: ディレクトリとファイル名を別の欄に分け、
        # 右端にフルパスのコピーボタンを置く。呼び出し側から見た顔
        # （setText / text / returnPressed）は今までと同じなので、
        # 既存の 10 箇所以上の setText はそのまま動く。
        from ui.address_bar import AddressBar
        # 設定を渡すのは «ディレクトリ｜ファイル名» の分割位置を覚えるため。
        self._addr_bar = AddressBar(settings_manager=self._sm)
        self._addr_bar.returnPressed.connect(
            lambda: self._navigate(self._addr_bar.text()))
        self._addr_bar.copied.connect(
            lambda full: self.status_message.emit(
                tr("フルパスをコピー: %s", "Copied the full path: %s") % full))
        tb_layout.addWidget(self._addr_bar, 1)

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
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:252 _build_ui")
        # フォルダ列挙の高速化: desktop.ini によるカスタムフォルダアイコンの
        # シェル照会を無効化（フォルダ毎のシェルI/Oが減り、大きなツリーや
        # ネットワーク先で初回列挙が大幅に速くなる。表示は標準アイコンになる）
        try:
            self._fs_model.setOption(
                QFileSystemModel.DontUseCustomDirectoryIcons, True)
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:260 _build_ui")
        # 【最重要】リンク解決の無効化。Qt の filePath()/列挙は、行が
        # symlink/ジャンクションだと canonicalFilePath でリンク先を «実際に»
        # 解決する。リンク先が到達不能なネットワークだと SMBタイムアウト
        # （約21秒）がUIスレッド/列挙スレッドで発生し、ナビ毎に21秒フリーズ・
        # 起動63秒停滞の直接原因になった（mfm_freeze.log で特定。2026-09）。
        # 本ツールは設計上リンクを実体へ解決しない（リンクのパスのまま扱う）
        # ため、この解決処理自体が不要。
        try:
            self._fs_model.setResolveSymlinks(False)
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:271 _build_ui")
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
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:294 _build_ui")
        # 保存済みのカラム幅を適用（未保存なら既定幅300）
        self._column_view.set_settings(self._sm)
        # r120: カラムのスライドアニメーション（設定で切れる）
        self._column_view.set_slide_animation(
            bool(self._sm.get("column_slide_animation", True)))
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
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:321 _build_ui")
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
        # r125: ✕ は «hide するだけ» だった。平坦カラムと一緒に出る
        # 共通フォルダカラム（_common_cols）・平坦トグル・パス欄が
        # 取り残され、「✕ で消すと子フォルダカラムが残る」になっていた
        # （ユーザー報告 2026-10-06）。書庫カラムの ✕ と同じく、
        # «閉じる» の後片付けを一本化した経路を通す。
        self._flat_col.closed.connect(lambda: self._on_flat_request([]))
        # r120: 平坦カラムでもサムネイル表示を選べるようにする（ユーザー指示）。
        # 平坦カラムは «フォルダ» を持たないので、記憶はフォルダ別ではなく 1 つ。
        self._flat_col.set_thumb_mgr(self._thumb_mgr)
        self._flat_col.set_thumb_prefetch_callback(
            lambda files: self._thumb_mgr.prefetch(list(files or [])[:400]))
        self._flat_col.set_item_size(
            int(self._sm.get("column_icon_size_list", 16)), "list")
        self._flat_col.set_item_size(
            int(self._sm.get("column_icon_size_thumb", 96)), "thumb")
        self._flat_col.view_mode_changed.connect(
            lambda m: self._sm.set("flat_view_mode", m))
        self._flat_col.set_view_mode(
            str(self._sm.get("flat_view_mode", "list") or "list"))
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
        # r146: «その中身がドライブ最上位から列挙できない» と分かったリンク。
        # ここに入ったものだけ、そのリンクをカラムのルートにする（退避路）。
        self._link_own_root = set()
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
            self._start_integration_auto_refresh(_mgr)
        except Exception as _e:
            # r121: **黙って無効にしない。** 以前はここで握り潰していたため、
            # EXE 版で core.integrations の import が失敗していること
            # （spec の excludes に 'xml' を入れていたのが原因）に気付けず、
            # 「開発版では出るバッジとメニューがリリース版だけ出ない」
            # という状態のまま配布してしまった（2026-10-05 実機）。
            # 画面に出し、ログにも «連携が丸ごと死んでいる» と明記する。
            import traceback
            _mfm_log("integrations start error: %r\n%s"
                     % (_e, traceback.format_exc()))
            self._integrations_error = str(_e)
            try:
                self.status_message.emit(tr(
                    "外部サービス連携を開始できませんでした（%s）"
                    "— Git/SVN/Perforce/クラウドのバッジと右クリックは出ません",
                    "Could not start integrations (%s) - Git/SVN/Perforce/"
                    "Cloud badges and menu entries will not appear") % _e)
            except Exception as _e2:
                _swallow(_e2, "ui/browser_panel.py integrations start notice")

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
        if self._quick_look and self._quick_look.isVisible() and _fs.isfile(path):
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
        # r120: 「"C:\\...\\x.ma"」のように «パスのコピー» で付く引用符は外す。
        # Explorer のアドレス欄と同じく、貼り付けただけで通るようにする。
        path = (path or "").strip()
        if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
            path = path[1:-1].strip()
        if not path:
            return
        path = os.path.normpath(path)
        # r120: **副作用はここでは起こさない。** 入力が «ファイル» だった場合は
        # 関連付けられたプログラムで開くだけで、カラムは一切動かさない
        # （ユーザー指示 2026-10-04）。選択の解除や平坦カラムを閉じる処理を
        # ここで先にやってしまうと、ファイルを開いただけで画面が動く。
        # 到達確認（_prober）の後、_navigate_now の先頭でまとめて行う。
        self._pending_nav = (path, add_to_history)
        self.status_message.emit(tr("確認中: %s", "Checking: %s") % path)
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
        # r120: フルパスでファイルを指定したら «関連付けられたプログラムで開く»
        # （Explorer のアドレス欄と同じ挙動。ユーザー指示 2026-10-04）。
        # 移動は行わず、アドレス欄も直前のパスへ戻すので、カラムは動かない。
        # isfile はここで初めて呼ぶ。到達確認が済んでいるので、切断ドライブで
        # 20 秒級にブロックする心配がない（path_guard の原則）。
        try:
            is_file = os.path.isfile(path)
        except OSError:
            is_file = False
        if is_file:
            self._open_path_externally(path)
            return
        self._navigate_now(path, add_to_history)

    def _open_path_externally(self, path: str):
        """アドレス欄に入れられたファイルを関連付けで開き、表示は元に戻す。"""
        name = os.path.basename(path)
        try:
            open_with_default_app(path)
        except Exception as e:
            _mfm_log("addr-bar open failed: %r %r" % (path, e))
            self.status_message.emit(
                tr("開けませんでした: %s（%s）", "Could not open %s (%s)")
                % (name, e))
        else:
            self.status_message.emit(
                tr("開きました: %s", "Opened: %s") % name)
        # アドレス欄は «直前に入っていたパス» へ戻す（表示は動かさない）
        try:
            self._addr_bar.setText(self._current_path or "")
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _open_path_externally")

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
        # r146: 打ちかけの名前変更があれば、移る前に確定する（取り残さない）
        try:
            self.commit_inline_edit()
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _navigate_now(commit rename)")
        # ナビゲーションは標準挙動どおり複数選択をリセットする
        # （ブックマーク・履歴・▲・ショートカット追従すべて共通）
        # r120: «実際に移動すると決まってから» 行う。_navigate の時点でやると、
        # アドレス欄にファイルを入れただけで平坦カラムが閉じてしまう。
        try:
            self._column_view._clear_multi_state()
            if self._flat_col.isVisible():
                self._on_flat_request([])
            if self._archive_col.isVisible():
                self._on_archive_request("")        # r97
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:511 _navigate")
        # r83: 新しい移動なので «選択維持ガード» を解除し、自己修復を許可する
        try:
            self._column_view._mfm_user_selected = False
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:547 _navigate_now")
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

        is_dir = _timed("isdir", lambda: _fs.isdir(path))
        target_dir = path if is_dir else str(Path(path).parent)

        # 経路上の隠しフォルダ(AppData等)を強制表示にしてカラムチェーンを構築可能にする
        _timed("apply_force_visible", lambda: self._apply_force_visible(path))

        # QFileSystemModel に対象ディレクトリまでのチェーンを能動的にロードさせる。
        # これが無いと、別ドライブ/深い/隠し経由のパスは index が無効のままで
        # カラムが構築されない（クリックしてもカラムが伸びない原因）。
        try:
            _timed("setRootPath", lambda: self._fs_model.setRootPath(target_dir))
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:578 _timed")

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

        # r146: リンクの中身がカラムに出たかを確かめる。
        # 実機の Windows ではジャンクションの配下がドライブ最上位ルートからは
        # 列挙されないことが分かった（回帰スイート 2026-10-14: linkdir の中身が
        # 出ずに Temp の中身が並んだ）。確認を «クリックで辿った時» だけに
        # 掛けていたのが誤りで、アドレス欄・ブックマーク・履歴から入ると
        # 中身が出ないままだった。移動のたびに確かめる。
        self._verify_link_listing(path)

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
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:626 _any_thumb_column")
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
        """カラムビューのルートを決める。

        r146（ユーザー報告 2026-10-13）:
          「シンボリックリンクやジャンクションを入ると、遷移前のカラムが
            消えていて、カラムを左に移動出来なくなっています。」

        r145 までは «経路にリンクが 1 つでもあれば、その最上位リンクをルートに
        する» という作りだった。QColumnView のルートより左にはカラムが作れない
        ので、ジャンクションへ入った瞬間に **その親から上のカラムが全部消え、
        左へ戻る手段が無くなる**。構造上の行き止まりで、仕様として許容できない。

        r146 は **既定でドライブ最上位をルートにする**。リンクの中身が本当に
        列挙できなかった時だけ、そのリンクをルートにする退避路へ落ちる
        （_note_link_needs_own_root。古い docstring が言っていた «列挙できない
        環境» のための道で、普段は通らない）。
        """
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
            # 退避路に登録されたリンクだけ、そこをルートにする（既定は通らない）
            if self._norm(comp) not in getattr(self, "_link_own_root", ()):
                continue
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

    # ── リンクの中身が出なかった時の退避路（r146）──────────────────
    #
    # 【前提】普通の Windows のジャンクション／シンボリックリンクは、
    # ドライブ最上位をルートにしたままでも中身がちゃんとカラムに出る
    # （2026-10-14 に実機で確認済み）。この退避路は «出ない環境» のための
    # 保険であって、**普段は絶対に通ってはいけない道**。
    #
    # ここが誤爆すると、リンクをカラムのルートにしてしまい、
    # «リンクへ入ると左のカラムが消えて戻れない» という、本来直したかった
    # 不具合そのものに戻る。だから判定は «疑わしきは何もしない» で組む。
    LINK_VERIFY_MS = 900          # 1 回目の確認までの待ち
    LINK_VERIFY_TRIES = 6         # これだけ連続で «空» なら初めて退避する
                                  #（900ms × 6 ＝ 約 5 秒。経路に数千件の
                                  #  フォルダがあると読み込みはこれくらい
                                  #  平気でかかる。実機の Temp は 3,484 件）

    def _deepest_link_in(self, path: str) -> str:
        """path の経路にあるリンクのうち、いちばん深いもの（無ければ ""）。"""
        p = os.path.normpath(os.path.abspath(path or ""))
        while p:
            try:
                if self._is_symlink_or_junction(p):
                    return p
            except OSError:
                pass
            parent = os.path.dirname(p)
            if not parent or parent == p:
                break
            p = parent
        return ""

    def _verify_link_listing(self, path: str, tries: int = 0):
        """リンクへ入った後、その中身がカラムに出ているかを確かめる（r146）。

        何度確かめても出なければ «ドライブ最上位からは列挙できない» 環境と
        みなし、そのリンクをルートにして開き直す。登録はこのセッションの間
        だけ覚える。判定の条件は厳しめ（下のコメント参照）。"""
        link = self._deepest_link_in(path)
        if not link or self._norm(link) in getattr(self, "_link_own_root", set()):
            return

        def _check():
            try:
                if self._norm(self._current_path or "") != self._norm(path):
                    return                     # もう別の場所にいる
                try:
                    real = [n for n in os.listdir(link)]
                except OSError:
                    return                     # 読めない＝リンク切れ。別の話
                if not real:
                    return                     # 空フォルダなら出なくて当然
                src = self._fs_model.index(link)
                # まだモデルに «その節» が無い＝読み込みがそこまで来ていない
                # だけ。出ていない証拠にはならない。
                if not src.isValid():
                    return self._verify_link_listing(path, tries + 1)
                if self._fs_model.rowCount(src) > 0:
                    return                     # 出ている＝退避路は要らない
                # canFetchMore が立っている＝これから読む。待てば出る。
                if self._fs_model.canFetchMore(src):
                    return self._verify_link_listing(path, tries + 1)
                if tries + 1 < self.LINK_VERIFY_TRIES:
                    return self._verify_link_listing(path, tries + 1)
                self._link_own_root.add(self._norm(link))
                _mfm_log("link: ドライブ最上位から列挙できないのでリンクを"
                         "ルートにして開き直す %r" % (link,))
                self._navigate_now(path, add_to_history=False)
            except Exception as e:
                _mfm_log("link verify error: %r" % (e,))
        if tries >= self.LINK_VERIFY_TRIES:
            return
        QTimer.singleShot(self.LINK_VERIFY_MS, _check)

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
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:740 _apply_force_visible")

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
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:767 _prime_path_loading")
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
                    except Exception as _e:
                        _swallow(_e, "ui/browser_panel.py:853 _advance_pending_load")
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
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:902 _force_column_rebuild")
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
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:939 _force_column_rebuild")
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
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:953 _force_column_rebuild")
            # ── 診断＋自己修復 ─────────────────────────────────────
            # Windows実機のpip版Qtで «リンクをルートにすると直下がproxy越しに
            # 見えない／カラムが構築されない» 事象への対策。
            rrows = self._proxy.rowCount(ridx) if ridx.isValid() else -1
            n_cols = 0
            try:
                n_cols = sum(1 for v in self._column_view.findChildren(QListView)
                             if not v.isHidden() and v.model() is not None)
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:963 _force_column_rebuild")
            _mfm_log("force_rebuild: target=%r root_valid=%s cur_valid=%s "
                     "hbar_max=%d proxy_root_rows=%s visible_cols=%d"
                     % (target, ridx.isValid(), cidx.isValid(),
                        self._column_view.horizontalScrollBar().maximum(),
                        rrows, n_cols))
            if ridx.isValid() and rrows == 0:
                # 1) プロキシのフィルタ再評価で子のマッピングを作り直す
                try:
                    self._proxy.invalidateFilter()
                except Exception as _e:
                    _swallow(_e, "ui/browser_panel.py:974 _force_column_rebuild")
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
            self._drive_combo.addItem(
                root if ok else tr("%s (応答なし)", "%s (not responding)") % root,
                root)
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
            if _fs.islink(path):      # symlink（最終要素のみ・ターゲット未接続でもTrue）
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
        リンクのパス(例: C:\\...\\Projects\\Foo)のまま中身を表示する。
        通常フォルダのネイティブ列展開ではプロキシが子を返さないため、
        リンクは setRootIndex 方式の _navigate で開く（パスは維持される）。
        リンク先が未接続で到達不能な場合は _navigate 側がステータスに通知する。

        r146: ルートはドライブ最上位のままにして «左のカラム» を残す。
        中身が出ない環境だった時だけ、後からリンクをルートにして開き直す
        （確認は _navigate_now が «移動のたび» に行う）。
        """
        self._navigate(path)

    # ------------------------------------------------------------------
    # r124: «今いる階層より上» へ戻る／複数選択のカラムを畳む
    # ------------------------------------------------------------------
    @staticmethod
    def _norm(p: str) -> str:
        try:
            return os.path.normcase(os.path.normpath(p))
        except Exception:
            return p or ""

    def _is_at_or_above_current(self, path: str) -> bool:
        """path が «現在地そのもの» か «現在地の祖先» か。"""
        cur = self._norm(self._current_path or "")
        tgt = self._norm(path or "")
        if not cur or not tgt:
            return False
        return cur == tgt or cur.startswith(tgt + os.sep)

    def _has_multi_columns(self) -> bool:
        """複数選択で出した «平坦カラム／共通フォルダカラム» が残っているか。"""
        try:
            if self._flat_col.isVisible():
                return True
        except Exception:
            pass
        return bool([c for c in (getattr(self, "_common_cols", None) or [])])

    def _close_multi_columns(self):
        """複数選択のカラムを畳む。

        r124: 以前は «平坦カラムが見えている時» だけ後片付けしていたため、
        平坦カラムだけ閉じて «共通フォルダカラム» が取り残されていた
        （ユーザー報告 2026-10-06「複数選択をやめたのに一部残る」）。
        どちらか一方でも残っていれば両方畳む。"""
        if self._has_multi_columns():
            self._on_flat_request([])

    def _collapse_to(self, path: str, proxy_index=None):
        """path の階層まで戻る（その下のカラムを閉じる）。

        r124: «子フォルダに入った後、選択済みの親フォルダをもう一度クリック
        したら親階層に戻りたい。親階層の別フォルダを選ばないと戻れないのは
        おかしい»（ユーザー指示 2026-10-06）。
        Qt 任せ（current の変更で closeColumns が走る）に頼らず、ここで
        «戻る» を明示的に行う。平坦カラム・共通フォルダカラム・書庫カラムも
        畳む（これらが残っていると «戻った感じ» がしない）。"""
        cv = self._column_view
        try:
            cv._cancel_reclick_rename()     # «もう一度クリック»＝名前変更ではない
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _collapse_to(cancel rename)")
        self._merge_panel.hide()
        self._on_archive_request("")
        self._close_multi_columns()
        try:
            idx = proxy_index
            if idx is None or not idx.isValid():
                idx = cv._proxy_index_for_path(path)
            if idx is not None and idx.isValid():
                QISM = _QtCore.QItemSelectionModel
                sm = cv.selectionModel()
                if sm is not None:
                    # ClearAndSelect で current を動かす＝Qt が下のカラムを閉じる
                    sm.setCurrentIndex(idx, QISM.ClearAndSelect | QISM.Rows)
                cv.scrollTo(idx)
        except Exception as e:
            _mfm_log("collapse_to error: %r" % (e,))
        self._set_current_path(path)
        _mfm_log("collapse_to: %r" % (path,))

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
        if _fs.isdir(path):
            # r124: 今いる階層の «祖先（または現在地そのもの）» をクリック
            # → そこまで戻る。従来は何も起きず、«親階層の別フォルダを選ばないと
            # 上に戻れない» 状態だった（ユーザー報告 2026-10-06）。
            # 平坦トグル ON のカラムは «追従して平坦表示» が仕様なので除く。
            _flat_on = False
            try:
                _fv = self._column_view._flatten_view
                _flat_on = _fv is not None and getattr(_fv, "_mfm_flatten", False)
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py _on_item_clicked(flatten)")
            if not _flat_on and self._is_at_or_above_current(path):
                self._collapse_to(path, proxy_index)
                self.selection_changed.emit([path])
                return
            if self._column_view.isVisible() and self._is_native_expandable(proxy_index, path):
                # 通常フォルダ(クリックしたカラムの子): ネイティブ列展開に任せルート不変
                _mfm_log("click dir: native expand path=%r" % path)
                self._set_current_path(path)
            else:
                # 別ブランチへのジャンプ(ショートカット先など) → トップから全カラム再構築
                _mfm_log("click dir: cross-branch -> _navigate path=%r current=%r"
                         % (path, self._current_path))
                self._navigate(path)
            if _flat_on and self._flat_col.isVisible():
                # 平坦トグルON中は閉じずに、クリックしたフォルダへ追従
                # （単一選択でも配下の階層を平坦で見続けたい、の仕様）
                self._on_flat_request([path])
            else:
                # 通常クリック＝単一選択（標準挙動）。複数選択で出したカラムは
                # «平坦カラムも共通フォルダカラムも» 畳んで通常の展開に戻す。
                self._close_multi_columns()
            return
        # r124: ファイルを単独でクリックした時も «複数選択をやめた» である。
        # 従来はここで何も畳んでおらず、平坦／共通フォルダカラムが
        # 取り残されていた（ユーザー報告 2026-10-06）。
        self._close_multi_columns()
        # 単一ファイル選択: パス欄にファイル名まで表示する
        try:
            self._addr_bar.setText(path)
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:1181 _on_item_clicked")
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
        # r124: ダブルクリックは «1回目の押下で clicked / 2回目で activated» を
        # 両方出す（Qt の仕様）。シングルクリック動作が «開く/インポート/
        # リファレンス» の時、続く activated の «関連付けで開く» まで走ると
        # 1 回のダブルクリックで 2 つの動作が起きる（実機で «2 度実行される»
        # の正体の一つ）。何をいつ実行したかを覚えておき、activated 側で弾く。
        if action != "none":
            self._note_click_action(path)
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
        if _fs.isdir(path):
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
                except Exception as _e:
                    _swallow(_e, "ui/browser_panel.py:1218 _on_item_activated")
            else:
                self._navigate(path)  # 別ブランチへのジャンプはトップから全カラム再構築
            return
        # ダブルクリックは関連付けアプリ（OS既定）で開く。
        # r124: 直前の clicked で «この同じファイルに» 動作を実行していたら
        # 何もしない（1 ジェスチャー = 1 動作）。
        if self._click_action_just_ran(path):
            _mfm_log("dblclick: 直前のクリック動作と同一対象のため関連付け起動を抑止 %r"
                     % (path,))
            return
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
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:1242 _is_native_expandable")
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
                 % (path, target, (_fs.exists(target) if target else None)))
        if target and _fs.exists(target):
            self.status_message.emit(
                tr("ショートカット解決: %s → %s",
                   "Shortcut resolved: %s -> %s") % (path, target))
            self._navigate(target)
            return True
        if target:
            self.status_message.emit(
                tr("ショートカット参照先が見つかりません: %s",
                   "Shortcut target not found: %s") % target)
        return False

    # r124: 1 ジェスチャー = 1 動作。clicked と activated が両方飛ぶ経路で
    # «同じ対象に 2 つの動作» が走るのを止めるための覚え書き。
    _CLICK_ACTION_GRACE_MS = 900

    def _note_click_action(self, path: str):
        import time as _t
        try:
            key = os.path.normcase(os.path.abspath(path))
        except Exception:
            key = path
        self._last_click_action = (key, _t.monotonic())

    def _click_action_just_ran(self, path: str) -> bool:
        import time as _t
        prev = getattr(self, "_last_click_action", None)
        if not prev:
            return False
        try:
            key = os.path.normcase(os.path.abspath(path))
        except Exception:
            key = path
        try:
            from core.compat import QApplication as _QA
            span = max(self._CLICK_ACTION_GRACE_MS,
                       _QA.doubleClickInterval() + 200) / 1000.0
        except Exception:
            span = self._CLICK_ACTION_GRACE_MS / 1000.0
        return prev[0] == key and (_t.monotonic() - prev[1]) < span

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
                except Exception as _e:
                    _swallow(_e, "ui/browser_panel.py:1348 _set_inline_panel_sizes")
            sizes[inline_index] = inline_w
            used += inline_w
            try:
                sp = self._view_stack.indexOf(self._flat_spacer)
                rest = max(0, total - used)
                if sp >= 0:
                    self._flat_spacer.setVisible(rest > 0)
                    sizes[sp] = rest
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:1358 _set_inline_panel_sizes")
            self._view_stack.setSizes(sizes)
            # 注意: ここで hbar を右端へ送る処理は行わない。視点が飛んで
            # 「元々のターゲットを見失う」ため、スクロール位置の調整は
            # _anchor_column_x / _restore_anchor_column_x（操作カラム固定）に任せる
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:1364 _set_inline_panel_sizes")

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
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:1395 _align_columns_right_edge")

    # ------------------------------------------------------------------
    # 連携状態の自動更新（r136）
    # ------------------------------------------------------------------
    # 従来は «表示時に 1 回、あとは手動更新» だった。そのほうが安いからだが、
    # 他で p4 を触って戻ってきた時にバッジが古いままで、毎回更新を押すことになる
    # （ユーザー指摘 2026-10-08）。
    #
    # 重いのは p4 fstat 等の «サーバ往復» なので、無制限には回さない。
    #   ・対象は «今開いているカラムのフォルダ» だけ
    #   ・有効期限（既定 60 秒）を超えたものだけ取り直す
    #   ・窓が隠れている間は何もしない（見えていないもののために回すのは無駄）
    #   ・設定 0 で従来どおり«表示時＋手動»へ戻せる

    def _integrations(self):
        """IntegrationManager。

        r136: **これまで BrowserPanel 側に定義が無かった。** 平坦ビューの
        状態要求（_request_flat_integration_status, r72）は self._integrations()
        を呼んでいたが AttributeError になり、try/except に吞まれていた——
        つまり平坦ビューのバッジと右クリックは直っていなかった。"""
        return self._column_view._integrations()

    INTEG_AUTO_KEY = "integrations_auto_refresh_sec"
    INTEG_AUTO_DEFAULT = 60
    _INTEG_TICK_MS = 5000

    def _start_integration_auto_refresh(self, mgr):
        sec = self._integration_auto_sec()
        try:
            mgr.set_ttl(sec)
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _start_integration_auto_refresh")
        t = QTimer(self)
        t.setInterval(self._INTEG_TICK_MS)
        t.timeout.connect(self._tick_integration_auto_refresh)
        self._integ_auto_timer = t
        if sec > 0:
            t.start()

    def _integration_auto_sec(self) -> int:
        try:
            return max(0, int(self._sm.get(self.INTEG_AUTO_KEY,
                                           self.INTEG_AUTO_DEFAULT)))
        except (TypeError, ValueError):
            return self.INTEG_AUTO_DEFAULT

    def apply_integration_auto_refresh(self):
        """設定を読み直して自動更新の入/切を合わせる（設定画面から）。"""
        t = getattr(self, "_integ_auto_timer", None)
        sec = self._integration_auto_sec()
        try:
            self._integrations().set_ttl(sec)
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py apply_integration_auto_refresh")
        if t is None:
            return
        if sec > 0:
            if not t.isActive():
                t.start()
        else:
            t.stop()

    def _auto_refresh_targets(self):
        """自動更新の対象。

        r136b: **ウィジェットを歩かない。** 当初は開いているカラムを
        findChildren で集めていたが、QColumnView はカラムを非同期に
        作っては捨てるので、タイマーがその最中に触ると落ちうる。
        実機で test_preset_dirs が ACCESS_VIOLATION で死んだ（2026-10-08）。
        «今見ているフォルダ» だけにすれば、安全な上に問い合わせも減る。"""
        try:
            cur = self.current_path()
            return [cur] if cur else []
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _auto_refresh_targets")
            return []

    def _tick_integration_auto_refresh(self):
        if self._integration_auto_sec() <= 0:
            return
        try:
            if not self.isVisible():
                return
            win = self.window()
            if win is not None and (win.isMinimized() or not win.isVisible()):
                return
        except RuntimeError:
            return                      # パネルがもう無い
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _tick_integration_auto_refresh")
            return
        try:
            mgr = self._integrations()
            for d in self._auto_refresh_targets():
                mgr.request_status(d)      # 期限切れのものだけ実際に走る
        except Exception as e:
            _mfm_log("integration auto refresh error: %r" % (e,))

    def refresh_integrations_on_activate(self):
        """窓が前面に戻った時の更新（r136）。

        他アプリで p4 を触って戻ってくる«典型»なので、通常より短い
        期限（5 秒）で見る。alt+tab の往復で叩かないため全部強制にはしない。"""
        if self._integration_auto_sec() <= 0:
            return
        try:
            mgr = self._integrations()
            for d in self._auto_refresh_targets():
                mgr.request_status(d, max_age=5.0)
        except Exception as e:
            _mfm_log("integration activate refresh error: %r" % (e,))

    def _ensure_flat_badge_refresh(self):
        """連携状態が届いたら平坦ビューも描き直す（r146）。

        カラム側は CappedColumnView が status_updated を受けて再描画して
        いたが、平坦ビューは誰も受けていなかった。状態を取りに行っていても
        «届いた時に描き直す» 人がいないので、バッジは出ないままだった。"""
        if getattr(self, "_flat_badge_hooked", False):
            return
        try:
            mgr = self._integrations()
            mgr.status_updated.connect(self._on_flat_integration_status)
            self._flat_badge_hooked = True
        except Exception as e:
            _mfm_log("flat badge hook error: %r" % (e,))

    def _on_flat_integration_status(self, _directory: str):
        try:
            fc = self._flat_col
            if fc.isVisible():
                fc._view.viewport().update()
        except RuntimeError:
            pass
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _on_flat_integration_status")

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
            self._ensure_flat_badge_refresh()
            mgr = self._integrations()
            for d in dirs:
                mgr.request_status(d)
        except Exception as e:
            _mfm_log("flat integration status error: %r" % (e,))

    def _apply_item_size_to_views(self, px: int, mode: str = None):
        """カラム以外のビュー（独立サムネビュー・平坦カラム）へも表示サイズを配る。

        スライダーは «全体的な表示サイズ» を変えるもの、というユーザー指示（r102）。"""
        px = max(8, int(px))
        # r120: 平坦カラムもリスト／サムネイルを持つので、同じモードの分だけ渡す
        try:
            for m in (("list", "thumb") if mode is None else (mode,)):
                self._flat_col.set_item_size(px, m)
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:1431 _apply_item_size_to_views")
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
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:1479 _on_archive_request")

    def _on_flat_request(self, dirs):
        """平坦カラムの表示要求。dirs があれば選択カラムの直後へ詰めて表示する。"""
        dirs = [d for d in (dirs or []) if d and _fs.isdir(d)]
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
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:1497 _on_flat_request")
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
                except Exception as _e:
                    _swallow(_e, "ui/browser_panel.py:1518 _on_flat_request")
        else:
            self._flat_col.hide()
            self._rebuild_common_columns(0, [])
            try:
                self._flat_spacer.hide()
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:1525 _on_flat_request")
            # 平坦トグルの状態を表示と同期（ONのまま非表示を防ぐ）
            try:
                self._column_view._reset_flatten_toggle()
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:1530 _on_flat_request")
            # 選択が無くなった時: パス欄を現在のディレクトリへ戻す
            try:
                self._addr_bar.setText(self._current_path or "")
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:1535 _on_flat_request")

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
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:1550 _rebuild_common_columns")
        del self._common_cols[level:]
        del self._common_srcs[level:]
        sources = [s for s in (sources or []) if _fs.isdir(s)]
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
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:1590 _on_flat_depth_changed")
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
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:1608 _on_common_selection")
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














    # ------------------------------------------------------------------
    # File operations (UI wrappers)
    # ------------------------------------------------------------------



    _DIR_DLG_OPTS = QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog



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
                    if p and _fs.isdir(p):
                        return p
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:2038 _active_column_folder")
        return self._paste_target_dir()







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
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:2225 _work")

        threading.Thread(target=_work, daemon=True,
                         name="mfm-gone-probe").start()

    def _on_gone_result(self, checked_path: str, dest: str):
        """ワーカーの判定結果を UI スレッドで受ける（r107）。"""
        self._gone_probe_busy = False
        # r146: «その中身がドライブ最上位から列挙できない» と分かったリンク。
        # ここに入ったものだけ、そのリンクをカラムのルートにする（退避路）。
        self._link_own_root = set()
        if not dest:
            return
        if (self._current_path or "") != checked_path:
            return          # 判定中に別の場所へ移動していた
        _mfm_log("current path gone(async): %s -> %s" % (checked_path, dest))
        self.status_message.emit(
            "表示中のフォルダが無くなったため、%s へ移動しました" % dest)
        # r146: 移動先が «すぐ上の親» で、その親のカラムが既に出ているなら
        # カラムを組み直さず、消えたフォルダのカラムを閉じて親に戻るだけにする
        # （ユーザー報告 2026-10-15「フォルダを削除したら、カラムが一度左に
        # 移動し、再度サーチされるように右に戻る」）。navigate_to は全カラムを
        # 作り直すので、消したものの下だけ片付けたい時には大げさ。
        try:
            parent = os.path.dirname(checked_path.rstrip("/\\"))
            same = os.path.normcase(os.path.normpath(parent)) == os.path.normcase(
                os.path.normpath(dest))
            idx = self._column_view._proxy_index_for_path(dest) if same else None
            if idx is not None and idx.isValid():
                self._collapse_to(dest, idx)
                return
        except Exception as e:
            _mfm_log("gone: collapse failed, fall back to navigate: %r" % (e,))
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




    RENAME_VERIFY_MS = 250











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







    def _refresh_flat_view(self):
        try:
            fc = self._flat_col
            if fc.isVisible():
                fc.set_sources(list(fc._sources))
                self._request_flat_integration_status()
        except Exception as e:
            _mfm_log("flat refresh error: %r" % (e,))




    # ------------------------------------------------------------------
    # カラムへのファイルドロップ（r87）
    # ------------------------------------------------------------------

    DROP_MENU_KEY = "drop_action_menu"













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
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:3035 _on_thumbnail_ready")
        try:
            self._flat_col.refresh_thumbs()      # r120
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _on_thumbnail_ready(flat)")

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

    def set_slide_animation(self, on: bool):
        """カラムのスライドアニメーションの入切（r120）。
        切ると «クリックした瞬間に出る»（滑らかさと引き換えに描画が不要）。"""
        self._column_view.set_slide_animation(bool(on))
        self._sm.set("column_slide_animation", bool(on))

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
