"""
Main Window
===========
The central QMainWindow that hosts all panels as dockable widgets.

Layout (default)
----------------
  ┌─────────────────────────────────────────────────────┐
  │  MenuBar                                            │
  │  ToolBar (quick-nav buttons, Maya ver, action mode) │
  ├──────────────┬──────────────────────────┬───────────┤
  │  Bookmark    │                          │ History   │
  │  Panel       │  Browser Panel           │ Panel     │
  │  (dock-left) │  (central)               │(dock-right│
  │              │                          │           │
  │              │                          │           │
  ├──────────────┴──────────────────────────┴───────────┤
  │  StatusBar (path | Maya version | message)          │
  └─────────────────────────────────────────────────────┘
"""
from core.diag import swallow as _swallow  # r112

import os
from pathlib import Path
from typing import Optional, List

from core.compat import (
    Qt, Signal, QObject,
    QMainWindow, QWidget, QHBoxLayout,
    QLabel, QComboBox, QToolButton, QMenu, QMenuBar, QSplitter, QMessageBox, QFileDialog, QInputDialog, QDialog,
    QTimer
)
from core.settings_manager import SettingsManager
from core.bookmark_manager import BookmarkManager
from core.thumbnail_generator import ThumbnailManager
from core.maya_version import (
    MayaInstallation, find_installed_maya_versions,
    is_running_inside_maya, get_current_maya_version,
    launch_maya
)
from core.maya_bridge import (MayaBridge, escape_path, scan_open_ports,
                              find_free_port, IDENTIFY_CODE, parse_identify)
from core.blender_version import (BlenderInstallation, find_installed_blender_versions,
                                  launch_blender)
from core import blender_bridge as _bl
from core.file_operations import dcc_for_path
from core.i18n import tr
def _tv():
    """テーマトークン（色・形状・書体）。**遅延 import** すること。
    トップレベルで core.theme_engine から名前を取り込むと、Maya 内の
    ホットリロードや部分再読込で «partially initialized module» に当たり
    ImportError（cannot import name 'qss_vars'）になる（r82 で実害）。"""
    from core.theme_engine import qss_vars
    return qss_vars()
from ui.preset_editor import ReferencePresetEditor
from ui.settings_dialog import SettingsDialog
from ui.batch_rename_dialog import BatchRenameDialog
from ui.reference_editor import ReferenceEditor


# ---------------------------------------------------------------------------
# History Panel (inline – keeps it self-contained)
# ---------------------------------------------------------------------------

from ui.nav_panels import (  # noqa: F401  （再エクスポート）
    HistoryPanel, QuickNavBar, _BridgeNotifier,
)
from ui.main_window_dcc import MainWindowDccMixin


