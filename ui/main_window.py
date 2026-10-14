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
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QApplication,
    QLabel, QComboBox, QToolButton, QMenu, QMenuBar, QSplitter, QMessageBox, QFileDialog, QInputDialog, QDialog,
    QTimer
)
from core.compat import QtCore as _QtCore
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
# r146: メニューの «開く / インポート / リファレンス» が NameError で
# 無反応だった（r145 でネイティブダイアログをやめた時の取りこぼし）。
from ui.dialog_util import pick_open_file, pick_directory  # noqa: F401
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

        # r119: «バージョン別の履歴/ブックマーク» は «Maya の中で動いている時»
        # だけのもの。スタンドアロンでは、起動する版を選び直しただけで箱が
        # 入れ替わると «ブックマークが消えた» に見える（ユーザー指摘）。
        if self._inside_maya and get_current_maya_version():
            self._sm.set_maya_version(get_current_maya_version())
        else:
            self._sm.set_maya_version(None)

        # Sub-managers
        self._bm_mgr = BookmarkManager(self._sm)
        # スタンドアロン⇔Maya 連携（commandPort ブリッジ）
        self._bridge = MayaBridge(
            port=int(self._sm.get("maya_command_port", 20261) or 20261))
        self._bridge_notify = _BridgeNotifier(self)
        self._bridge_notify.done.connect(self._on_bridge_done)
        self._bridge_notify.conn_list.connect(self._on_conn_list)
        self._bridge_notify.dcc_idle.connect(self._on_dcc_idle)   # r120
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
        # r144: 640px 下限だとエリアを縦に積んだ時に «ここから縮まない» に
        # なる（ユーザー指摘 2026-10-08）。実用に耐える最小まで下げる。
        self.setMinimumSize(720, 360)

        self._build_ui()
        self._build_menu()
        self._install_header()      # メニュー構築後（以降 self.menuBar() を呼ばない）
        self._restore_geometry()
        # r145: 最前面表示は «ジオメトリ復元の後» に当てる（フラグ変更で
        # ウィンドウが作り直されるため、先にやると復元した位置が飛ぶ）。
        if bool(self._sm.get(self.ALWAYS_ON_TOP_KEY, False)):
            QTimer.singleShot(0, lambda: self.set_always_on_top(True))
        # r146（ユーザー報告 2026-10-13）: コンパクトのまま終了すると、次の起動で
        # «本体も出ない・パックも出ない» になっていた。ACTIVE_KEY を保存する
        # だけで読み戻していなかったため。ここで復元する（本体を見せてからに
        # する。先に畳むと復元したジオメトリが確定しない）。
        if bool(self._sm.get("compact_mode", False)):
            QTimer.singleShot(0, self._restore_compact_mode)

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
        # r119g: エリアの «縦の高さ» は splitter 自身が持つ値で、
        # 各エリアの get_state() には入っていない。別に保存・復元する。
        # （従来は保存していなかったため、再起動のたびに均等割りに戻っていた）
        #
        # 復元が済むまでは保存しない。splitterMoved は «ユーザーが掴んで
        # 動かした時» だけでなく **起動直後のレイアウトでも発火** するため、
        # 素直に繋ぐと «均等割り» で上書きしてから復元することになり、
        # 保存値が毎回潰れる（この修正を入れた直後に実際に踏んだ）。
        self._area_sizes_ready = False
        self._areas_split.splitterMoved.connect(
            lambda *_a: self._save_area_sizes())
        QTimer.singleShot(0, self._restore_area_sizes)
        self._browser = self._areas[0].browser   # 互換エイリアス（既存機能の参照先）
        self._bookmark_panel = self._areas[0].bookmark_panel
        self._history_panel = self._areas[0].history_panel
        self._bm_dock = None
        self._hist_dock = None
        self.setCentralWidget(self._areas_split)
        # r130: エリアの表示方式（並列 / タブ）を復元する。
        self._active_area = 0
        mode = self._sm.get(self.AREA_VIEW_MODE_KEY, "split")
        self._area_view_mode = "tabs" if mode == "tabs" else "split"
        self._apply_area_view_mode()

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
        area.bookmark_panel.set_dcc_target_provider(lambda: self._dcc)   # r125
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
        # r130: 表示方式（並列 ⇄ タブ）
        area.view_mode_toggle_requested.connect(self._toggle_area_view_mode)
        area.tab_selected.connect(self._on_area_tab_selected)

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
        # 件数が変わると保存済みの割合は当てはまらない。以後は現状を保存する。
        self._area_sizes_ready = True
        new = self._add_area(after=area)
        if self._is_tab_mode():
            # r130: 追加したタブをそのまま開く（見えないまま増えても困る）
            try:
                self._active_area = self._areas.index(new)
            except ValueError:
                pass
            self._apply_area_view_mode()
        return new

    def _on_area_remove(self, area):
        if len(self._areas) <= 1 or area not in self._areas:
            return
        self._area_sizes_ready = True
        i = self._areas.index(area)
        self._areas.remove(area)
        area.setParent(None)
        area.deleteLater()
        # 互換エイリアスの付け替え
        self._bind_aliases(self._areas[0])
        self._refresh_area_headers()
        if self._is_tab_mode():
            # r130: 消したタブの位置を引き継ぐ（末尾を消したら一つ前）
            self._active_area = max(0, min(i, len(self._areas) - 1))
            self._apply_area_view_mode()
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
        if self._is_tab_mode():
            # r130: 表示中のタブを動かしたのだから、表示もそれに付いていく
            if self._active_area == i:
                self._active_area = j
            elif self._active_area == j:
                self._active_area = i
            self._apply_area_view_mode()
        self._save_areas_state()

    def _refresh_area_headers(self):
        n = len(self._areas)
        for i, a in enumerate(self._areas):
            try:
                a.set_index(i, n)
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:491 _refresh_area_headers")
        if self._is_tab_mode():
            labels = [tr("エリア %d", "Area %d") % (i + 1) for i in range(n)]
            act = max(0, min(getattr(self, "_active_area", 0), n - 1))
            for a in self._areas:
                try:
                    a.set_tabs(labels, act)
                except Exception as _e:
                    _swallow(_e, "ui/main_window.py _refresh_area_headers(tabs)")

    AREA_SIZES_KEY = "browser_area_sizes"

    def _save_areas_state(self):
        try:
            self._sm.set("browser_areas_state",
                         [a.get_state() for a in self._areas], save=False)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:498 _save_areas_state")
        self._save_area_sizes()

    AREA_VIEW_MODE_KEY = "area_view_mode"

    # ------------------------------------------------------------------
    # エリアの表示方式: 並列（縦スプリッタ）⇄ タブ（r130）
    # ------------------------------------------------------------------
    # タブ表示は «アクティブ以外を hide() する» だけ。QSplitter は隠れた子に
    # 場所を割かないので、並び順・状態保存・エリア操作はそのまま使える。
    # +▲▼✕ の意味も変えない（追加 / 上へ / 下へ / 削除）が、タブ表示中は
    # それが «表示中のタブ» に対する操作になる（ユーザー指示 2026-10-08）。

    def _is_tab_mode(self) -> bool:
        return getattr(self, "_area_view_mode", "split") == "tabs"

    def _toggle_area_view_mode(self):
        self._area_view_mode = "split" if self._is_tab_mode() else "tabs"
        try:
            self._sm.set(self.AREA_VIEW_MODE_KEY, self._area_view_mode)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _toggle_area_view_mode")
        self._apply_area_view_mode()

    def _apply_area_view_mode(self):
        n = len(self._areas)
        if n == 0:
            return
        self._active_area = max(0, min(getattr(self, "_active_area", 0), n - 1))
        tabs = self._is_tab_mode()
        labels = [tr("エリア %d", "Area %d") % (i + 1) for i in range(n)]
        for i, a in enumerate(self._areas):
            try:
                a.set_view_mode(self._area_view_mode)
                if tabs:
                    a.set_tabs(labels, self._active_area)
                a.setVisible((not tabs) or i == self._active_area)
            except Exception as _e:
                _swallow(_e, "ui/main_window.py _apply_area_view_mode")
        if tabs:
            self._bind_aliases(self._areas[self._active_area])
        else:
            self._bind_aliases(self._areas[0])
            QTimer.singleShot(0, self._restore_area_sizes)

    def _bind_aliases(self, area):
        """既存機能が見ている «代表エリア» の参照を付け替える。"""
        try:
            self._browser = area.browser
            self._bookmark_panel = area.bookmark_panel
            self._history_panel = area.history_panel
            self._quick_nav = area.quick_nav
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _bind_aliases")

    def _on_area_tab_selected(self, index: int):
        if not self._is_tab_mode():
            return
        if not (0 <= index < len(self._areas)) or index == self._active_area:
            return
        self._active_area = index
        self._apply_area_view_mode()

    def _save_area_sizes(self):
        """エリアの縦の高さを保存する（r119g）。

        QSplitter の sizes() は «ピクセル» なので、そのまま保存すると
        ウィンドウの大きさが変わった時に比率が狂う。割合で持つ。

        **復元が済むまでは書かない。** 起動直後のレイアウトでも
        splitterMoved が飛ぶため、保存値を均等割りで潰してしまう。"""
        if not getattr(self, "_area_sizes_ready", False):
            return
        if self._is_tab_mode():
            # r130: タブ表示中は 1 つしか見えていない。その比率を書くと
            # 並列に戻した時に «他が潰れた» 状態で復元される。
            return
        try:
            sizes = [int(x) for x in self._areas_split.sizes()]
            total = sum(sizes)
            if total <= 0 or len(sizes) != len(self._areas):
                return
            self._sm.set(self.AREA_SIZES_KEY,
                         [round(x / float(total), 6) for x in sizes],
                         save=False)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _save_area_sizes")

    def _restore_area_sizes(self):
        """保存した割合で高さを戻す。エリア数が変わっていたら何もしない。

        終わったら «以後は保存してよい» 印を立てる。"""
        if self._is_tab_mode():
            self._area_sizes_ready = True
            return
        try:
            ratios = self._sm.get(self.AREA_SIZES_KEY, None)
            if not isinstance(ratios, list) or len(ratios) != len(self._areas):
                self._area_sizes_ready = True      # 戻すものが無い＝以後は保存する
                return
            ratios = [max(0.0, float(r)) for r in ratios]
            total = sum(ratios)
            if total <= 0:
                self._area_sizes_ready = True
                return
            h = self._areas_split.height()
            if h <= len(self._areas):          # まだレイアウト前。もう一度待つ
                QTimer.singleShot(60, self._restore_area_sizes)
                return
            self._areas_split.setSizes(
                [max(1, int(round(h * r / total))) for r in ratios])
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _restore_area_sizes")
        self._area_sizes_ready = True

    def _install_header(self):
        """メニューバーを含む «ヘッダ行» を組み立てて setMenuWidget で置き換える。
        必ず _build_menu の後に1回だけ呼ぶ。以降 self.menuBar() を呼ぶと
        QMainWindow が新しい QMenuBar を作ってこのヘッダを削除してしまう。"""
        from core.compat import QSizePolicy
        # 【重要】QMainWindow::setMenuBar/setMenuWidget は «前のメニューバー» を
        # deleteLater する。そのため QMainWindow.menuBar() が作る既定バーは使わず、
        # _build_menu が自前の QMenuBar（self._menubar）にメニューを構築し、
        # ここでヘッダ行へ組み込む。以降どこでも self.menuBar() を呼ばないこと。
        from ui.dcc_header import _HeaderRow, PrefsBlock, LeadBlock
        mb = self._menubar
        mb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        # r134: メニューバーの真下の空きに «言語 / テーマ» のプルダウンを置く
        self._prefs_blk = PrefsBlock()
        self._prefs_blk.set_values(self._sm.get("ui_language", "auto"),
                                   self._sm.get("theme", "dark"))
        self._prefs_blk.language_changed.connect(self._on_language_changed)
        self._prefs_blk.theme_changed.connect(self._on_theme_changed)
        # r145: メニューバーの左隣に «最前面表示» と «コンパクトモード»
        self._lead_blk = LeadBlock()
        self._lead_blk.set_always_on_top(
            bool(self._sm.get(self.ALWAYS_ON_TOP_KEY, False)))
        self._lead_blk.always_on_top_toggled.connect(self.set_always_on_top)
        self._lead_blk.compact_toggled.connect(self.set_compact_mode)
        # r145: コンパクトから開く向き（右下→左下→左上→右上）
        self._lead_blk.set_open_dir(self._sm.get("compact_open_dir", "dr"))
        self._lead_blk.open_dir_changed.connect(self.set_compact_open_dir)
        # 手動レイアウト: 起動ボタンがウィンドウ中央に来るように DCC ブロックを配置
        header = _HeaderRow(mb, self._dcc_hdr, self._action_blk, self,
                            prefs_block=self._prefs_blk,
                            lead_block=self._lead_blk)
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
        self._dcc_hdr.maya_setup_requested.connect(self._open_maya_launch_setup)
        self._conn_combo.currentIndexChanged.connect(
            lambda i: self._on_conn_changed(i, "maya"))
        self._bl_conn_combo.currentIndexChanged.connect(
            lambda i: self._on_conn_changed(i, "blender"))
        QTimer.singleShot(1000, self._refresh_connections)
        # r119: Maya 連携は «セットアップの一部» なので、メニューを探させずに
        # 初回起動で聞く。ここで聞くのは «Maya が起動していなくても»
        # 実在するバージョンフォルダから場所を決められるから。
        # 後で Maya が繋がった時に食い違えば
        # _check_bridge_location_against_maya() が拾う。
        if not self._inside_maya:
            QTimer.singleShot(3500, self._maybe_offer_maya_bridge)

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
        # r136: 連携状態の自動更新。オフにすると従来どおり
        # «表示時に 1 回＋手動更新»。オンは即時反映。
        from ui.browser_panel import BrowserPanel as _BP
        self._integ_auto_act = edit_menu.addAction(
            tr("連携状態を自動で更新（%d 秒ごと／窓を戻した時）",
               "Refresh integration status automatically (every %ds / on focus)")
            % _BP.INTEG_AUTO_DEFAULT)
        self._integ_auto_act.setCheckable(True)
        self._integ_auto_act.setChecked(
            int(self._sm.get(_BP.INTEG_AUTO_KEY, _BP.INTEG_AUTO_DEFAULT) or 0) > 0)
        self._integ_auto_act.toggled.connect(self._on_integ_auto_toggled)
        # r141: DCC へ送ったらそのウィンドウを最前面へ（ユーザー指示 2026-10-08）
        self._raise_act = edit_menu.addAction(
            tr("送った DCC のウィンドウを最前面に出す",
               "Bring the target DCC window to front when sending"))
        self._raise_act.setCheckable(True)
        self._raise_act.setChecked(
            bool(self._sm.get(self.RAISE_ON_SEND_KEY, True)))
        self._raise_act.toggled.connect(
            lambda v: self._sm.set(self.RAISE_ON_SEND_KEY, bool(v)))

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
            # r119: userSetup は «次回起動から» しか効かない。作業中の Maya の
            # 救済手段をメニューの «すぐ隣» に置く（ここに無いと辿り着けない）
            tools_menu.addAction(
                tr("起動中の Maya を今すぐ接続...",
                   "Connect a running Maya now...")
            ).triggered.connect(self._connect_running_maya)
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
        path, _ = pick_open_file(
            self, "ファイルを開く", "", "Scenes (*.ma *.mb *.blend);;All (*.*)"
        )
        if path:
            self._dcc_open(path)

    def _import_dialog(self):
        path, _ = pick_open_file(
            self, "インポート", "",
            "3D Files (*.ma *.mb *.blend *.fbx *.obj *.abc *.usd *.usda *.usdc *.gltf *.glb);;All (*.*)"
        )
        if path:
            self._dcc_import(path)

    def _reference_dialog(self):
        path, _ = pick_open_file(
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

    def _show_tool_window(self, attr: str, factory, reuse: bool = True):
        """非モーダルの道具窓を開く（実体は ui/dialog_util）。"""
        from ui.dialog_util import show_tool_window, pick_directory, pick_open_file
        return show_tool_window(self, attr, factory, reuse=reuse)

    def _open_preset_editor(self):
        """リファレンスプリセットエディタ（r119: 非モーダル）。"""
        def _make():
            d = ReferencePresetEditor(self._sm, parent=self,
                                      bridge_cb=self._connected_maya_bridge)
            d.status_message.connect(
                lambda msg: self.statusBar().showMessage(msg, 8000))
            return d
        self._show_tool_window("_preset_editor_dlg", _make)

    def _connected_maya_bridge(self):
        """スタンドアロンから適用する時の送信先（接続中の Maya）。無ければ None。"""
        try:
            if self._inside_maya:
                return None
            combo = getattr(self, "_conn_combo", None)
            if combo is None or not combo.isEnabled() or combo.currentData() is None:
                return None
            return self._bridge
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _connected_maya_bridge")
            return None

    def _open_settings(self):
        """設定（r119: 非モーダル）。

        返り値を待たず settings_changed で受けているので止める理由が無い。
        設定を変えながら結果をその場で見られるようにする。"""
        def _make():
            d = SettingsDialog(self._sm, parent=self)
            d.settings_changed.connect(self._on_settings_changed)
            return d
        self._show_tool_window("_settings_dlg", _make)

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
                a.browser.set_slide_animation(      # r120
                    self._sm.get("column_slide_animation", True))
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:1937 _on_settings_changed")
        self._thumb_mgr.set_cache_size(self._sm.get("thumbnail_cache_size", 256))
        self.sync_prefs_block()        # r134: ヘッダのプルダウンとずれさせない
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
        # r119: 非モーダル。プレビューを見ながら元のカラムを確認できる。
        # 対象は «開いた時点の選択» なので、後ろで選択が変わっても影響しない。
        def _make(ps=list(paths)):
            d = BatchRenameDialog(ps, parent=self)
            d.renamed.connect(self._on_batch_renamed)
            return d
        # reuse=False: 対象は «呼んだ時の選択» なので使い回してはいけない
        self._show_tool_window("_batch_rename_dlg", _make, reuse=False)

    def _on_batch_renamed(self, results):
        """バッチリネームの後始末（r146、ユーザー報告 2026-10-13）。

        r145 まではステータスバーに件数を出すだけで、**ブラウザを一切
        更新していなかった**。os.rename は QFileSystemModel の外で起きるため、
        取りこぼすと «古い名前が残ったまま新しい名前が増える»＝新規作成された
        ように見える。変更があったフォルダを読み直させる。"""
        try:
            self.statusBar().showMessage(
                tr("%d 件リネーム完了", "Renamed %d item(s)") % len(results))
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _on_batch_renamed(status)")
        try:
            import os as _os
            dirs, seen = [], set()
            for old_p, _new_p in results or []:
                d = _os.path.dirname(_os.path.normpath(old_p))
                k = _os.path.normcase(d)
                if d and k not in seen:
                    seen.add(k)
                    dirs.append(d)
            for d in dirs[:8]:          # 深い階層を丸ごと変えた時の暴発止め
                self._browser._resync_dir(d)
        except Exception as e:
            self._log_quiet("batch rename refresh failed: %r" % (e,))

    def _open_reference_editor(self):
        """リファレンスエディタ（r119: 非モーダル）。

        «今のシーンの中身を直しながらファイルを探す» 使い方なので、
        これもマネージャーを塞いではいけない。"""
        self._show_tool_window("_reference_editor_dlg",
                               lambda: ReferenceEditor(parent=self))

    def _show_dock(self, dock):
        """閉じた/タブ化されたdockを確実に再表示して前面に出す。"""
        if dock is None:
            return
        dock.setVisible(True)
        dock.show()
        dock.raise_()

    # ------------------------------------------------------------------
    # 表示言語 / テーマ（ヘッダのプルダウン。r134）
    # ------------------------------------------------------------------

    def _on_language_changed(self, value: str):
        if self._sm.get("ui_language", "auto") == value:
            return
        try:
            self._sm.set("ui_language", value)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _on_language_changed")
        self._relaunch_ui(tr("表示言語を適用しています…",
                             "Applying the UI language…"))

    def _on_theme_changed(self, value: str):
        if self._sm.get("theme", "dark") == value:
            return
        try:
            self._sm.set("theme", value)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _on_theme_changed")
        self._relaunch_ui(tr("テーマを適用しています…",
                             "Applying the theme…"))

    # ------------------------------------------------------------------
    # テーマ / 言語の «プロセスを落とさない再適用»（r135）
    # ------------------------------------------------------------------
    # なぜ作り直すのか:
    #   apply_theme() を呼び直すだけでは足りない。UI のあちこちで
    #   setStyleSheet("... % qss_vars()") と «生成時の色を焼き込んでいる» ため、
    #   アプリ全体の QSS を差し替えても古い色が残る。tr() も同じで、
    #   文字列はウィジェットを作った時点の言語で固まっている。
    #   全部に refresh フックを生やすより、窓ごと作り直す方が取りこぼしが無い。
    # プロセス再起動にしない理由:
    #   Maya 内組込みでは Maya ごと落とすことになる。単一起動ガードとも
    #   競合する。窓だけ作り直せばどちらも避けられる。

    def _relaunch_ui(self, note: str = ""):
        if getattr(self, "_rebuilding", False):
            return None
        self._rebuilding = True
        overlay = self._show_busy_overlay(note)
        try:
            self._close_tool_windows()
            self._persist_for_relaunch()
            self._apply_prefs_now()
            new = self._spawn_replacement()
        except Exception as e:
            _swallow(e, "ui/main_window.py _relaunch_ui")
            self._rebuilding = False
            if overlay is not None:
                overlay.deleteLater()
            return None
        # 古い窓は «シグナルの外» で閉じる（コンボのスロットの中で自分を
        # 消すと C++ 側が死んだオブジェクトへ戻ってくる）。
        QTimer.singleShot(0, self._finish_relaunch)
        return new

    def _finish_relaunch(self):
        try:
            self.close()
            self.setParent(None)
            self.deleteLater()
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _finish_relaunch")

    def _show_busy_overlay(self, note: str):
        """«適用しています…» の覆い。作り直しの間のチラつきを隠す。"""
        if not note:
            return None
        try:
            from core.theme_engine import qss_vars
            v = qss_vars()
            ov = QWidget(self)
            ov.setObjectName("mfmBusyOverlay")
            ov.setAttribute(Qt.WA_StyledBackground, True)
            ov.setStyleSheet(
                "#mfmBusyOverlay{background:%(surface)s;}"
                "QLabel{color:%(on_surface)s;font-size:%(title_px)spx;}"
                % dict(v, title_px=v.get("label_px", 11)))
            lay = QVBoxLayout(ov)
            lab = QLabel(note, ov)
            lab.setAlignment(Qt.AlignCenter)
            lay.addWidget(lab)
            ov.setGeometry(self.rect())
            ov.show()
            ov.raise_()
            QApplication.processEvents()
            return ov
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _show_busy_overlay")
            return None

    def _close_tool_windows(self):
        """開いている非モーダルの道具窓を閉じる（古い窓の子なので残せない）。"""
        for attr in [a for a in list(vars(self)) if a.endswith("_dlg")]:
            try:
                d = getattr(self, attr, None)
                if d is not None:
                    d.close()
                    d.deleteLater()
                setattr(self, attr, None)
            except Exception as _e:
                _swallow(_e, "ui/main_window.py _close_tool_windows")

    def _persist_for_relaunch(self):
        """作り直し前に «復元に要るもの» を全部書き出す。

        set(..., save=False) で止まっている値が残っているので、最後に save()。"""
        try:
            self._sm.set("window_geometry",
                         self.saveGeometry().toHex().data().decode(), save=False)
            self._sm.set("window_state",
                         self.saveState(4).toHex().data().decode(), save=False)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _persist_for_relaunch(geom)")
        try:
            self._sm.set("last_path", self._browser.current_path(), save=False)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _persist_for_relaunch(path)")
        self._area_sizes_ready = True
        self._save_areas_state()
        try:
            self._sm.save()
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _persist_for_relaunch(save)")

    def _apply_prefs_now(self):
        """言語とテーマを «これから作るウィジェット» に効く形で適用する。"""
        from core import i18n
        from core.theme_engine import set_mode
        try:
            i18n.set_language(self._sm.get("ui_language", "auto"))
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _apply_prefs_now(lang)")
        mode = self._sm.get("theme", "dark")
        try:
            set_mode(mode)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _apply_prefs_now(mode)")
        # Maya 内では Maya 全体の見た目を壊すのでアプリ全体には当てない（r82）。
        if self._inside_maya:
            return
        try:
            from core.theme_engine import apply_theme
            app = QApplication.instance()
            if app is not None:
                apply_theme(app, mode=mode)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _apply_prefs_now(apply)")

    def _spawn_replacement(self):
        """同じ設定で MainWindow をもう一つ作って表に出す。"""
        new = MainWindow(self._sm, maya_installation=self._maya_inst)
        new.show()
        new.raise_()
        new.activateWindow()
        # 単一起動ガードの «2 つ目が来たら前面へ» が古い窓を指さないようにする。
        try:
            import main as _m
            if hasattr(_m, "register_main_window"):
                _m.register_main_window(new, in_maya=self._inside_maya)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py _spawn_replacement(register)")
        return new

    def sync_prefs_block(self):
        """設定ダイアログから変えられた時にヘッダの表示を合わせる（r134）。"""
        blk = getattr(self, "_prefs_blk", None)
        if blk is None:
            return
        blk.set_values(self._sm.get("ui_language", "auto"),
                       self._sm.get("theme", "dark"))

    # ------------------------------------------------------------------
    # 連携状態の自動更新（r136）
    # ------------------------------------------------------------------

    def _on_integ_auto_toggled(self, on: bool):
        from ui.browser_panel import BrowserPanel as _BP
        self._sm.set(_BP.INTEG_AUTO_KEY,
                     _BP.INTEG_AUTO_DEFAULT if on else 0)
        for a in getattr(self, "_areas", []):
            try:
                a.browser.apply_integration_auto_refresh()
            except Exception as _e:
                _swallow(_e, "ui/main_window.py _on_integ_auto_toggled")
        self.statusBar().showMessage(
            tr("連携状態の自動更新を入にしました",
               "Automatic integration refresh is on") if on else
            tr("連携状態は表示時と手動更新のみにしました",
               "Integration status now updates on open and manual refresh only"),
            6000)

    def changeEvent(self, event):
        """窓が前面に戻ったら連携状態を見直す（r136）。

        他アプリ（P4V 等）でチェックアウトして戻ってくるのが典型。

        r136b: **ここでは何もしない。** changeEvent は Qt の都合で呼ばれ、
        窓の破棄中にも来る。その最中にエリアやブラウザを辿ると、
        すでに C++ 側が消えているものを掴むことがある。次のイベントループへ
        逃がしておけば、その時点で窓が生きているかを見てから触れられる。"""
        try:
            if (event is not None
                    and event.type() == _QtCore.QEvent.ActivationChange):
                QTimer.singleShot(0, self._refresh_integrations_if_active)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py changeEvent")
        super().changeEvent(event)

    def _refresh_integrations_if_active(self):
        try:
            if not self.isVisible() or not self.isActiveWindow():
                return
            areas = list(getattr(self, "_areas", []) or [])
        except RuntimeError:
            return                      # 窓がもう無い
        for a in areas:
            try:
                a.browser.refresh_integrations_on_activate()
            except (RuntimeError, AttributeError):
                pass
            except Exception as _e:
                _swallow(_e, "ui/main_window.py _refresh_integrations_if_active")

    LOGO_FILE = "pls_logo.png"

    @staticmethod
    def _logo_pixmap(width=240):
        """PointLights のロゴを読む（r131）。無ければ None。

        原本は CMYK の JPEG（BREST/Logo/PLs）。Qt は CMYK JPEG を確実には
        読めないので、RGB の PNG に落としたものを同梱している。"""
        import os as _os
        from core.compat import QPixmap
        path = _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))), "resources", "icons",
            MainWindow.LOGO_FILE)
        if not _os.path.isfile(path):
            return None
        pm = QPixmap(path)
        if pm.isNull():
            return None
        if pm.width() > width:
            pm = pm.scaledToWidth(width, Qt.SmoothTransformation)
        return pm

    def _about(self):
        """バージョン情報（r131: 非モーダル）。

        r119 の «原則 Manager を止めない» に合わせ、他の道具窓と同じ扱いにする。"""
        self._show_tool_window("_about_dlg", self._build_about_dialog)

    def _build_about_dialog(self, parent=None):
        parent = parent if parent is not None else self
        # r119: バージョンは core/version.py を唯一の出所にする
        # （ここに手書きしていたため «v1.0» のまま取り残されていた）。
        from core.version import version_string, COPYRIGHT
        from core.compat import QDialog, QVBoxLayout, QHBoxLayout, QPushButton
        from core.theme_engine import qss_vars
        v = qss_vars()
        body = ("Maya File Manager %s\n\n%s\nPySide2 / PySide6\n\n%s"
                % (version_string(),
                   tr("Maya 2023 以降対応", "Supports Maya 2023 and later"),
                   COPYRIGHT))
        dlg = QDialog(parent)
        dlg.setWindowTitle(tr("バージョン情報", "About"))
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 18, 20, 14)
        lay.setSpacing(12)
        pm = self._logo_pixmap(240)
        if pm is not None:
            # ロゴは白地。ダークでもライトでも読めるよう白いカードに載せる。
            holder = QLabel(dlg)
            holder.setPixmap(pm)
            holder.setAlignment(Qt.AlignCenter)
            # ロゴは «白地の上に成り立つ意匠» の絵柄なので、ダークでも白い台に載せる。
            # その «テーマによらず白» をトークン artwork_backdrop として持つ（r131）。
            holder.setStyleSheet(
                "background:%(artwork_backdrop)s;border-radius:%(r_m)spx;"
                "padding:10px;" % v)
            lay.addWidget(holder, 0, Qt.AlignHCenter)
        txt = QLabel(body, dlg)
        txt.setAlignment(Qt.AlignHCenter)
        txt.setTextInteractionFlags(Qt.TextSelectableByMouse)
        txt.setStyleSheet("color:%(on_surface)s;" % v)
        lay.addWidget(txt)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton(tr("閉じる", "Close"), dlg)
        ok.setDefault(True)
        ok.clicked.connect(dlg.accept)
        row.addWidget(ok)
        lay.addLayout(row)
        return dlg

    # ------------------------------------------------------------------
    # Window state
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 最前面表示 / コンパクトモード（r145、ユーザー指示 2026-10-09）
    # ------------------------------------------------------------------
    ALWAYS_ON_TOP_KEY = "always_on_top"

    def is_always_on_top(self) -> bool:
        return bool(getattr(self, "_on_top", False))

    def set_always_on_top(self, on: bool):
        """すべてのウィンドウの上に出す／やめる（r146 で実装を入れ替え）。

        r145 までは ``setWindowFlag(Qt.WindowStaysOnTopHint)`` でやっていたが、
        これには看過できない副作用が 3 つあった（ユーザー報告 2026-10-11）。

        1. タイトルバーの ✕ / 最小化 / 最大化 が付かなくなる。Qt の
           ``adjustFlags()`` は «カスタマイズ系のヒントが 1 つでも立っていたら
           既定の部品を足さない» ので、最前面表示を入れた瞬間に ✕ が灰色の
           «押せないボタン» になる。
        2. ウィンドウが作り直されるので、切り替えのたびに白い枠が一瞬見える。
        3. **子ダイアログが本体の裏に回る。** Windows の TOPMOST は «TOPMOST
           でない窓は絶対に上に来られない» という規則なので、確認ダイアログや
           保存ダイアログが本体の裏で開く。モーダルなら入力はそこで止まるので、
           ユーザーから見ると «削除もエクスプローラー表示も保存も無反応»。

        r146 は Win32 の SetWindowPos を直接呼ぶ（core/win_topmost.py）。
        Qt のフラグを触らないので 1・2 は起きない。3 は TopmostKeeper が
        «新しく出た窓も一緒に最前面へ上げる» ことで消す。"""
        on = bool(on)
        self._on_top = on
        try:
            self._sm.set(self.ALWAYS_ON_TOP_KEY, on)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py set_always_on_top(save)")
        try:
            from core.win_topmost import set_topmost
            set_topmost(self, on)
            self._topmost_keeper().set_active(on)
        except Exception as e:
            self._log_quiet("always_on_top failed: %r" % (e,))
        try:
            self._lead_blk.set_always_on_top(on)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py set_always_on_top(ui)")

    def _topmost_keeper(self):
        """最前面表示の «お守り役»（遅延生成）。"""
        k = getattr(self, "_topmost_keep", None)
        if k is None:
            from ui.topmost_keeper import TopmostKeeper
            k = TopmostKeeper(self)
            self._topmost_keep = k
        return k

    def _compact(self):
        """コンパクトモードの制御（遅延生成）。"""
        c = getattr(self, "_compact_ctl", None)
        if c is None:
            from ui.compact_mode import CompactController
            c = CompactController(self, self._sm)
            self._compact_ctl = c
        return c

    def set_compact_mode(self, on: bool):
        """コンパクトモードの入り切り（r145 / r146 で方針変更）。

        r145 は «入る時に最前面表示も一緒に入れる» ことでパックが Maya の裏へ
        回るのを防いでいた。r146 でこれをやめる（ユーザー指示 2026-10-11:
        «常に最前面にしていない時に、どこに行ったかがわからなくなります»）。
        最前面表示は **ユーザーが決めるもの** で、コンパクトモードが勝手に
        触ってよいものではない。代わりに

          * パック自身を «常に手前» の小窓にする（本体の設定とは無関係）
          * パックを **タスクバーに出す**（Qt.Tool をやめて Qt.Window に）

        の 2 つで «見失わない» を担保する。"""
        self._compact().set_active(bool(on))

    def enter_compact_mode(self):
        self.set_compact_mode(True)

    def leave_compact_mode(self):
        self.set_compact_mode(False)

    def is_compact_mode(self) -> bool:
        return self._compact().is_active()

    def set_compact_open_dir(self, d: str):
        """コンパクトから開く向きを変える（r145、指示 2026-10-09）。"""
        self._compact().set_open_dir(d)
        try:
            self._lead_blk.set_open_dir(self._compact().open_dir())
        except Exception as _e:
            _swallow(_e, "ui/main_window.py set_compact_open_dir")

    def _restore_compact_mode(self):
        """起動時にコンパクトモードを復元する（r146）。

        «見失わない» ことを最優先にする。パックを出せなかった場合は
        コンパクトを諦めて通常表示に戻す（CompactController 側で面倒を見る）。"""
        try:
            if not self.isVisible():
                self.show()           # ジオメトリを確定させてから畳む
            self._compact().restore_from_settings()
        except Exception as e:
            self._log_quiet("compact restore failed: %r" % (e,))
            try:
                self._compact().set_active(False)
            except Exception as _e:
                _swallow(_e, "ui/main_window.py _restore_compact_mode")
        self.sync_compact_button()

    def sync_compact_button(self):
        """ヘッダのボタンの «光っている/いない» を今の状態に合わせる。"""
        try:
            self._lead_blk.set_compact(self._compact().is_active())
        except Exception as _e:
            _swallow(_e, "ui/main_window.py sync_compact_button")

    def _restore_geometry(self):
        geom = self._sm.get("window_geometry")
        state = self._sm.get("window_state")
        if geom:
            try:
                self.restoreGeometry(bytes.fromhex(geom))
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:2000 _restore_geometry")
            # r145: 保存位置が «今つながっている画面の外» なら引き戻す。
            # モニタ構成が変わった（サブを外した・配置を変えた）後に起動すると、
            # 画面外に開いて «起動しない» ように見える（実機 2026-10-09:
            # 保存値が x=-2143, y=-606 / screenNumber=3 だった）。
            self._pull_onto_screen()
        if state:
            try:
                # version=3: プリセット行のエリア内移動に伴い旧状態を無効化
                # version=4: ツールバー廃止（メニューバー右肩へ統合）で旧状態を無効化
                self.restoreState(bytes.fromhex(state), 4)
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:2007 _restore_geometry")

    def _pull_onto_screen(self, min_visible: int = 120):
        """ウィンドウが «どの画面にも十分に載っていない» なら中央へ戻す。

        min_visible: 載っていると見なす最小の重なり（px 四方）。タイトルバーを
        掴める程度は見えていないと «起動しない» のと同じ。"""
        from core.compat import QApplication
        try:
            frame = self.frameGeometry()
            best = 0
            for sc in QApplication.screens():
                a = sc.availableGeometry().intersected(frame)
                if a.isValid():
                    best = max(best, min(a.width(), a.height()))
            if best >= min_visible:
                return
            sc = QApplication.primaryScreen()
            if sc is None:
                return
            av = sc.availableGeometry()
            w = min(self.width() or 1200, max(1, av.width() - 40))
            h = min(self.height() or 800, max(1, av.height() - 60))
            self.resize(max(w, self.minimumWidth()), max(h, self.minimumHeight()))
            g = self.frameGeometry()
            g.moveCenter(av.center())
            self.move(g.topLeft())
            self._log_quiet("geometry: 画面外だったので中央へ戻した frame=%r"
                            % (frame,))
        except Exception as e:
            self._log_quiet("pull_onto_screen failed: %r" % (e,))

    @staticmethod
    def _log_quiet(msg: str):
        """ログに残すだけ（失敗しても何もしない）。"""
        try:
            from ui.browser_panel import _mfm_log
            _mfm_log(msg)
        except Exception:
            pass

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
        # Undo 用の作業ごみ箱（削除の取り消し先）を OS のごみ箱へ送る（r62）。
        # r135: ただしテーマ/言語の作り直しは «終了» ではないので捨てない。
        # ここで捨てると、テーマを変えただけで Undo が全部吸われる。
        if not getattr(self, "_rebuilding", False):
            try:
                from core.undo_stack import get_undo_stack
                get_undo_stack().dispose_all()
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:2025 closeEvent")
        super().closeEvent(event)
