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
from ui.browser_panel import BrowserPanel
from ui.bookmark_panel import BookmarkPanel
from ui.preset_editor import ReferencePresetEditor
from ui.settings_dialog import SettingsDialog
from ui.batch_rename_dialog import BatchRenameDialog
from ui.quick_nav_editor import QuickNavPresetEditor
from ui.reference_editor import ReferenceEditor


# ---------------------------------------------------------------------------
# History Panel (inline – keeps it self-contained)
# ---------------------------------------------------------------------------

class HistoryPanel(QWidget):

    navigate_requested = Signal(str)

    def __init__(self, settings_manager: SettingsManager, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        from core.compat import QListWidget, QAbstractItemView, QToolButton, QVBoxLayout, QHBoxLayout, QLabel
        # 視認性の階層（r56）: サイドバーは «脇役» の面（theme_engine の #mfmSidePanel）
        self.setObjectName("mfmSidePanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(4, 4, 4, 4)
        header.addWidget(QLabel("🕐 履歴"))
        header.addStretch()
        clr_btn = QToolButton()
        clr_btn.setText("クリア")
        clr_btn.clicked.connect(self._clear_history)
        header.addWidget(clr_btn)
        layout.addLayout(header)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._list)

        self.refresh()

    def refresh(self):
        from core.compat import QListWidgetItem
        self._list.clear()
        for path in self._sm.get_history():
            item = QListWidgetItem(path)
            item.setToolTip(path)
            self._list.addItem(item)

    def _on_double_click(self, item):
        self.navigate_requested.emit(item.text())

    def _clear_history(self):
        self._sm.clear_history()
        self._list.clear()


# ---------------------------------------------------------------------------
# Quick-nav toolbar area
# ---------------------------------------------------------------------------

class QuickNavBar(QWidget):
    """Row of quick-navigation buttons loaded from settings presets."""

    navigate_requested = Signal(str)
    # r95: プリセットが切り替わった（変更前, 変更後）。BrowserArea が
    # «プリセット毎の最後に見ていたディレクトリ» の保存/復元に使う。
    preset_changed = Signal(str, str)

    # 開いている全インスタンス（マルチエリア間でプリセット変更を一括反映する）
    _instances: list = []

    @classmethod
    def refresh_all(cls):
        """全エリアのプリセット行を再構築する（編集保存・プリセット切替時）。"""
        import weakref
        alive = []
        for ref in cls._instances:
            w = ref()
            if w is None:
                continue
            try:
                w.refresh()
                alive.append(ref)
            except RuntimeError:
                pass   # C++側が破棄済み
        cls._instances = alive

    def __init__(self, settings_manager: SettingsManager, parent=None):
        super().__init__(parent)
        import weakref
        QuickNavBar._instances.append(weakref.ref(self))
        self._sm = settings_manager
        self._buttons: List[QToolButton] = []
        # プリセット選択は «エリア毎» に独立（リンクON時のみ全エリア連動）。
        # 初期値は最後に使ったプリセット（グローバル設定）。
        self._active = self._sm.get("quick_nav_preset", "default")
        # 視認性の階層（r56）: プリセット行は «脇役» の面（#mfmPresetBar）
        self.setObjectName("mfmPresetBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        # 左余白は BrowserArea の body 側で一括確保する（プリセット行と
        # 下のパネル群の左端が揃うように、ここでは最小限にする）
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(4)

        # プリセット選択＋編集アイコンを最左に配置
        from core.i18n import tr as _tr
        layout.addWidget(QLabel(_tr("プリセット:", "Preset:")))
        self._preset_combo = QComboBox()
        self._preset_combo.setFixedWidth(120)
        self._preset_combo.currentTextChanged.connect(self._on_preset_changed)
        layout.addWidget(self._preset_combo)

        # エリア間リンク（ON=明るい: プリセット変更を全エリアへ適用）
        self._link_btn = QToolButton()
        self._link_btn.setText("🔗")
        self._link_btn.setCheckable(True)
        self._link_btn.setChecked(
            bool(self._sm.get("quick_nav_link_areas", False)))
        self._link_btn.setToolTip(_tr(
            "エリア間リンク: ONの場合、プリセットの変更を全エリアに適用",
            "Link areas: when ON, preset changes apply to all areas"))
        self._link_btn.setObjectName("mfmPresetLink")
        # ON/OFF は「明るい/暗い」だけでは判別しづらいとの指摘により、
        # ON = Violet塗り＋白文字＋"🔗 ON" / OFF = 暗い文字＋"🔗 OFF" で明示
        self._link_btn.setStyleSheet(
            "#mfmPresetLink{color:%(on_surface_dim)s;"
            "background:transparent;border:1px solid %(hairline)s;"
            "border-radius:%(r_pill)spx;padding:2px 12px;font-weight:%(w_strong)s;}"
            "#mfmPresetLink:checked{color:%(on_primary)s;background:%(primary)s;"
            "border-color:%(primary)s;}" % _tv())
        self._link_btn.toggled.connect(self._on_link_toggled)
        self._update_link_look()
        layout.addWidget(self._link_btn)

        edit_btn = QToolButton()
        edit_btn.setText("⚙")
        edit_btn.setToolTip("クイックナビを編集")
        edit_btn.clicked.connect(self._edit_presets)
        layout.addWidget(edit_btn)

        # その横にナビボタンを並べる
        self._container = QWidget()
        self._btn_layout = QHBoxLayout(self._container)
        self._btn_layout.setContentsMargins(0, 0, 0, 0)
        self._btn_layout.setSpacing(4)
        layout.addWidget(self._container)
        layout.addStretch()

        self.refresh()

    def refresh(self):
        # Clear existing buttons
        while self._btn_layout.count():
            item = self._btn_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._buttons.clear()

        # Populate preset combo（選択はこのエリア固有の self._active を使う）
        presets = self._sm.get_quick_nav_presets()
        if self._active not in presets:
            # 編集で削除された場合等のフォールバック
            self._active = "default" if "default" in presets else \
                (sorted(presets.keys())[0] if presets else "default")

        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for name in sorted(presets.keys()):
            self._preset_combo.addItem(name)
        idx = self._preset_combo.findText(self._active)
        self._preset_combo.setCurrentIndex(max(0, idx))
        self._preset_combo.blockSignals(False)

        # Build buttons for this area's active preset
        for nav in presets.get(self._active, []):
            btn = QToolButton()
            btn.setText(nav.get("label", "?"))
            btn.setToolTip(nav.get("path", ""))
            path = nav.get("path", "")
            btn.clicked.connect(lambda checked=False, p=path: self.navigate_requested.emit(p))
            self._btn_layout.addWidget(btn)
            self._buttons.append(btn)

    def active_preset(self) -> str:
        return self._active

    def set_active_preset(self, name: str, notify: bool = True):
        """このエリアの選択プリセットを切り替えて再描画する。

        notify=False は «状態復元中» に使う（復元した path を
        プリセット切替の復元で上書きさせないため）。"""
        prev = self._active
        self._active = name
        self.refresh()
        if notify and prev != name:
            self.preset_changed.emit(prev, name)

    def _on_preset_changed(self, name: str):
        prev = self._active
        self._active = name
        self._sm.set("quick_nav_preset", name)   # 新規エリアの初期値として記憶
        # リンクONの時だけ他エリアへ伝播（OFFならこのエリアのみ）
        if self._sm.get("quick_nav_link_areas", False):
            for ref in list(QuickNavBar._instances):
                inst = ref()
                if inst is not None and inst is not self:
                    inst.set_active_preset(name)
        self.refresh()
        if prev != name:
            self.preset_changed.emit(prev, name)

    def _update_link_look(self):
        """🔗ボタンの表記（ON/OFF）を状態に合わせる。"""
        on = self._link_btn.isChecked()
        self._link_btn.setText("🔗 ON" if on else "🔗 OFF")

    def _on_link_toggled(self, on: bool):
        self._sm.set("quick_nav_link_areas", bool(on))
        self._update_link_look()
        # リンク状態はグローバルなので、他エリアのボタン表示も同期する
        for ref in list(QuickNavBar._instances):
            inst = ref()
            if inst is not None and inst is not self:
                inst._link_btn.blockSignals(True)
                inst._link_btn.setChecked(bool(on))
                inst._link_btn.blockSignals(False)
                inst._update_link_look()

    def _edit_presets(self):
        from ui.quick_nav_editor import QuickNavPresetEditor
        # 親は «トップレベルウィンドウ» にする（r63）。self（プリセット行）を親に
        # すると、行の QSS（QToolButton の pill: padding 1px 12px / radius 12）が
        # ダイアログへカスケードし、24px 固定の ▲▼ の内容領域が潰れて字が消える
        # （実機で「上下ボタンが出ない」原因。グローバル QSS の padding 修正
        #   だけでは直らなかった理由）。
        dlg = QuickNavPresetEditor(self._sm, parent=self.window())
        # 編集結果は «開いている全エリア» に反映する
        dlg.presets_saved.connect(QuickNavBar.refresh_all)
        dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()


# ---------------------------------------------------------------------------
# Maya連携の非同期通知（ソケット待ちをUIスレッドから隔離するため）
# ---------------------------------------------------------------------------

class _BridgeNotifier(QObject):
    """ワーカースレッドの送信結果をUIスレッドへqueued接続で運ぶ。"""
    done = Signal(bool, str, object)   # (ok, label, reply)
    conn_list = Signal(list, str)      # ([(port, label, ok, pid, dcc), ...], dcc) 接続候補スキャン結果


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

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
        self.statusBar().showMessage(f"ブックマークに追加: {added} 件")

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

    def _dcc_label(self, dcc=None) -> str:
        return "Blender" if (dcc or self._dcc) == "blender" else "Maya"

    def _populate_version_combos(self):
        """Maya / Blender のバージョンコンボをそれぞれ埋める（r67: 両方常時表示）。"""
        mc, bc = self._maya_combo, self._blender_combo
        mc.blockSignals(True)
        mc.clear()
        for inst in reversed(self._maya_installs):
            mc.addItem(f"Maya {inst.version}", inst)
        if not self._maya_installs:
            mc.addItem(tr("Maya（未検出）", "Maya (not found)"), None)
        sel = -1
        if self._maya_inst:
            for i in range(mc.count()):
                if mc.itemData(i) is self._maya_inst:
                    sel = i
        if sel >= 0:
            mc.setCurrentIndex(sel)
        if not isinstance(self._maya_inst, MayaInstallation):
            d = mc.currentData()
            if isinstance(d, MayaInstallation):
                self._maya_inst = d
        mc.blockSignals(False)

        bc.blockSignals(True)
        bc.clear()
        for inst in reversed(self._blender_installs):
            # r80: Store 版と通常インストール版が同じ版で同居した時に区別できるよう
            # WindowsApps 配下は「(Store)」を添える（ツールチップに exe パス）
            store = "windowsapps" in str(inst.executable).lower()
            bc.addItem(f"Blender {inst.version}" + (" (Store)" if store else ""), inst)
            bc.setItemData(bc.count() - 1, str(inst.executable), Qt.ToolTipRole)
        if not self._blender_installs:
            bc.addItem(tr("Blender（未検出）", "Blender (not found)"), None)
        d = bc.currentData()
        self._blender_inst = d if isinstance(d, BlenderInstallation) else None
        bc.blockSignals(False)

    def _on_blender_version_changed(self, idx: int):
        inst = self._blender_combo.itemData(idx)
        self._blender_inst = inst if isinstance(inst, BlenderInstallation) else None

    def _on_dcc_switched(self, dcc: str):
        dcc = "blender" if dcc == "blender" else "maya"
        if dcc == self._dcc:
            return
        self._dcc = dcc
        self._sm.set("dcc_target", dcc, save=False)
        self.statusBar().showMessage(
            tr("操作対象を %s に切り替えました", "Target DCC: %s") % self._dcc_label())

    def _launch_dcc(self):
        if self._dcc == "blender":
            self._launch_blender()
        else:
            self._launch_maya()

    def _launch_blender(self):
        inst = self._blender_inst
        if not inst:
            QMessageBox.warning(self, "Blender",
                                tr("Blender が見つかりません。ポータブル版は環境変数 "
                                   "MFM_BLENDER_EXE に blender.exe のパスを設定してください。",
                                   "Blender not found. For a portable build, set "
                                   "MFM_BLENDER_EXE to blender.exe."))
            return
        try:
            port = _bl.find_free_port()
            launch_blender(inst, command_port=port)
            self._bl_bridge.set_port(port)
            self._sm.set("blender_command_port", int(port), save=False)
            self.statusBar().showMessage(
                tr("Blender %s を起動しました（連携ポート :%d）",
                   "Launched Blender %s (bridge port :%d)") % (inst.version, port))
            for delay in (6000, 15000, 30000):
                QTimer.singleShot(delay, self._refresh_connections)
        except Exception as e:
            QMessageBox.critical(self, tr("起動エラー", "Launch Error"), str(e))

    def _refresh_connections(self):
        """Maya / Blender 両方の接続一覧を更新する（r67: 2つのコンボを常時表示）。"""
        if not self._inside_maya:
            self._refresh_maya_connections()
        self._refresh_blender_connections()

    # ---- 起動済みMaya/Blenderの列挙・接続先切替 ---------------------------

    # r78: 識別できた接続の記憶 {port: (label, pid)}。識別がタイムアウトしても
    # （Maya がシーン読込・レンダー・モーダル中で応答できない）一覧から消さず、
    # 前回のラベルに「…」を付けて残す。従来は 1 回の無応答で一覧から落ち、
    # 「Maya の数は変わらないのに更新のたびに増減する／送っても反応しない」に
    # 見えていた（2026-09-16 実機、mfm_maya.log）。
    def _refresh_maya_connections(self):
        """連携ポートを開いているMayaをスキャンし、識別情報つきで一覧化する。
        ソケットI/Oは全てワーカースレッドで行う（UIを止めない）。
        r78: スキャンが走行中なら重ねて走らせない（Maya の commandPort へ
        同時に複数接続すると応答が乱れる）。"""
        import threading
        if self._scan_running.get("maya"):
            return
        self._scan_running["maya"] = True

        def _run():
            items = []
            try:
                from core.maya_bridge import (bridge_log, usersetup_path,
                                              is_usersetup_installed)
                ports = scan_open_ports()
                try:
                    bridge_log("scan: open ports=%r / userSetup=%r installed=%s"
                               % (ports, usersetup_path(), is_usersetup_installed()))
                except Exception as _e:
                    _swallow(_e, "ui/main_window.py:879 _run")
                for port in ports:
                    label = None
                    try:
                        ok, reply = MayaBridge(port).send_python(
                            IDENTIFY_CODE, timeout=1.0)
                        # 応答にはスクリプト出力等が混入し得るため厳格に検証
                        # （生の応答をそのままラベルにするとレイアウトが壊れる）
                        parsed = parse_identify(reply) if ok else None
                        try:
                            bridge_log("port %d: ok=%s reply=%r parsed=%r"
                                       % (port, ok, (reply or "")[:120], parsed))
                        except Exception as _e:
                            _swallow(_e, "ui/main_window.py:892 _run")
                        pid = None
                        if parsed:
                            ver, scene, pid = parsed
                            scene = os.path.basename(scene)[:40] or \
                                tr("無題", "untitled")
                            label = f"Maya {ver[:20]} — {scene} (:{port})"
                            self._known_conns["maya"][port] = (label, pid)
                    except Exception:
                        pid = None
                    if label is None and port in self._known_conns["maya"]:
                        # 応答なし＝ビジー。前回の識別を保ち «…» を付ける
                        klabel, kpid = self._known_conns["maya"][port]
                        label, pid = klabel + " …", kpid
                    # (ポート, 表示ラベル, Mayaと確認できたか, PID, dcc)
                    items.append((port, label or f"Maya? (:{port})",
                                  label is not None, pid, "maya"))
                for port in list(self._known_conns["maya"]):
                    if port not in ports:
                        self._known_conns["maya"].pop(port, None)   # ポートが閉じた
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:913 _run")
            finally:
                self._scan_running["maya"] = False
            self._bridge_notify.conn_list.emit(items, "maya")

        threading.Thread(target=_run, daemon=True,
                         name="mfm-maya-scan").start()

    def _refresh_blender_connections(self):
        import threading
        if self._scan_running.get("blender"):
            return
        self._scan_running["blender"] = True

        def _run():
            items = []
            try:
                from core.maya_bridge import bridge_log
                ports = _bl.scan_open_ports()
                bridge_log("scan(blender): open ports=%r" % (ports,))
                for port in ports:
                    label = None
                    pid = None
                    try:
                        ok, reply = _bl.BlenderBridge(port).send_python(
                            _bl.IDENTIFY_CODE, timeout=1.5)
                        parsed = parse_identify(reply) if ok else None
                        bridge_log("blender port %d: ok=%s reply=%r parsed=%r"
                                   % (port, ok, (reply or "")[:120], parsed))
                        if parsed:
                            ver, scene, pid = parsed
                            scene = os.path.basename(scene)[:40] or tr("無題", "untitled")
                            label = f"Blender {ver[:20]} — {scene} (:{port})"
                            self._known_conns["blender"][port] = (label, pid)
                    except Exception as _e:
                        _swallow(_e, "ui/main_window.py:948 _run")
                    if label is None and port in self._known_conns["blender"]:
                        klabel, kpid = self._known_conns["blender"][port]
                        label, pid = klabel + " …", kpid
                    items.append((port, label or f"Blender? (:{port})",
                                  label is not None, pid, "blender"))
                for port in list(self._known_conns["blender"]):
                    if port not in ports:
                        self._known_conns["blender"].pop(port, None)
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:958 _run")
            finally:
                self._scan_running["blender"] = False
            self._bridge_notify.conn_list.emit(items, "blender")

        threading.Thread(target=_run, daemon=True, name="mfm-blender-scan").start()

    def _on_conn_list(self, items, dcc: str = None):
        """接続一覧の反映（DCC ごとのコンボへ）。items の第5要素が dcc。
        空リストは dcc 引数で判別（スキャン側が emit 時に渡す）。"""
        if dcc is None:
            dcc = items[0][4] if items and len(items[0]) > 4 else "maya"
        combo = self._bl_conn_combo if dcc == "blender" else getattr(self, "_conn_combo", None)
        if combo is None:
            return
        bridge = self._bl_bridge if dcc == "blender" else self._bridge
        port_key = "blender_command_port" if dcc == "blender" else "maya_command_port"
        cur_port = bridge.port
        combo.blockSignals(True)
        combo.clear()
        if not items:
            combo.addItem(tr("（%s なし）", "(no %s)") % self._dcc_label(dcc), None)
            combo.setEnabled(False)
        else:
            combo.setEnabled(True)
            pids = {it[0]: it[3] for it in items if it[3]}
            if dcc == "blender":
                self._bl_conn_pids = pids
            else:
                self._conn_pids = pids
            sel = -1
            first_ok = -1
            for i, it in enumerate(items):
                port, label, identified = it[0], it[1], it[2]
                combo.addItem(label, port)
                combo.setItemData(i, label, Qt.ToolTipRole)
                if port == cur_port:
                    sel = i
                if identified and first_ok < 0:
                    first_ok = i
            if sel < 0:
                # 現在の接続先が一覧に無い場合のみ切り替える。
                # 切替先は «識別できたポート» を優先（別アプリのポートへ
                # 送ってしまうと「何も起きない」ため）。
                sel = first_ok if first_ok >= 0 else 0
                new_port = combo.itemData(sel)
                bridge.set_port(new_port)
                self._sm.set(port_key, int(new_port), save=False)
            combo.setCurrentIndex(sel)
        combo.blockSignals(False)

    def _on_conn_changed(self, idx: int, dcc: str = None):
        dcc = dcc or self._dcc
        combo = self._bl_conn_combo if dcc == "blender" else getattr(self, "_conn_combo", None)
        if combo is None:
            return
        port = combo.itemData(idx)
        if port is None:
            return
        if dcc == "blender":
            self._bl_bridge.set_port(int(port))
            self._sm.set("blender_command_port", int(port), save=False)
        else:
            self._bridge.set_port(int(port))
            self._sm.set("maya_command_port", int(port), save=False)
        self.statusBar().showMessage(
            tr("%s 接続先を切替: %s", "%s connection switched: %s")
            % (self._dcc_label(dcc), combo.itemText(idx)))

    def _focus_connected_dcc(self):
        if self._dcc == "blender":
            pid = self._bl_conn_pids.get(self._bl_bridge.port)
            self._focus_pid_window(pid, "Blender", self._refresh_blender_connections)
        else:
            self._focus_connected_maya()

    def _install_freeze_watchdog(self):
        """常時有効: UIスレッドが1.5秒以上止まったら、その瞬間のメインスレッドの
        スタックを記録する（フリーズ原因の特定用）。出力先はツールフォルダ直下の
        mfm_freeze.log（+ MFM_DEBUG時は mfm_debug.log にも）。"""
        import threading
        import time as _time
        import sys as _sys
        import traceback as _tb
        from ui.browser_panel import _mfm_log
        freeze_log = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mfm_freeze.log")

        def _dump(text):
            try:
                with open(freeze_log, "a", encoding="utf-8") as f:
                    import datetime
                    f.write("[%s] %s\n"
                            % (datetime.datetime.now().strftime("%H:%M:%S"),
                               text))
            except OSError:
                pass
            _mfm_log(text)

        beat = [_time.monotonic()]

        # ── ネイティブ方式（GIL不要）: faulthandler の遅延ダンプを UI の
        # ハートビート毎に再アームする。UIスレッドが1.5秒止まるとCレベルの
        # 監視スレッドが «フリーズ最中の» 全スレッドのPythonスタックを書く。
        # （Pythonスレッドのサンプラは PySide の C++呼び出し中はGILを取れず、
        #  フリーズ終了後の位置しか記録できない＝真犯人の直前行しか見えない）
        try:
            import faulthandler
            self._fh_file = open(freeze_log, "a", encoding="utf-8")
            self._fh_file.write("\n[%s] === faulthandler 監視開始 ===\n"
                                % __import__("datetime").datetime.now()
                                .strftime("%H:%M:%S"))
            self._fh_file.flush()
            # クラッシュ（アクセス違反等）時にも Python スタックを同じログへ
            # 書く（r64。ネイティブダイアログでのプロセス消滅の切り分け用）
            try:
                faulthandler.enable(file=self._fh_file, all_threads=True)
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:1077 _dump")

            def _rearm():
                try:
                    faulthandler.dump_traceback_later(
                        1.5, repeat=False, file=self._fh_file, exit=False)
                except Exception as _e:
                    _swallow(_e, "ui/main_window.py:1084 _rearm")
            _rearm()
        except Exception:
            _rearm = None

        def _heartbeat():
            beat[0] = _time.monotonic()
            if _rearm is not None:
                _rearm()

        t = QTimer(self)
        t.setInterval(200)
        t.timeout.connect(_heartbeat)
        t.start()
        self._wd_timer = t
        main_id = threading.get_ident()

        def _watch():
            last_dump = 0.0
            while True:
                _time.sleep(0.5)
                now = _time.monotonic()
                stalled = now - beat[0]
                if stalled > 1.5 and now - last_dump > 5.0:
                    # r108: ドラッグ等 «意図して UI スレッドを握っている» 区間は
                    # 記録しない（本物のフリーズが埋もれるため）
                    try:
                        from ui.browser_panel import mfm_blocking_reason
                        if mfm_blocking_reason():
                            continue
                    except Exception as _e:
                        _swallow(_e, "ui/main_window.py:1115 _watch")
                    last_dump = now
                    try:
                        frm = _sys._current_frames().get(main_id)
                        if frm is not None:
                            _dump(
                                "=== UIフリーズ検出 (%.1f秒停止) メインスレッド ===\n%s"
                                % (stalled, "".join(_tb.format_stack(frm))))
                    except Exception as _e:
                        _swallow(_e, "ui/main_window.py:1124 _watch")

        threading.Thread(target=_watch, daemon=True,
                         name="mfm-freeze-watchdog").start()

    def _install_maya_bridge(self):
        """userSetup.py に連携スニペットを書き込み、ショートカット等から
        起動したMayaでも自動で commandPort が開くようにする。"""
        from core.maya_bridge import (install_usersetup, usersetup_path,
                                      is_usersetup_installed)
        installed = is_usersetup_installed()
        msg = tr(
            "全てのMayaが起動時に連携ポートを自動で開くように、\n"
            "以下のファイルへスニペットを書き込みます:\n%s\n\n"
            "これにより、マネージャー以外から起動したMayaも\n"
            "「接続:」リストに表示されるようになります。\n"
            "（次回のMaya起動から有効）\n\n実行しますか？",
            "Writes a snippet to the file below so every Maya opens a\n"
            "bridge port automatically at startup:\n%s\n\n"
            "Mayas launched outside this manager will then appear in\n"
            "the Connect list.\n(Takes effect from the next Maya launch)\n\n"
            "Proceed?") % usersetup_path()
        if installed:
            msg = tr("（既にインストール済みです。最新の内容に更新します）\n\n",
                     "(Already installed; will update to the latest snippet)\n\n"
                     ) + msg
        ret = QMessageBox.question(
            self, tr("Maya連携のインストール", "Install Maya Bridge"),
            msg, QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            p = install_usersetup()
            QMessageBox.information(
                self, tr("完了", "Done"),
                tr("インストールしました:\n%s\n\n"
                   "次回以降に起動したMayaが自動で接続可能になります。",
                   "Installed:\n%s\n\nMayas launched from now on will be "
                   "connectable automatically.") % p)
        except Exception as e:
            QMessageBox.critical(
                self, tr("インストール失敗", "Install Failed"), str(e))

    def _focus_connected_maya(self):
        """接続中のMayaのウィンドウを最前面に出す（Windows専用）。
        PIDは接続スキャン時の識別応答から取得済み。"""
        pid = self._conn_pids.get(self._bridge.port)
        self._focus_pid_window(pid, "Maya", self._refresh_maya_connections)

    def _focus_pid_window(self, pid, app_name: str, rescan):
        if not pid:
            self.statusBar().showMessage(
                tr("%sのPIDが未取得です。⟳で再スキャンしてください。",
                   "%s PID unknown. Rescan with ⟳ first.") % app_name)
            rescan()
            return
        if os.name != "nt":
            return
        try:
            import ctypes
            import ctypes.wintypes as wt
            u32 = ctypes.windll.user32
            found = []

            @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
            def _enum(hwnd, _lp):
                wpid = wt.DWORD(0)
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                # 可視のトップレベル（オーナー無し）＝メインウィンドウ候補
                if (wpid.value == pid and u32.IsWindowVisible(hwnd)
                        and not u32.GetWindow(hwnd, 4)):   # GW_OWNER=4
                    found.append(hwnd)
                return True

            u32.EnumWindows(_enum, 0)
            if not found:
                self.statusBar().showMessage(
                    tr("%sのウィンドウが見つかりません（PID %d）",
                       "%s window not found (PID %d)") % (app_name, pid))
                return
            hwnd = found[0]
            if u32.IsIconic(hwnd):
                u32.ShowWindow(hwnd, 9)    # SW_RESTORE
            u32.SetForegroundWindow(hwnd)
        except Exception as e:
            self.statusBar().showMessage(
                tr("最前面化に失敗: %s", "Failed to bring to front: %s") % e)

    def _bridge_send_async(self, code: str, label: str, timeout: float = 6.0,
                           bridge=None):
        """送信〜応答待ちをワーカースレッドで行う（UIを絶対に止めない）。
        Mayaがビジーだと commandPort の応答は数秒〜返ってこないことがあり、
        UIスレッドで recv を待つとクリックのたびにフリーズする。"""
        import threading
        bridge = bridge or self._bridge

        def _run():
            ok, reply = bridge.send_python(code, timeout=timeout)
            self._bridge_notify.done.emit(ok, label, reply)

        threading.Thread(target=_run, daemon=True,
                         name="mfm-dcc-send").start()

    def _on_bridge_done(self, ok: bool, label: str, reply):
        if ok:
            msg = tr("送信: %s", "Sent: %s") % label
            if reply is None:
                # r78: 送れたが応答が無い＝DCC 側がビジー（シーン読込中・レンダー中・
                # ダイアログ表示中）。黙っていると「何も反応しない」に見える。
                msg += tr("  — 応答なし（DCC 側が処理中かダイアログ待ちです。DCC の画面を確認してください）",
                          "  — no reply (the DCC is busy or waiting on a dialog; check its window)")
            elif reply and reply not in ("None", "0", "1"):
                msg += f"  →  {str(reply)[:120]}"
            self.statusBar().showMessage(msg, 15000 if reply is None else 0)
            if reply and str(reply).startswith("Error:"):
                QMessageBox.warning(self, tr("連携エラー", "Bridge Error"),
                                    "%s\n\n%s" % (label, str(reply)[:1500]))
        else:
            QMessageBox.critical(
                self, tr("連携エラー", "Bridge Error"), str(reply))

    # ---- Blender 連携（r65） -------------------------------------------------

    def _blender_send_or_prompt(self, code: str, label: str, log=None) -> bool:
        """log=(操作名, パス) を渡すと Blender 側の Info エディタにログを残す（r88）。"""
        if log:
            from core.dcc_log import wrap_blender
            code = wrap_blender(code, log[0], log[1])
        if self._bl_bridge.is_connected(timeout=0.3):
            self.statusBar().showMessage(
                tr("Blenderへ送信中: %s …", "Sending to Blender: %s …") % label)
            self._bridge_send_async(code, "Blender: " + label, timeout=8.0,
                                    bridge=self._bl_bridge)
            return True
        ver = self._blender_inst.version if self._blender_inst else ""
        ret = QMessageBox.question(
            self, tr("Blender未接続", "Blender Not Connected"),
            tr("連携できるBlenderが見つかりません。\n"
               "連携できるのは、このマネージャーの「起動」から起動したBlender、\n"
               "またはツールメニューで連携をインストール済みのBlenderです。\n\n"
               "Blender %s を今すぐ起動しますか？",
               "No connectable Blender found.\nOnly Blender launched from this "
               "manager, or with the bridge installed (Tools menu), can be controlled."
               "\n\nLaunch Blender %s now?") % ver,
            QMessageBox.Yes | QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._launch_blender()
        return False

    def _blender_open(self, path: str):
        self._blender_send_or_prompt(_bl.code_open(path),
                                     tr("開く %s", "Open %s") % Path(path).name,
                                     log=(tr("開く", "Open"), path))

    def _blender_import(self, path: str):
        self._blender_send_or_prompt(_bl.code_import(path),
                                     tr("インポート %s", "Import %s") % Path(path).name,
                                     log=(tr("インポート", "Import"), path))

    def _blender_link(self, path: str):
        self._blender_send_or_prompt(_bl.code_link(path),
                                     tr("リンク %s", "Link %s") % Path(path).name,
                                     log=(tr("リンク", "Link"), path))

    def _detect_blender(self):
        """設定 blender_exe（ユーザー指定）＋自動検出（標準配置・他ドライブ・
        レジストリ・Store エイリアス）。結果は mfm_startup.log にも残す（r71）。"""
        from ui.browser_panel import _mfm_timeline
        extra = [self._sm.get("blender_exe", "") or ""]
        try:
            installs = find_installed_blender_versions(extra)
        except Exception as e:
            installs = []
            _mfm_timeline("blender detect error: %r" % (e,))
        try:
            from core.blender_version import detection_report
            _mfm_timeline("blender detect: " + detection_report(extra, installs).replace("\n", " | "))
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:1302 _detect_blender")
        return installs

    def _locate_blender(self):
        """ツールメニュー「Blender の場所を指定...」: 自動検出で出ない Blender
        （標準外の場所／ポータブル版）の blender.exe を設定 blender_exe に記憶し、
        バージョン一覧とバッジのアイコンを更新する。"""
        start = ""
        if self._blender_inst:
            start = str(self._blender_inst.executable.parent)
        path, _ = QFileDialog.getOpenFileName(
            self, tr("blender.exe を選択", "Select blender.exe"), start,
            "Blender (blender.exe blender);;All (*)", options=QFileDialog.DontUseNativeDialog)
        if not path:
            return
        self._sm.set("blender_exe", path, save=True)
        self._blender_installs = self._detect_blender()
        self._populate_version_combos()
        # 指定したものを選択
        for i in range(self._blender_combo.count()):
            d = self._blender_combo.itemData(i)
            if isinstance(d, BlenderInstallation) and \
                    str(d.executable).lower() == os.path.normpath(path).lower():
                self._blender_combo.setCurrentIndex(i)
                self._blender_inst = d
                break
        try:
            m_exe = self._maya_inst.executable if self._maya_inst else None
            b_exe = self._blender_inst.executable if self._blender_inst else None
            self._dcc_hdr.set_app_icons(m_exe, b_exe)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:1333 _locate_blender")
        self.statusBar().showMessage(tr("Blender: %s", "Blender: %s") % path)

    def _install_blender_bridge(self):
        vers = [i.version for i in getattr(self, "_blender_installs", []) if i.version != "?"]
        dirs = _bl.startup_dirs(vers)
        if not dirs:
            QMessageBox.warning(self, "Blender",
                                tr("Blender の設定フォルダが見つかりません。",
                                   "Blender config folder not found."))
            return
        msg = tr(
            "全ての Blender が起動時に連携ポートを自動で開くように、\n"
            "以下へ mfm_bridge.py を配置します:\n%s\n\n"
            "これにより、マネージャー以外から起動した Blender も\n"
            "「接続:」リストに表示されます（次回の Blender 起動から有効）。\n\n実行しますか？",
            "Places mfm_bridge.py in the folders below so every Blender opens a\n"
            "bridge port at startup:\n%s\n\nBlenders launched outside this manager "
            "will then appear in the Connect list (from the next launch).\n\nProceed?"
        ) % "\n".join(str(d) for d in dirs)
        if QMessageBox.question(self, tr("Blender連携のインストール", "Install Blender Bridge"),
                                msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            written = _bl.install_startup(vers)
            QMessageBox.information(self, tr("完了", "Done"),
                                    tr("インストールしました:\n%s", "Installed:\n%s")
                                    % "\n".join(written))
        except Exception as e:
            QMessageBox.critical(self, tr("インストール失敗", "Install Failed"), str(e))

    # ---- 形式に応じた送り先の振り分け（r65） ----------------------------------
    # .blend → Blender / .ma .mb → Maya / 共通形式 → 選択中の DCC。
    # app を明示された時（D&D の落下先ウィンドウ）はそれを優先する。

    def _can_take_over_drop(self, paths, app: str):
        """DCC のウィンドウへの落下を «Manager がブリッジ経由で» 処理するか（r90）。

        True の時、DCC には空のファイル一覧が渡り、DCC 自身のドロップ処理は
        走らない。Windows の D&D は落とし先の処理が終わるまでドラッグ元を
        止めるため、DCC が落としたスクリプトを同期実行してダイアログを出すと
        Manager が固まる（install.py で 250 秒以上停止した実例）。
        ブリッジに繋がっていない／扱えない形式なら False（従来どおり DCC に任せる）。
        ※ Windows の COM 呼び出しの中で呼ばれるので、重い処理は禁止。"""
        # r91: 戻り値は «Manager が引き受けるファイルの一覧»（空＝引き受けない）。
        # 各ファイルは «実行するコマンドの対象か» で判定する（.py に «開く» を
        # 送らない）。引き受けないファイルは DCC に実ファイルとして渡る。
        from core import dcc_caps
        if self._inside_maya or not paths:
            return []
        bridge = self._bl_bridge if app == "blender" else self._bridge
        try:
            if not bridge.is_connected(timeout=0.2):
                return []
        except Exception:
            return []
        action = self._sm.get("dnd_action", "none")
        taken = []
        for p in paths:
            if app == "maya" and dcc_caps.supports("maya", "run_script", p):
                taken.append(p)               # スクリプトは Maya と同じく実行
            elif action != "none" and dcc_caps.supports(app, action, p):
                taken.append(p)
        return taken

    def _maya_run_script(self, path: str):
        """Maya へ落とした .py / .mel を «Maya と同じ作法で» 実行する（r90）。
        .py は Maya 標準の executeDroppedPythonFile（onMayaDroppedPythonFile を
        呼ぶ）を使い、無い版では同等の処理をする。.mel は source。
        インストーラがダイアログを出しても Manager は待たない（非同期送信）。
        ダイアログが裏に隠れないよう、送信後に Maya を前面に出す。"""
        if not self._dcc_accepts("run_script", path, "maya"):
            return
        if not self._dcc_once("run_script", path, "maya"):
            return
        p = escape_path(path)
        if os.path.splitext(path)[1].lower() == ".mel":
            inner = ("import maya.mel as _mel\n"
                     "_mel.eval('source \"%s\"')\n" % p)
        else:
            inner = (
                "import os, sys, runpy\n"
                "_p = '%s'\n"
                "_d = os.path.dirname(_p)\n"
                "try:\n"
                "    import maya.app.general.executeDroppedPythonFile as _edp\n"
                "    _edp.executeDroppedPythonFile(_p, '')\n"
                "except ImportError:\n"
                "    if _d not in sys.path:\n"
                "        sys.path.insert(0, _d)\n"
                "    _g = runpy.run_path(_p, run_name='__mfm_dropped__')\n"
                "    _f = _g.get('onMayaDroppedPythonFile')\n"
                "    if callable(_f):\n"
                "        _f('')\n" % p)
        inner = ("try:\n" + "".join("    " + l + "\n" for l in inner.splitlines())
                 + "except Exception as _e:\n"
                 "    _mfm_result = 'Failed: ' + str(_e)\n")
        code = "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result'))[1])({})" % inner
        if self._maya_send_or_prompt(
                code, tr("スクリプト実行 %s", "Run script %s") % Path(path).name,
                log=(tr("スクリプト実行", "Run script"), path)):
            QTimer.singleShot(400, self._focus_connected_maya)

    def _dcc_once(self, kind: str, path: str, app: str) -> bool:
        """«1操作＝1回» を保証するガード（r86）。

        同じ (種別, パス, 送り先) が 1.2 秒以内に再度来たら False を返して捨てる。
        Maya へのリファレンスが同一ファイルで2回実行される報告（2026-09-18）への
        対策。クリック動作・右クリック・D&D のどの経路から来ても、DCC へ送る
        直前のここを必ず通るので、発火元がどれでも止まる。
        捨てた時はログに残すので、mfm ログを見れば «どの経路が二重に呼んでいるか»
        を後から特定できる。"""
        import time as _time_mod
        from ui.browser_panel import _mfm_log
        try:
            key = (kind, os.path.normcase(os.path.abspath(path)), app or "")
        except Exception:
            key = (kind, path, app or "")
        now = _time_mod.monotonic()
        last_key, last_t = getattr(self, "_last_dcc_send", (None, 0.0))
        if key == last_key and (now - last_t) < 1.2:
            _mfm_log("dcc-send: 二重発火を抑止 kind=%s app=%s dt=%.3f path=%r"
                     % (kind, app, now - last_t, path))
            self.statusBar().showMessage(
                tr("連続した同一操作を1回にまとめました: %s",
                   "Merged a repeated action into one: %s") % Path(path).name, 4000)
            return False
        self._last_dcc_send = (key, now)
        return True

    def _dcc_accepts(self, command: str, path: str, app: str) -> bool:
        """«そのコマンドの対象か» の関所（r91、ユーザー指示: 対象外には絶対に
        反応しない）。対象外ならステータスに理由を出して False。"""
        from core import dcc_caps
        from ui.browser_panel import _mfm_log
        if dcc_caps.supports(app, command, path):
            return True
        name = "Blender" if app == "blender" else "Maya"
        msg = tr("スキップ: %s は %s の「%s」の対象外です",
                 "Skipped: %s is not a target of %s \"%s\"") % (
            Path(path).name, name, dcc_caps.command_label(command))
        _mfm_log("dcc-gate: " + msg)
        try:
            self.statusBar().showMessage(msg, 6000)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:1478 _dcc_accepts")
        return False

    def _dcc_open(self, path: str, app: str = None):
        target = app or dcc_for_path(path, self._dcc)
        if not self._dcc_accepts("open", path, target):
            return
        if not self._dcc_once("open", path, target):
            return
        if target == "blender":
            self._blender_open(path)
        else:
            self._maya_open(path)

    def _dcc_import(self, path: str, app: str = None):
        target = app or dcc_for_path(path, self._dcc)
        if not self._dcc_accepts("import", path, target):
            return
        if not self._dcc_once("import", path, target):
            return
        if target == "blender":
            self._blender_import(path)
        else:
            self._maya_import(path)

    def _dcc_reference(self, path: str, app: str = None, ask_ns: bool = True):
        target = app or dcc_for_path(path, self._dcc)
        if not self._dcc_accepts("reference", path, target):
            return
        if not self._dcc_once("reference", path, target):
            return
        if target == "blender":
            self._blender_link(path)
        else:
            self._maya_reference(path, ask_ns=ask_ns)

    def _maya_send_or_prompt(self, code: str, label: str, log=None) -> bool:
        """起動済みMaya（commandPort）へPythonコードを送る。未接続なら
        Mayaの起動を提案する。送信を開始できたら True。
        log=(操作名, パス) を渡すと Maya のスクリプトエディタにログを残す（r88）。"""
        if log:
            from core.dcc_log import wrap_maya
            code = wrap_maya(code, log[0], log[1])
        if self._bridge.is_connected(timeout=0.3):
            self.statusBar().showMessage(
                tr("Mayaへ送信中: %s …", "Sending to Maya: %s …") % label)
            self._bridge_send_async(code, label)
            return True
        ver = self._maya_inst.version if self._maya_inst else ""
        _port_hint = (f'  cmds.commandPort(name=":{self._bridge.port}", '
                      'sourceType="python")')
        ret = QMessageBox.question(
            self, tr("Maya未接続", "Maya Not Connected"),
            tr("連携できるMayaが見つかりません。\n"
               "連携できるのは、このマネージャーの「起動」ボタンから起動したMayaです。\n"
               "（手動起動のMayaと繋ぐ場合は、Mayaのスクリプトエディタで\n"
               "%s\nを実行してください）\n\n"
               "Maya %s を今すぐ起動しますか？",
               "No connectable Maya found.\n"
               "Only Maya launched from this manager's Launch button can be "
               "controlled.\n(To connect a manually launched Maya, run\n"
               "%s\nin Maya's Script Editor)\n\n"
               "Launch Maya %s now?") % (_port_hint, ver),
            QMessageBox.Yes | QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._launch_maya()
            self.statusBar().showMessage(
                tr("Mayaを起動中です。起動完了後にもう一度実行してください。",
                   "Launching Maya. Please retry after it finishes starting."))
        return False

    def _maya_local_setproject(self, path: str):
        """Maya 内起動時: workspace.mel があれば «プロジェクトをセットするか» を
        確認する（リモート時と同じコードをプロセス内で実行。r89）。"""
        from core.maya_project import setproject_code
        code = setproject_code(path)
        if code:
            try:
                exec(code, {})
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:1558 _maya_local_setproject")

    def _maya_local_log(self, action: str, path: str, what: str, err: str = ""):
        """Maya 内起動時のログ（スクリプトエディタ）。what: start/done/fail/cancel。"""
        from core.dcc_log import maya_local, maya_local_messages
        m = maya_local_messages(action, path)
        if what == "fail":
            maya_local(m["fail"] + err, "error")
        elif what == "cancel":
            maya_local(m["cancel"], "warning")
        else:
            maya_local(m[what], "info")

    def _maya_open(self, path: str):
        if not self._inside_maya:
            p = escape_path(path)
            # 未保存確認は «Maya側で» confirmDialog を出す。
            # マネージャーからのリモート照会（recv待ち）はMayaビジー時に
            # UIをフリーズさせるため行わない。
            _msg = tr("現在のシーンに未保存の変更があります。\\n破棄して開きますか？",
                      "The current scene has unsaved changes.\\nDiscard and open?")
            from core.maya_project import setproject_code
            inner = (
                "import maya.cmds as cmds\n"
                "_r = 'open'\n"
                "if cmds.file(q=True, modified=True):\n"
                "    _r = cmds.confirmDialog(title='Maya File Manager',"
                " message=u'%s',"
                " button=['open', 'cancel'], defaultButton='cancel',"
                " cancelButton='cancel', dismissString='cancel')\n"
                "if _r == 'open':\n"
                % _msg
                # r89: workspace.mel があれば «プロジェクトをセットするか» を確認
                + setproject_code(path, indent="    ")
                + "    cmds.file('%s', open=True, force=True, ignoreVersion=True)\n"
                "else:\n"
                "    _mfm_result = 'Cancelled'\n"
                % p
            )
            code = "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result'))[1])({})" % inner
            self._maya_send_or_prompt(
                code, tr("開く %s", "Open %s") % Path(path).name,
                log=(tr("開く", "Open"), path))
            return
        act = tr("開く", "Open")
        self._maya_local_log(act, path, "start")
        try:
            import maya.cmds as cmds
            if cmds.file(query=True, modified=True):
                ret = QMessageBox.question(
                    self, "未保存の変更",
                    "現在のシーンを保存しますか？",
                    QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
                )
                if ret == QMessageBox.Cancel:
                    self._maya_local_log(act, path, "cancel")
                    return
                if ret == QMessageBox.Save:
                    cmds.file(save=True)
            self._maya_local_setproject(path)
            cmds.file(path, open=True, force=True, ignoreVersion=True)
            self._maya_local_log(act, path, "done")
        except Exception as e:
            self._maya_local_log(act, path, "fail", str(e))
            QMessageBox.critical(self, "エラー", str(e))

    def _maya_import(self, path: str):
        if not self._inside_maya:
            p = escape_path(path)
            # r91: 形式ごとの取り込みプラグインを必ず先にロードする
            #（obj/abc/usd はプラグイン未ロードだと «未対応形式» で失敗する）
            from core.dcc_caps import MAYA_PLUGIN_FOR_EXT
            plug = MAYA_PLUGIN_FOR_EXT.get(Path(path).suffix.lower())
            body = ("    cmds.loadPlugin('%s', quiet=True)\n" % plug) if plug else ""
            if Path(path).suffix.lower() == ".fbx":
                body += "    cmds.file('%s', i=True, type='FBX')\n" % p
            else:
                body += ("    cmds.file('%s', i=True, ignoreVersion=True,"
                         " mergeNamespacesOnClash=False)\n" % p)
            # エラーはMaya側のダイアログで表示（リファレンスと同方針）
            from core.maya_project import setproject_code
            inner = (
                "import maya.cmds as cmds\n"
                + setproject_code(path) +      # r89: プロジェクトのセット確認
                "try:\n" + body +
                "except Exception as _e:\n"
                "    cmds.confirmDialog(title='Maya File Manager',"
                " message=u'%s\\n' + str(_e), button=['OK'])\n"
                "    _mfm_result = 'Failed: ' + str(_e)\n"
                % tr("インポートに失敗しました:", "Failed to import:")
            )
            code = "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result'))[1])({})" % inner
            self._maya_send_or_prompt(
                code, tr("インポート %s", "Import %s") % Path(path).name,
                log=(tr("インポート", "Import"), path))
            return
        act = tr("インポート", "Import")
        self._maya_local_log(act, path, "start")
        try:
            import maya.cmds as cmds
            self._maya_local_setproject(path)
            ext = Path(path).suffix.lower()
            from core.dcc_caps import MAYA_PLUGIN_FOR_EXT
            if MAYA_PLUGIN_FOR_EXT.get(ext):
                try:
                    cmds.loadPlugin(MAYA_PLUGIN_FOR_EXT[ext], quiet=True)
                except Exception as _e:
                    _swallow(_e, "ui/main_window.py:1665 _maya_import")
            if ext == ".fbx":
                from core.file_operations import fbx_import_maya
                fbx_import_maya(path)
            else:
                cmds.file(path, i=True, ignoreVersion=True,
                          mergeNamespacesOnClash=False)
            self._maya_local_log(act, path, "done")
        except Exception as e:
            self._maya_local_log(act, path, "fail", str(e))
            QMessageBox.critical(self, "インポートエラー", str(e))

    def _on_maya_drop(self, action: str, paths, app: str = None):
        """ブラウザから Maya/Blender のウィンドウへD&Dされた時のアクション実行。
        app は落下先のプロセス（"maya"/"blender"）。右クリックの一括経路からは
        None（形式と選択中 DCC で決める）。リファレンスはダイアログを出さず
        デフォルトNamespaceを使う（複数ファイルのD&Dで連続ダイアログにならないように）。"""
        from ui.browser_panel import _mfm_log
        # r86: 同一パスの重複を除去（選択やD&Dで同じパスが2つ入る経路がある）。
        # 「1操作＝1回」の最終的な保証は _dcc_once（DCC 送信の直前）で行う。
        seen = set()
        uniq = []
        for p in (paths or []):
            if not p:
                continue
            k = os.path.normcase(os.path.abspath(p))
            if k in seen:
                _mfm_log("dcc-dispatch: 重複パスを除去 action=%s path=%r" % (action, p))
                continue
            seen.add(k)
            uniq.append(p)
        paths = uniq
        if not paths:
            return
        if action in ("save_scene", "export_selection"):          # r70
            self._dcc_save_dialog(app or self._dcc,
                                  "export" if action == "export_selection" else "save",
                                  paths[0])
            return
        if action == "run_script":                                  # r90
            for p in paths:
                self._maya_run_script(p)
            return
        if action == "open":
            from core import dcc_caps
            tgt = [p for p in paths if dcc_caps.supports(
                app or dcc_for_path(p, self._dcc), "open", p)]
            self._dcc_open((tgt or paths)[0], app)
        elif action == "import":
            for p in paths:
                self._dcc_import(p, app)
        elif action == "reference":
            for p in paths:
                self._dcc_reference(p, app, ask_ns=False)
        elif action == "reference_ask":      # 右クリック単一: Namespace を確認
            self._dcc_reference(paths[0], app, ask_ns=True)

    # ---- DCC からの保存／書き出し（r70） ------------------------------------

    def _connected_scene_name(self, dcc: str) -> str:
        """接続コンボのラベル «Maya 2026 — scene.ma (:20261)» からシーン名を取る
        （スキャン時点のスナップショット。リモート照会はしない）。"""
        combo = self._bl_conn_combo if dcc == "blender" else getattr(self, "_conn_combo", None)
        if combo is None or combo.itemData(combo.currentIndex()) is None:
            return ""
        label = combo.currentText()
        if " — " not in label:
            return ""
        scene = label.split(" — ", 1)[1].rsplit(" (:", 1)[0].strip()
        return "" if scene in (tr("無題", "untitled"), "untitled") else scene

    def _dcc_save_dialog(self, dcc: str, mode: str, target: str):
        """右クリック「シーンを保存」「選択を書き出し」。target はフォルダ
        （空白右クリック）または単一ファイル（名前欄の初期値）。"""
        from ui.save_dialog import SaveDialog
        dcc = "blender" if dcc == "blender" else "maya"
        if os.path.isdir(target):
            folder, initial = target, ""
        else:
            folder, initial = os.path.dirname(target), os.path.basename(target)
        if not initial and mode == "save":
            initial = self._connected_scene_name(dcc)
        dlg = SaveDialog(dcc, mode, folder, initial, self._sm, parent=self.window())
        try:
            ret = dlg.exec_()
        except AttributeError:
            ret = dlg.exec()
        if ret != QDialog.Accepted:
            return
        path, code = dlg.result_path(), dlg.dcc_code()
        label = (tr("選択を書き出し %s", "Export Selection %s") if mode == "export"
                 else tr("シーンを保存 %s", "Save Scene %s")) % Path(path).name
        act = (tr("選択を書き出し", "Export Selection") if mode == "export"
               else tr("シーンを保存", "Save Scene"))
        if dcc == "blender":
            self._blender_send_or_prompt(code, label, log=(act, path))
        elif self._inside_maya:
            try:
                from core.dcc_log import wrap_maya
                res = eval(wrap_maya(code, act, path), {})   # プロセス内で実行（ログ付き）
                if str(res).startswith("Error:"):
                    QMessageBox.warning(self, tr("連携エラー", "Bridge Error"), str(res))
                else:
                    self.statusBar().showMessage(label)
            except Exception as e:
                QMessageBox.critical(self, tr("連携エラー", "Bridge Error"), str(e))
        else:
            self._maya_send_or_prompt(code, label, log=(act, path))

    def _maya_reference(self, path: str, ask_ns: bool = True):
        # デフォルトNamespace: ファイル名を「.」区切りした先頭（例: chr_A.v012.ma → chr_A）
        default_ns = Path(path).name.split(".")[0] or "ref"
        if not self._inside_maya:
            if ask_ns:
                ns, ok = QInputDialog.getText(
                    self, "Namespace",
                    tr("Namespace を入力:", "Enter namespace:"),
                    text=default_ns)
                if not ok:
                    return
            else:
                ns = default_ns
            ns = ((ns or default_ns).replace("'", "").replace('"', "").strip()
                  or default_ns)
            p = escape_path(path)
            # エラーはMaya側のダイアログで表示する（応答は待たない方針のため、
            # 握りつぶすと「何も起きない」ように見えてしまう）
            from core.maya_project import setproject_code
            from core.dcc_caps import MAYA_PLUGIN_FOR_EXT
            _plug = MAYA_PLUGIN_FOR_EXT.get(Path(path).suffix.lower())
            inner = (
                "import maya.cmds as cmds\n"
                + setproject_code(path)     # r89: % 書式の外（後続リテラルだけが書式対象）
                + (("try:\n    cmds.loadPlugin(%r, quiet=True)\n"
                    "except Exception:\n    pass\n" % _plug) if _plug else "") +  # r91
                "try:\n"
                "    cmds.file('%s', reference=True, namespace='%s',"
                " ignoreVersion=True, mergeNamespacesOnClash=False)\n"
                "except Exception as _e:\n"
                "    cmds.confirmDialog(title='Maya File Manager',"
                " message=u'%s\\n' + str(_e), button=['OK'])\n"
                "    _mfm_result = 'Failed: ' + str(_e)\n"
                % (p, ns,
                   tr("リファレンスに失敗しました:", "Failed to reference:"))
            )
            code = "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result'))[1])({})" % inner
            self._maya_send_or_prompt(
                code, tr("リファレンス %s", "Reference %s") % Path(path).name,
                log=(tr("リファレンス", "Reference") + " [ns:%s]" % ns, path))
            return
        try:
            import maya.cmds as cmds
            if ask_ns:
                ns, ok = QInputDialog.getText(
                    self, "Namespace",
                    tr("Namespace を入力:", "Enter namespace:"),
                    text=default_ns)
                if not ok:
                    return
            else:
                ns = default_ns
            act = tr("リファレンス", "Reference") + " [ns:%s]" % (ns or default_ns)
            self._maya_local_setproject(path)
            self._maya_local_log(act, path, "start")
            from core.dcc_caps import MAYA_PLUGIN_FOR_EXT
            _plug = MAYA_PLUGIN_FOR_EXT.get(Path(path).suffix.lower())
            if _plug:
                try:
                    cmds.loadPlugin(_plug, quiet=True)
                except Exception as _e:
                    _swallow(_e, "ui/main_window.py:1835 _maya_reference")
            try:
                cmds.file(path, reference=True, namespace=ns or default_ns,
                          ignoreVersion=True, mergeNamespacesOnClash=False)
            except Exception as e:
                self._maya_local_log(act, path, "fail", str(e))
                raise
            self._maya_local_log(act, path, "done")
        except Exception as e:
            QMessageBox.critical(self, "リファレンスエラー", str(e))

    # ------------------------------------------------------------------
    # Maya version (standalone)
    # ------------------------------------------------------------------

    def _on_maya_version_changed(self, idx: int):
        inst = self._maya_combo.itemData(idx)
        if isinstance(inst, MayaInstallation):
            self._maya_inst = inst
            self._sm.set_maya_version(inst.version)
            self.setWindowTitle(f"Maya File Manager  —  Maya {inst.version}")
        elif isinstance(inst, BlenderInstallation):
            self._blender_inst = inst

    def _launch_maya(self):
        inst = self._maya_inst
        if not inst:
            QMessageBox.warning(self, "エラー", "Maya バージョンが選択されていません。")
            return
        try:
            # 連携用 commandPort 付きで起動（スタンドアロンから開く/インポート/
            # リファレンスを送り込めるようにする）。複数Maya同時起動に備えて
            # レンジ内の空きポートを割り当て、起動したMayaへ接続先を切り替える。
            port = find_free_port()
            launch_maya(inst, command_port=port)
            self._bridge.set_port(port)
            self._sm.set("maya_command_port", int(port), save=False)
            self.statusBar().showMessage(
                tr("Maya %s を起動しました（連携ポート :%d）",
                   "Launched Maya %s (bridge port :%d)")
                % (inst.version, port))
            # Maya起動には時間がかかるため、少し置いて接続リストを再スキャン
            for delay in (8000, 20000, 40000):
                QTimer.singleShot(delay, self._refresh_maya_connections)
        except Exception as e:
            QMessageBox.critical(self, tr("起動エラー", "Launch Error"), str(e))

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------

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
        self.statusBar().showMessage("設定を適用しました")

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
            QMessageBox.information(self, "情報", "リネーム対象のファイルが選択されていません。")
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