class MainWindow(MainWindowDccMixin, QMainWindow):

    def __init__(self, settings_manager: SettingsManager,
                 maya_installation: Optional[MayaInstallation] = None,
                 parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        self._maya_inst = maya_installation
        self._inside_maya = is_running_inside_maya()
        self._maya_ver = (get_current_maya_version() or
                          (maya_installation.version if maya_installation else ""))

        if self._maya_ver:
            self._sm.set_maya_version(self._maya_ver)

        # Sub-managers
        self._bm_mgr = BookmarkManager(self._sm)
        # スタンドアロン⇔Maya 連携（commandPort ブリッジ）
        self._bridge = MayaBridge(
            port=int(self._sm.get("maya_command_port", 20261) or 20261))
        self._bridge_notify = _BridgeNotifier(self)
        self._bridge_notify.done.connect(self._on_bridge_done)
        self._bridge_notify.conn_list.connect(self._on_conn_list)
        self._conn_pids = {}   # 接続ポート → MayaのPID（識別照会で取得）
        # Blender 連携（r65）: Maya と同じプロトコル、別ポートレンジ
        self._bl_bridge = _bl.BlenderBridge(
            port=int(self._sm.get("blender_command_port", _bl.DEFAULT_PORT) or _bl.DEFAULT_PORT))
        self._bl_conn_pids = {}
        self._blender_inst = None
        # ヘッダの DCC 切替（"maya" / "blender"）。共通形式の送り先にもなる
        self._dcc = self._sm.get("dcc_target", "maya") or "maya"
        self._known_conns = {"maya": {}, "blender": {}}      # r78: 識別済み接続の記憶
        self._scan_running = {"maya": False, "blender": False}
        self._install_freeze_watchdog()   # MFM_DEBUG時のみ有効
        self._thumb_mgr = ThumbnailManager(
            cache_size=self._sm.get("thumbnail_cache_size", 256),
            thumb_size=self._sm.get("thumbnail_size", 128),
            parent=self,
        )

        from core.version import version_string
        self.setWindowTitle(
            f"Maya File Manager {version_string()}"
            + (f"  —  Maya {self._maya_ver}" if self._maya_ver else "")
        )
        self.setMinimumSize(1024, 640)

        self._build_ui()
        self._build_menu()
        self._install_header()      # メニュー構築後（以降 self.menuBar() を呼ばない）
        self._restore_geometry()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Central: ブラウザエリア（複数化対応・縦積み） ────────────────
        # 「プリセット行＋ブックマーク/履歴＋ブラウザ」を1ユニット(BrowserArea)
        # とし、縦スプリッタで複数配置できる。追加・削除・並び替え可能、
        # 各エリアの状態(パス・分割幅)は設定に保存する。
        self._areas = []
        self._areas_split = QSplitter(Qt.Vertical, self)
        self._areas_split.setChildrenCollapsible(False)
        self._areas_split.setHandleWidth(4)
        states = self._sm.get("browser_areas_state", None)
        if not isinstance(states, list) or not states:
            states = [None]
        for st in states:
            self._add_area(state=st, save=False)
        self._browser = self._areas[0].browser   # 互換エイリアス（既存機能の参照先）
        self._bookmark_panel = self._areas[0].bookmark_panel
        self._history_panel = self._areas[0].history_panel
        self._bm_dock = None
        self._hist_dock = None
        self.setCentralWidget(self._areas_split)

        # （旧「重複フォルダ検出」ドックは r56 で廃止。複数選択＋平坦/共通子
        #   フォルダ表示が上位互換で、再帰スキャンはリンク越しI/Oの温床だった）

        # ── Toolbar ───────────────────────────────────────────────────
        self._build_toolbar()

        # ── Status bar ────────────────────────────────────────────────
        sb = self.statusBar()

        self._status_path_label = QLabel("")
        self._status_path_label.setStyleSheet(
            "color:%(on_surface_dim)s;font-size:%(label_px)spx;" % _tv())
        sb.addWidget(self._status_path_label)

        sb.addPermanentWidget(QLabel(f"Maya {self._maya_ver}" if self._maya_ver else "Standalone"))

    # ------------------------------------------------------------------
    # ブラウザエリア管理（追加・削除・並び替え・状態保存）
    # ------------------------------------------------------------------

    def _add_area(self, state=None, after=None, save=True):
        """新しいブラウザエリアを追加する。after 指定でその直下に挿入。"""
        from ui.browser_area import BrowserArea
        area = BrowserArea(self._sm, self._thumb_mgr, self._bm_mgr, parent=self)
        # DCC 連携（クリック動作）: 形式と選択中 DCC で Maya/Blender へ振り分け（r65）
        area.browser.set_open_callback(self._dcc_open)
        area.browser.set_import_callback(self._dcc_import)
        area.browser.set_reference_callback(self._dcc_reference)
        area.browser.set_dnd_callback(self._on_maya_drop)
        # r90: DCC への落下を Manager が引き受けるか（OLE の同期ドロップで固まらない）
        area.browser.set_dcc_takeover_callback(self._can_take_over_drop)
        # 右クリックの「Maya へ…」「Blender へ…」は送り先を明示する
        area.browser.set_dcc_callback(
            lambda app, action, paths: self._on_maya_drop(action, paths, app))
        # r70: 保存／書き出しの「選択中 DCC」をブラウザへ教える
        area.browser.set_dcc_target_provider(lambda: self._dcc)
        area.bookmark_panel.open_requested.connect(self._dcc_open)
        area.bookmark_panel.import_requested.connect(self._dcc_import)
        area.bookmark_panel.reference_requested.connect(self._dcc_reference)
        # 共有ハンドラ
        area.file_activated.connect(self._on_file_activated)
        area.directory_changed.connect(self._on_directory_changed)
        area.status_message.connect(self.statusBar().showMessage)
        area.bookmark_requested.connect(self._on_bookmark_requested)
        area.batch_rename_requested.connect(self._open_batch_rename)   # r101
        # エリア操作
        area.add_below_requested.connect(self._on_area_add_below)
        area.remove_requested.connect(self._on_area_remove)
        area.move_up_requested.connect(lambda a: self._on_area_move(a, -1))
        area.move_down_requested.connect(lambda a: self._on_area_move(a, +1))

        if after is not None and after in self._areas:
            pos = self._areas.index(after) + 1
        else:
            pos = len(self._areas)
        self._areas.insert(pos, area)
        self._areas_split.insertWidget(pos, area)
        if isinstance(state, dict):
            area.apply_state(state)
        self._refresh_area_headers()
        if save:
            self._save_areas_state()
        return area

    def _on_area_add_below(self, area):
        self._add_area(after=area)

    def _on_area_remove(self, area):
        if len(self._areas) <= 1 or area not in self._areas:
            return
        self._areas.remove(area)
        area.setParent(None)
        area.deleteLater()
        # 互換エイリアスの付け替え
        self._browser = self._areas[0].browser
        self._bookmark_panel = self._areas[0].bookmark_panel
        self._history_panel = self._areas[0].history_panel
        self._quick_nav = self._areas[0].quick_nav
        self._refresh_area_headers()
        self._save_areas_state()

    def _on_area_move(self, area, delta):
        if area not in self._areas:
            return
        i = self._areas.index(area)
        j = i + delta
        if j < 0 or j >= len(self._areas):
            return
        self._areas.pop(i)
        self._areas.insert(j, area)
        self._areas_split.insertWidget(j, area)
        self._refresh_area_headers()
        self._save_areas_state()

    def _refresh_area_headers(self):
        n = len(self._areas)
        for i, a in enumerate(self._areas):
            try:
                a.set_index(i, n)
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:491 _refresh_area_headers")

    def _save_areas_state(self):
        try:
            self._sm.set("browser_areas_state",
                         [a.get_state() for a in self._areas], save=False)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:498 _save_areas_state")

    def _install_header(self):
        """メニューバーを含む «ヘッダ行» を組み立てて setMenuWidget で置き換える。
        必ず _build_menu の後に1回だけ呼ぶ。以降 self.menuBar() を呼ぶと
        QMainWindow が新しい QMenuBar を作ってこのヘッダを削除してしまう。"""
        from core.compat import QSizePolicy
        # 【重要】QMainWindow::setMenuBar/setMenuWidget は «前のメニューバー» を
        # deleteLater する。そのため QMainWindow.menuBar() が作る既定バーは使わず、
        # _build_menu が自前の QMenuBar（self._menubar）にメニューを構築し、
        # ここでヘッダ行へ組み込む。以降どこでも self.menuBar() を呼ばないこと。
        from ui.dcc_header import _HeaderRow
        mb = self._menubar
        mb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        # 手動レイアウト: 起動ボタンがウィンドウ中央に来るように DCC ブロックを配置
        header = _HeaderRow(mb, self._dcc_hdr, self._action_blk, self)
        header.setObjectName("mfmHeaderCorner")     # 視認性の階層: 最も沈めた面
        header.setAttribute(Qt.WA_StyledBackground, True)
        self.setMenuWidget(header)
        self._header_widget = header
        # M / b バッジにインストール済み DCC の正式アイコン（exe 埋め込み）を使う
        try:
            m_exe = self._maya_inst.executable if self._maya_inst else None
            b_exe = self._blender_inst.executable if self._blender_inst else None
            self._dcc_hdr.set_app_icons(m_exe, b_exe)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:524 _install_header")

    def _build_toolbar(self):
        """ヘッダ（r67、ユーザーのスケッチ準拠）:
        [メニュー] … [DCC ブロック 2段（中央）] … [クリック/D&D 2段（右端）]
        QMenuBar のコーナーは左右しか無いため、メニューバーを含む «ヘッダ行»
        ウィジェットを作って setMenuWidget で置き換える。部品は ui/dcc_header.py。"""
        from ui.dcc_header import DccHeader, ActionBlock
        # 部品だけ作る。ヘッダ行への組み込み（setMenuWidget）は _install_header で
        # «メニュー構築の後» に行う。先に setMenuWidget すると、後の
        # self.menuBar() が新しい QMenuBar を作ってヘッダごと削除する
        # （実機で「UIが無い＋QComboBox already deleted」になった、r68）。
        self._dcc_hdr = DccHeader(self._dcc)
        self._action_blk = ActionBlock()

        # 互換用の別名（他メソッドが参照）
        self._maya_combo = self._dcc_hdr.maya_ver
        self._blender_combo = self._dcc_hdr.blender_ver
        self._conn_combo = self._dcc_hdr.maya_conn
        self._bl_conn_combo = self._dcc_hdr.blender_conn
        self._action_combo = self._action_blk.click_combo
        self._dnd_combo = self._action_blk.dnd_combo

        # バージョン一覧（スタンドアロン時のみ意味がある。Maya 内では Maya 側を無効化）
        self._maya_installs = find_installed_maya_versions(2023) if not self._inside_maya else []
        self._blender_installs = self._detect_blender()
        # r88: インストール済みの Blender 自動起動ブリッジが古ければ差し替える
        # （ログ表示などの新機能を届けるため。未インストールには触れない）
        try:
            _bl.refresh_installed_startup()
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:555 _build_toolbar")
        self._populate_version_combos()
        self._maya_combo.currentIndexChanged.connect(self._on_maya_version_changed)
        self._blender_combo.currentIndexChanged.connect(self._on_blender_version_changed)
        if self._inside_maya:
            self._maya_combo.setEnabled(False)
            self._conn_combo.setEnabled(False)

        # DCC 切替・起動・接続
        self._dcc_hdr.dcc_changed.connect(self._on_dcc_switched)
        self._dcc_hdr.launch_requested.connect(self._launch_dcc)
        self._dcc_hdr.refresh_requested.connect(self._refresh_connections)
        self._dcc_hdr.front_requested.connect(self._focus_connected_dcc)
        self._conn_combo.currentIndexChanged.connect(
            lambda i: self._on_conn_changed(i, "maya"))
        self._bl_conn_combo.currentIndexChanged.connect(
            lambda i: self._on_conn_changed(i, "blender"))
        QTimer.singleShot(1000, self._refresh_connections)

        # クリック動作 / D&D動作
        action_map = ["open", "import", "reference", "none"]
        current_action = self._sm.get("single_click_action", "open")
        if current_action == "preview":     # 旧設定の移行
            current_action = "open"
        if current_action in action_map:
            self._action_combo.setCurrentIndex(action_map.index(current_action))
        self._action_combo.currentIndexChanged.connect(
            lambda idx: self._sm.set("single_click_action", action_map[idx]))
        dnd_action = self._sm.get("dnd_action", "import")
        if dnd_action in action_map:
            self._dnd_combo.setCurrentIndex(action_map.index(dnd_action))
        self._dnd_combo.currentIndexChanged.connect(
            lambda idx: self._sm.set("dnd_action", action_map[idx]))

        # クイックナビ（プリセット）行は各ブラウザエリアが個別に持つ
        self._quick_nav = self._areas[0].quick_nav if self._areas else None

    def _build_menu(self):
        # 自前の QMenuBar（QMainWindow.menuBar() は使わない。_install_header 参照）
        mb = QMenuBar()
        self._menubar = mb

        # ── File ─────────────────────────────────────────────────────
        file_menu = mb.addMenu(tr("ファイル", "File"))

        if self._inside_maya:
            open_act = file_menu.addAction(tr("開く...", "Open..."))
            open_act.triggered.connect(self._open_dialog)
            import_act = file_menu.addAction(tr("インポート...", "Import..."))
            import_act.triggered.connect(self._import_dialog)
            ref_act = file_menu.addAction(tr("リファレンス...", "Reference..."))
            ref_act.triggered.connect(self._reference_dialog)
            file_menu.addSeparator()

        file_menu.addAction(tr("終了", "Exit")).triggered.connect(self.close)

        # ── 編集 ─────────────────────────────────────────────────────
        edit_menu = mb.addMenu(tr("編集", "Edit"))
        # Undo/Redo（r62）。ショートカットはブラウザ側の QAction（Ctrl+Z / Ctrl+Y、
        # WidgetWithChildrenShortcut）が持つ。ここにも同じショートカットを付けると
        # 「あいまいなショートカット」で両方が発火しなくなるため、メニューは
        # 表示用（\t の後ろはヒント文字列）にして各ブラウザへ委譲する。
        self._undo_act = edit_menu.addAction(tr("元に戻す", "Undo") + "\tCtrl+Z")
        self._undo_act.triggered.connect(lambda _c=False: self._browser_undo())
        self._redo_act = edit_menu.addAction(tr("やり直す", "Redo") + "\tCtrl+Y")
        self._redo_act.triggered.connect(lambda _c=False: self._browser_redo())
        edit_menu.addSeparator()
        try:
            from core.undo_stack import get_undo_stack
            get_undo_stack().add_listener(self._refresh_undo_menu)
            edit_menu.aboutToShow.connect(self._refresh_undo_menu)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:627 _build_menu")
        self._restore_act = edit_menu.addAction(
            tr("前回のパスを復元", "Restore last path"))
        self._restore_act.setCheckable(True)
        self._restore_act.setChecked(bool(self._sm.get("restore_last_path", True)))
        self._restore_act.toggled.connect(
            lambda v: self._sm.set("restore_last_path", bool(v)))
        # 外部サービス連携（Git/SVN/Perforce/クラウド）の全体スイッチ。
        # 未検出のサービスは元々コードパスに入らないが、万一の際の退避用。
        self._integ_act = edit_menu.addAction(
            tr("外部サービス連携（Git/SVN/Perforce/クラウド）※再起動で反映",
               "Integrations (Git/SVN/Perforce/Cloud) - restart to apply"))
        self._integ_act.setCheckable(True)
        self._integ_act.setChecked(bool(self._sm.get("integrations_enabled", True)))
        self._integ_act.toggled.connect(
            lambda v: self._sm.set("integrations_enabled", bool(v)))

        # ── ブックマーク ──────────────────────────────────────────────
        bm_menu = mb.addMenu(tr("ブックマーク", "Bookmarks"))
        bm_menu.addAction(tr("現在のフォルダをブックマーク",
                             "Bookmark current folder")).triggered.connect(
            lambda: self._bm_mgr.add_directory(self._browser.current_path())
        )

        # ── ツール ────────────────────────────────────────────────────
        tools_menu = mb.addMenu(tr("ツール", "Tools"))

        tools_menu.addAction(tr("バッチリネーム...", "Batch Rename...")).triggered.connect(
            self._open_batch_rename
        )
        if not self._inside_maya:
            tools_menu.addSeparator()
            tools_menu.addAction(
                tr("Maya連携を全Mayaにインストール...",
                   "Install Maya bridge for all Mayas...")
            ).triggered.connect(self._install_maya_bridge)
            tools_menu.addAction(
                tr("Blender連携を全Blenderにインストール...",
                   "Install Blender bridge for all Blenders...")
            ).triggered.connect(lambda _c=False: self._install_blender_bridge())
        tools_menu.addAction(
            tr("Blender の場所を指定...", "Locate Blender...")
        ).triggered.connect(lambda _c=False: self._locate_blender())
        tools_menu.addSeparator()
        tools_menu.addAction(tr("リファレンスプリセットエディタ...",
                                "Reference Preset Editor...")).triggered.connect(
            self._open_preset_editor
        )
        tools_menu.addAction(tr("リファレンスエディタ...",
                                "Reference Editor...")).triggered.connect(
            self._open_reference_editor
        )
        tools_menu.addSeparator()
        tools_menu.addAction(tr("設定...", "Settings...")).triggered.connect(
            self._open_settings)

        # ── 表示 ──────────────────────────────────────────────────────
        view_menu = mb.addMenu(tr("表示", "View"))
        # ブックマーク/履歴は各ブラウザエリアに常設のためdockメニューは廃止
        view_menu.addAction(tr("ブラウザエリアを追加", "Add Browser Area")).triggered.connect(
            lambda: self._add_area(after=self._areas[-1] if self._areas else None)
        )

        # ── ヘルプ ────────────────────────────────────────────────────
        help_menu = mb.addMenu(tr("ヘルプ", "Help"))
        help_menu.addAction(tr("バージョン情報", "About")).triggered.connect(self._about)

    # ------------------------------------------------------------------
    # File actions
    # ------------------------------------------------------------------

    def _on_file_activated(self, path: str):
        action = self._sm.get("double_click_action", "open")
        if action == "open":
            self._dcc_open(path)
        elif action == "import":
            self._dcc_import(path)
        elif action == "reference":
            self._dcc_reference(path)

    def _on_directory_changed(self, path: str):
        try:
            self._status_path_label.setText(path)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:711 _on_directory_changed")
        # 全エリアの履歴パネルを更新（履歴データは共有のため）
        for a in getattr(self, "_areas", []):
            try:
                a.history_panel.refresh()
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:717 _on_directory_changed")
        self._save_areas_state()

    def _on_bookmark_requested(self, paths):
        """ブラウザの右クリック『ブックマークに追加』を BookmarkManager に反映する。"""
        added = 0
        for p in paths:
            if not p or self._bm_mgr.is_bookmarked(p):
                continue
            if os.path.isdir(p):
                self._bm_mgr.add_directory(p)
            else:
                self._bm_mgr.add_file(p)
            added += 1
        self.statusBar().showMessage(tr("ブックマークに追加: %d 件",
                                        "Added %d item(s) to bookmarks") % added)

    def _open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "ファイルを開く", "", "Scenes (*.ma *.mb *.blend);;All (*.*)"
        )
        if path:
            self._dcc_open(path)

    def _import_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "インポート", "",
            "3D Files (*.ma *.mb *.blend *.fbx *.obj *.abc *.usd *.usda *.usdc *.gltf *.glb);;All (*.*)"
        )
        if path:
            self._dcc_import(path)

    def _reference_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "リファレンス / リンク", "",
            "Scenes (*.ma *.mb *.blend *.fbx);;All (*.*)"
        )
        if path:
            self._dcc_reference(path)

    # ------------------------------------------------------------------
    # Maya operations (inside-Maya callbacks)
    # ------------------------------------------------------------------

    # ---- スタンドアロン⇔Maya 連携 ----------------------------------

    # ---- 起動済みMayaの列挙・接続先切替 -------------------------------

    # ---- DCC 切替（r65） ---------------------------------------------------

    def _open_preset_editor(self):
        dlg = ReferencePresetEditor(self._sm, parent=self)
        dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()

    def _open_settings(self):
        dlg = SettingsDialog(self._sm, parent=self)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()

    # ── Undo / Redo（r62）────────────────────────────────────────────────
    def _active_browser(self):
        """フォーカスのあるエリアのブラウザ（無ければ先頭）。"""
        try:
            from core.compat import QApplication
            w = QApplication.focusWidget()
            for a in getattr(self, "_areas", []):
                if w is not None and a.browser.isAncestorOf(w):
                    return a.browser
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:1905 _active_browser")
        return self._areas[0].browser if getattr(self, "_areas", None) else None

    def _browser_undo(self):
        b = self._active_browser()
        if b is not None:
            b._undo_op()

    def _browser_redo(self):
        b = self._active_browser()
        if b is not None:
            b._redo_op()

    def _refresh_undo_menu(self):
        try:
            from core.undo_stack import get_undo_stack
            st = get_undo_stack()
            u, r = st.undo_label(), st.redo_label()
            self._undo_act.setText((tr("元に戻す", "Undo") + (("  " + u) if u else "")) + "\tCtrl+Z")
            self._undo_act.setEnabled(st.can_undo())
            self._redo_act.setText((tr("やり直す", "Redo") + (("  " + r) if r else "")) + "\tCtrl+Y")
            self._redo_act.setEnabled(st.can_redo())
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:1928 _refresh_undo_menu")

    def _on_settings_changed(self):
        """Re-apply live settings to browsers and thumbnail manager."""
        for a in getattr(self, "_areas", []):
            try:
                a.browser.set_max_depth(self._sm.get("column_max_depth", 4))
                a.browser.set_thumb_size(self._sm.get("thumbnail_size", 128))
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:1937 _on_settings_changed")
        self._thumb_mgr.set_cache_size(self._sm.get("thumbnail_cache_size", 256))
        self.statusBar().showMessage(tr("設定を適用しました", "Settings applied"))

    def _open_batch_rename(self, paths=None):
        """バッチリネームを開く。

        r101: 右クリックから呼ばれた時は **その時の選択（paths）をそのまま対象**に
        する。メニューから呼ばれた時だけ «現在の選択 → 無ければ現在地の全ファイル»
        を拾う（従来は必ず拾い直していたため、右クリックした対象と食い違った）。
        """
        import os
        paths = [p for p in (paths or []) if p]
        if not paths:
            paths = self._browser._get_selected_paths() \
                if hasattr(self._browser, "_get_selected_paths") else []
        if not paths:
            current = self._browser.current_path()
            if os.path.isdir(current):
                paths = [os.path.join(current, f) for f in os.listdir(current)
                         if os.path.isfile(os.path.join(current, f))]
        if not paths:
            QMessageBox.information(self, tr("情報", "Information"),
                                    tr("リネーム対象のファイルが選択されていません。",
                                       "No files are selected for renaming."))
            return
        dlg = BatchRenameDialog(paths, parent=self)
        dlg.renamed.connect(lambda results: self.statusBar().showMessage(
            f"{len(results)} 件リネーム完了"))
        dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()

    def _open_reference_editor(self):
        dlg = ReferenceEditor(parent=self)
        dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()

    def _show_dock(self, dock):
        """閉じた/タブ化されたdockを確実に再表示して前面に出す。"""
        if dock is None:
            return
        dock.setVisible(True)
        dock.show()
        dock.raise_()

    def _about(self):
        QMessageBox.about(
            self, "Maya File Manager",
            "Maya File Manager v1.0\n\n"
            "Maya 2023 以降対応\n"
            "PySide2 / PySide6\n\n"
            "© 2025 PointLights for entertainment"
        )

    # ------------------------------------------------------------------
    # Window state
    # ------------------------------------------------------------------

    def _restore_geometry(self):
        geom = self._sm.get("window_geometry")
        state = self._sm.get("window_state")
        if geom:
            try:
                from core.compat import Qt
                import base64
                self.restoreGeometry(bytes.fromhex(geom))
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:2000 _restore_geometry")
        if state:
            try:
                # version=3: プリセット行のエリア内移動に伴い旧状態を無効化
                # version=4: ツールバー廃止（メニューバー右肩へ統合）で旧状態を無効化
                self.restoreState(bytes.fromhex(state), 4)
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:2007 _restore_geometry")

    def closeEvent(self, event):
        # version=3: ナビツールバー廃止（プリセット行はエリア内へ移動）に伴い
        # 旧ツールバー状態を無効化
        self._sm.set("window_geometry", self.saveGeometry().toHex().data().decode(), save=False)
        self._sm.set("window_state",    self.saveState(4).toHex().data().decode(), save=False)
        try:
            self._sm.set("last_path", self._browser.current_path(), save=False)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:2017 closeEvent")
        self._save_areas_state()
        self._sm.save()
        # Undo 用の作業ごみ箱（削除の取り消し先）を OS のごみ箱へ送る（r62）
        try:
            from core.undo_stack import get_undo_stack
            get_undo_stack().dispose_all()
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:2025 closeEvent")
        super().closeEvent(event)
