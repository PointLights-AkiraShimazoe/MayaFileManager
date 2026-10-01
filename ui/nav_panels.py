"""
Nav Panels
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

