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

import os
from pathlib import Path
from typing import Optional, List

from core.compat import (
    Qt, Signal,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QToolButton, QAction, QCheckBox,
    QMenu, QMenuBar, QStatusBar, QDockWidget, QToolBar,
    QSizePolicy, QSplitter, QFrame,
    QMessageBox, QFileDialog, QInputDialog,
    QSize, QFont, QColor
)
from core.settings_manager import SettingsManager
from core.bookmark_manager import BookmarkManager
from core.thumbnail_generator import ThumbnailManager
from core.maya_version import (
    MayaInstallation, find_installed_maya_versions,
    is_running_inside_maya, get_current_maya_version,
    launch_maya
)
from ui.browser_panel import BrowserPanel
from ui.bookmark_panel import BookmarkPanel
from ui.preset_editor import ReferencePresetEditor
from ui.settings_dialog import SettingsDialog
from ui.batch_rename_dialog import BatchRenameDialog
from ui.quick_nav_editor import QuickNavPresetEditor
from ui.reference_editor import ReferenceEditor
from ui.duplicate_folder_panel import DuplicateFolderPanel


# ---------------------------------------------------------------------------
# History Panel (inline – keeps it self-contained)
# ---------------------------------------------------------------------------

class HistoryPanel(QWidget):

    navigate_requested = Signal(str)

    def __init__(self, settings_manager: SettingsManager, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        from core.compat import QListWidget, QListWidgetItem, QAbstractItemView, QToolButton, QVBoxLayout, QHBoxLayout, QLabel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
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

    def __init__(self, settings_manager: SettingsManager, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        self._buttons: List[QToolButton] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # プリセット選択＋編集アイコンを最左に配置
        layout.addWidget(QLabel("プリセット:"))
        self._preset_combo = QComboBox()
        self._preset_combo.setFixedWidth(120)
        self._preset_combo.currentTextChanged.connect(self._on_preset_changed)
        layout.addWidget(self._preset_combo)

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

        # Populate preset combo
        presets = self._sm.get_quick_nav_presets()
        active = self._sm.get("quick_nav_preset", "default")

        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for name in sorted(presets.keys()):
            self._preset_combo.addItem(name)
        idx = self._preset_combo.findText(active)
        self._preset_combo.setCurrentIndex(max(0, idx))
        self._preset_combo.blockSignals(False)

        # Build buttons for active preset
        for nav in self._sm.get_active_quick_nav():
            btn = QToolButton()
            btn.setText(nav.get("label", "?"))
            btn.setToolTip(nav.get("path", ""))
            path = nav.get("path", "")
            btn.clicked.connect(lambda checked=False, p=path: self.navigate_requested.emit(p))
            self._btn_layout.addWidget(btn)
            self._buttons.append(btn)

    def _on_preset_changed(self, name: str):
        self._sm.set("quick_nav_preset", name)
        self.refresh()

    def _edit_presets(self):
        from ui.quick_nav_editor import QuickNavPresetEditor
        dlg = QuickNavPresetEditor(self._sm, parent=self)
        dlg.presets_saved.connect(self.refresh)
        dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()


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
        self._thumb_mgr = ThumbnailManager(
            cache_size=self._sm.get("thumbnail_cache_size", 256),
            thumb_size=self._sm.get("thumbnail_size", 128),
            parent=self,
        )

        self.setWindowTitle(
            f"Maya File Manager"
            + (f"  —  Maya {self._maya_ver}" if self._maya_ver else "")
        )
        self.setMinimumSize(1024, 640)

        self._build_ui()
        self._build_menu()
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

        # ── Bottom dock: duplicate folder finder ──────────────────────
        self._dup_panel = DuplicateFolderPanel(parent=self)
        self._dup_panel.navigate_requested.connect(self._browser.navigate_to)

        dup_dock = QDockWidget("重複フォルダ検出", self)
        dup_dock.setObjectName("DupFolderDock")
        dup_dock.setWidget(self._dup_panel)
        dup_dock.setVisible(False)   # hidden by default
        self.addDockWidget(Qt.BottomDockWidgetArea, dup_dock)
        self._dup_dock = dup_dock

        # ── Toolbar ───────────────────────────────────────────────────
        self._build_toolbar()

        # ── Status bar ────────────────────────────────────────────────
        sb = self.statusBar()

        self._status_path_label = QLabel("")
        self._status_path_label.setStyleSheet("color: #888; font-size: 11px;")
        sb.addWidget(self._status_path_label)

        sb.addPermanentWidget(QLabel(f"Maya {self._maya_ver}" if self._maya_ver else "Standalone"))

    # ------------------------------------------------------------------
    # ブラウザエリア管理（追加・削除・並び替え・状態保存）
    # ------------------------------------------------------------------

    def _add_area(self, state=None, after=None, save=True):
        """新しいブラウザエリアを追加する。after 指定でその直下に挿入。"""
        from ui.browser_area import BrowserArea
        area = BrowserArea(self._sm, self._thumb_mgr, self._bm_mgr, parent=self)
        # Maya連携（クリック動作）
        area.browser.set_open_callback(self._maya_open)
        area.browser.set_import_callback(self._maya_import)
        area.browser.set_reference_callback(self._maya_reference)
        area.bookmark_panel.open_requested.connect(self._maya_open)
        area.bookmark_panel.import_requested.connect(self._maya_import)
        area.bookmark_panel.reference_requested.connect(self._maya_reference)
        # 共有ハンドラ
        area.file_activated.connect(self._on_file_activated)
        area.directory_changed.connect(self._on_directory_changed)
        area.status_message.connect(self.statusBar().showMessage)
        area.bookmark_requested.connect(self._on_bookmark_requested)
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
            except Exception:
                pass

    def _save_areas_state(self):
        try:
            self._sm.set("browser_areas_state",
                         [a.get_state() for a in self._areas], save=False)
        except Exception:
            pass

    def _build_toolbar(self):
        tb = self.addToolBar("メイン")
        tb.setObjectName("MainToolBar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))

        # 前回のパスを復元（最左に配置）
        self._restore_check = QCheckBox("前回のパスを復元")
        self._restore_check.setChecked(self._sm.get("restore_last_path", False))
        self._restore_check.toggled.connect(
            lambda v: self._sm.set("restore_last_path", bool(v))
        )
        tb.addWidget(self._restore_check)
        # 伸縮スペーサーで以降（Maya/起動/クリック動作）を右寄せにする
        _rspacer = QWidget()
        _rspacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(_rspacer)

        # Maya version selector (standalone mode)
        if not self._inside_maya:
            tb.addWidget(QLabel("Maya:"))
            self._maya_combo = QComboBox()
            self._maya_combo.setFixedWidth(100)
            installations = find_installed_maya_versions(2023)
            for inst in reversed(installations):
                self._maya_combo.addItem(f"Maya {inst.version}", inst)
            # Pre-select
            if self._maya_inst:
                for i in range(self._maya_combo.count()):
                    if self._maya_combo.itemData(i) is self._maya_inst:
                        self._maya_combo.setCurrentIndex(i)
                        break
            # 既定選択（先頭＝最新）を _maya_inst に反映（「Mayaバージョンが選択されていません」防止）
            if not isinstance(self._maya_inst, MayaInstallation):
                _d = self._maya_combo.currentData()
                if isinstance(_d, MayaInstallation):
                    self._maya_inst = _d
            self._maya_combo.currentIndexChanged.connect(self._on_maya_version_changed)
            tb.addWidget(self._maya_combo)

            launch_btn = QPushButton("🚀 起動")
            launch_btn.clicked.connect(self._launch_maya)
            tb.addWidget(launch_btn)

        tb.addSeparator()

        # クリック動作（Maya内動作の設定）＋前回パス復元 — 最右端に配置
        tb.addWidget(QLabel("クリック動作:"))
        self._action_combo = QComboBox()
        self._action_combo.addItems(["プレビュー", "開く", "インポート", "リファレンス"])
        action_map = ["preview", "open", "import", "reference"]
        current_action = self._sm.get("single_click_action", "preview")
        if current_action in action_map:
            self._action_combo.setCurrentIndex(action_map.index(current_action))
        self._action_combo.currentIndexChanged.connect(
            lambda idx: self._sm.set("single_click_action", action_map[idx])
        )
        tb.addWidget(self._action_combo)

        # クイックナビ（プリセット）行は各ブラウザエリアが個別に持つ
        # （BrowserArea 内に配置。エリアごとに独立して操作できる）
        self._quick_nav = self._areas[0].quick_nav if self._areas else None

    def _build_menu(self):
        mb = self.menuBar()

        # ── File ─────────────────────────────────────────────────────
        file_menu = mb.addMenu("ファイル")

        if self._inside_maya:
            open_act = file_menu.addAction("開く...")
            open_act.triggered.connect(self._open_dialog)
            import_act = file_menu.addAction("インポート...")
            import_act.triggered.connect(self._import_dialog)
            ref_act = file_menu.addAction("リファレンス...")
            ref_act.triggered.connect(self._reference_dialog)
            file_menu.addSeparator()

        file_menu.addAction("終了").triggered.connect(self.close)

        # ── ブックマーク ──────────────────────────────────────────────
        bm_menu = mb.addMenu("ブックマーク")
        bm_menu.addAction("現在のフォルダをブックマーク").triggered.connect(
            lambda: self._bm_mgr.add_directory(self._browser.current_path())
        )

        # ── ツール ────────────────────────────────────────────────────
        tools_menu = mb.addMenu("ツール")

        tools_menu.addAction("バッチリネーム...").triggered.connect(
            self._open_batch_rename
        )
        tools_menu.addSeparator()
        tools_menu.addAction("リファレンスプリセットエディタ...").triggered.connect(
            self._open_preset_editor
        )
        tools_menu.addAction("リファレンスエディタ...").triggered.connect(
            self._open_reference_editor
        )
        tools_menu.addSeparator()
        tools_menu.addAction("重複フォルダ検出...").triggered.connect(
            self._toggle_dup_panel
        )
        tools_menu.addSeparator()
        tools_menu.addAction("設定...").triggered.connect(self._open_settings)

        # ── 表示 ──────────────────────────────────────────────────────
        view_menu = mb.addMenu("表示")
        # ブックマーク/履歴は各ブラウザエリアに常設のためdockメニューは廃止
        view_menu.addAction("ブラウザエリアを追加").triggered.connect(
            lambda: self._add_area(after=self._areas[-1] if self._areas else None)
        )
        view_menu.addAction("重複フォルダパネル").triggered.connect(self._toggle_dup_panel)

        # ── ヘルプ ────────────────────────────────────────────────────
        help_menu = mb.addMenu("ヘルプ")
        help_menu.addAction("バージョン情報").triggered.connect(self._about)

    # ------------------------------------------------------------------
    # File actions
    # ------------------------------------------------------------------

    def _on_file_activated(self, path: str):
        action = self._sm.get("double_click_action", "open")
        if action == "open":
            self._maya_open(path)
        elif action == "import":
            self._maya_import(path)
        elif action == "reference":
            self._maya_reference(path)

    def _on_directory_changed(self, path: str):
        try:
            self._status_path_label.setText(path)
        except Exception:
            pass
        # 全エリアの履歴パネルを更新（履歴データは共有のため）
        for a in getattr(self, "_areas", []):
            try:
                a.history_panel.refresh()
            except Exception:
                pass
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
            self, "ファイルを開く", "", "Maya Files (*.ma *.mb);;All (*.*)"
        )
        if path:
            self._maya_open(path)

    def _import_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "インポート", "",
            "3D Files (*.ma *.mb *.fbx *.obj *.abc);;All (*.*)"
        )
        if path:
            self._maya_import(path)

    def _reference_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "リファレンス", "",
            "Maya Files (*.ma *.mb *.fbx);;All (*.*)"
        )
        if path:
            self._maya_reference(path)

    # ------------------------------------------------------------------
    # Maya operations (inside-Maya callbacks)
    # ------------------------------------------------------------------

    def _maya_open(self, path: str):
        if not self._inside_maya:
            self.statusBar().showMessage(f"[Standalone] 開く: {path}")
            return
        try:
            import maya.cmds as cmds
            if cmds.file(query=True, modified=True):
                ret = QMessageBox.question(
                    self, "未保存の変更",
                    "現在のシーンを保存しますか？",
                    QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
                )
                if ret == QMessageBox.Cancel:
                    return
                if ret == QMessageBox.Save:
                    cmds.file(save=True)
            cmds.file(path, open=True, force=True, ignoreVersion=True)
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e))

    def _maya_import(self, path: str):
        if not self._inside_maya:
            self.statusBar().showMessage(f"[Standalone] インポート: {path}")
            return
        try:
            import maya.cmds as cmds
            ext = Path(path).suffix.lower()
            if ext == ".fbx":
                from core.file_operations import fbx_import_maya
                fbx_import_maya(path)
            else:
                cmds.file(path, i=True, ignoreVersion=True,
                          mergeNamespacesOnClash=False)
        except Exception as e:
            QMessageBox.critical(self, "インポートエラー", str(e))

    def _maya_reference(self, path: str):
        if not self._inside_maya:
            self.statusBar().showMessage(f"[Standalone] リファレンス: {path}")
            return
        try:
            import maya.cmds as cmds
            ns, ok = QInputDialog.getText(self, "Namespace",
                                          "Namespace を入力:", text="ref")
            if not ok:
                return
            cmds.file(path, reference=True, namespace=ns,
                      ignoreVersion=True, mergeNamespacesOnClash=False)
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

    def _launch_maya(self):
        inst = self._maya_inst
        if not inst:
            QMessageBox.warning(self, "エラー", "Maya バージョンが選択されていません。")
            return
        try:
            launch_maya(inst)
            self.statusBar().showMessage(f"Maya {inst.version} を起動しました")
        except Exception as e:
            QMessageBox.critical(self, "起動エラー", str(e))

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

    def _on_settings_changed(self):
        """Re-apply live settings to browsers and thumbnail manager."""
        for a in getattr(self, "_areas", []):
            try:
                a.browser.set_max_depth(self._sm.get("column_max_depth", 4))
                a.browser.set_thumb_size(self._sm.get("thumbnail_size", 128))
            except Exception:
                pass
        self._thumb_mgr.set_cache_size(self._sm.get("thumbnail_cache_size", 256))
        self.statusBar().showMessage("設定を適用しました")

    def _open_batch_rename(self):
        """Open batch rename for current browser selection (fallback: whole dir)."""
        import os
        # Try to get selected items from browser
        paths = self._browser._get_selected_paths() if hasattr(self._browser, "_get_selected_paths") else []
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

    def _toggle_dup_panel(self):
        visible = not self._dup_dock.isVisible()
        self._dup_dock.setVisible(visible)
        if visible:
            # Pre-fill with current directory
            self._dup_panel.set_root(self._browser.current_path())

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
            except Exception:
                pass
        if state:
            try:
                # version=3: プリセット行のエリア内移動に伴い旧状態を無効化
                self.restoreState(bytes.fromhex(state), 3)
            except Exception:
                pass

    def closeEvent(self, event):
        # version=3: ナビツールバー廃止（プリセット行はエリア内へ移動）に伴い
        # 旧ツールバー状態を無効化
        self._sm.set("window_geometry", self.saveGeometry().toHex().data().decode(), save=False)
        self._sm.set("window_state",    self.saveState(3).toHex().data().decode(), save=False)
        try:
            self._sm.set("last_path", self._browser.current_path(), save=False)
        except Exception:
            pass
        self._save_areas_state()
        self._sm.save()
        super().closeEvent(event)
