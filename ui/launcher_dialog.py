"""
Launcher Dialog
===============
Shown when MayaFileManager is started standalone.
Lets the user pick which Maya version to launch, optionally opening a file.
Auto-selects the version inferred from the file header.
"""

import os
from pathlib import Path
from typing import List, Optional

from core.compat import (
    Qt, Signal,
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QLineEdit,
    QCheckBox, QGroupBox, QSizePolicy,
    QFileDialog, QMessageBox,
    QPixmap, QIcon, QColor, QPainter, QFont, QSize,
    exec_app
)
from core.maya_version import (
    MayaInstallation, find_installed_maya_versions,
    detect_version_from_file, best_match, launch_maya,
)


# ---------------------------------------------------------------------------
# Launcher Dialog
# ---------------------------------------------------------------------------

class LauncherDialog(QDialog):
    """
    Standalone launcher window.

    Emits
    -----
    launch_requested(installation, file_path_or_None)
        When the user clicks Launch.
    open_manager_only()
        When the user wants to open the file manager without launching Maya.
    """

    launch_requested = Signal(object, object)  # MayaInstallation, Optional[str]
    open_manager_only = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Maya File Manager – Launcher")
        self.setMinimumWidth(540)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._installations: List[MayaInstallation] = []
        self._selected_file: Optional[str] = None

        self._build_ui()
        self._refresh_maya_versions()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setSpacing(12)
        root_layout.setContentsMargins(20, 20, 20, 20)

        # ── Header ───────────────────────────────────────────────────
        header_layout = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(self._make_app_icon(48))
        header_layout.addWidget(icon_label)

        title_layout = QVBoxLayout()
        title = QLabel("Maya File Manager")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        sub = QLabel("Maya バージョンを選択して起動")
        sub.setStyleSheet("color: #888;")
        title_layout.addWidget(title)
        title_layout.addWidget(sub)
        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        root_layout.addLayout(header_layout)

        # Divider
        root_layout.addWidget(self._make_divider())

        # ── Maya version group ────────────────────────────────────────
        ver_group = QGroupBox("Maya バージョン")
        ver_layout = QGridLayout(ver_group)
        ver_layout.setSpacing(8)

        ver_layout.addWidget(QLabel("インストール済み:"), 0, 0)
        self._version_combo = QComboBox()
        self._version_combo.setMinimumWidth(200)
        self._version_combo.currentIndexChanged.connect(self._on_version_changed)
        ver_layout.addWidget(self._version_combo, 0, 1)

        self._refresh_btn = QPushButton("🔄 再スキャン")
        self._refresh_btn.setFixedWidth(100)
        self._refresh_btn.clicked.connect(self._refresh_maya_versions)
        ver_layout.addWidget(self._refresh_btn, 0, 2)

        self._path_label = QLabel("")
        self._path_label.setStyleSheet("color: #666; font-size: 11px;")
        self._path_label.setWordWrap(True)
        ver_layout.addWidget(self._path_label, 1, 0, 1, 3)

        root_layout.addWidget(ver_group)

        # ── File group ────────────────────────────────────────────────
        file_group = QGroupBox("ファイル（省略可）")
        file_layout = QVBoxLayout(file_group)
        file_layout.setSpacing(6)

        file_row = QHBoxLayout()
        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText("Maya ファイルをドロップ、または参照…")
        self._file_edit.textChanged.connect(self._on_file_changed)
        file_row.addWidget(self._file_edit)

        browse_btn = QPushButton("参照…")
        browse_btn.setFixedWidth(70)
        browse_btn.clicked.connect(self._browse_file)
        file_row.addWidget(browse_btn)
        file_layout.addLayout(file_row)

        self._auto_detect_label = QLabel("")
        self._auto_detect_label.setStyleSheet("color: #4A9; font-size: 11px;")
        file_layout.addWidget(self._auto_detect_label)

        root_layout.addWidget(file_group)

        # Enable drag-drop on dialog
        self.setAcceptDrops(True)

        # ── Options ───────────────────────────────────────────────────
        opt_group = QGroupBox("オプション")
        opt_layout = QVBoxLayout(opt_group)
        self._open_manager_cb = QCheckBox("マネージャーウィンドウも開く")
        self._open_manager_cb.setChecked(True)
        opt_layout.addWidget(self._open_manager_cb)
        root_layout.addWidget(opt_group)

        # ── Buttons ───────────────────────────────────────────────────
        root_layout.addStretch()
        btn_layout = QHBoxLayout()

        manager_only_btn = QPushButton("マネージャーのみ開く")
        manager_only_btn.clicked.connect(self._on_manager_only)
        btn_layout.addWidget(manager_only_btn)
        btn_layout.addStretch()

        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        self._launch_btn = QPushButton("🚀  Maya 起動")
        self._launch_btn.setDefault(True)
        self._launch_btn.setMinimumWidth(140)
        self._launch_btn.setStyleSheet(
            "QPushButton { background: #4A90D9; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 6px 16px; }"
            "QPushButton:hover { background: #5AA0E9; }"
            "QPushButton:disabled { background: #444; color: #888; }"
        )
        self._launch_btn.clicked.connect(self._on_launch)
        btn_layout.addWidget(self._launch_btn)

        root_layout.addLayout(btn_layout)

        # ── Status bar area ───────────────────────────────────────────
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #E87; font-size: 11px;")
        root_layout.addWidget(self._status_label)

    # ------------------------------------------------------------------
    # Maya version scanning
    # ------------------------------------------------------------------

    def _refresh_maya_versions(self):
        self._status_label.setText("スキャン中…")
        self._version_combo.blockSignals(True)
        self._version_combo.clear()

        self._installations = find_installed_maya_versions(min_version=2023)

        if not self._installations:
            self._version_combo.addItem("Maya が見つかりません", None)
            self._launch_btn.setEnabled(False)
            self._status_label.setText("Maya のインストールが見つかりませんでした。")
        else:
            for inst in reversed(self._installations):  # Latest first
                self._version_combo.addItem(f"Maya {inst.version}", inst)
            self._launch_btn.setEnabled(True)
            self._status_label.setText("")

        self._version_combo.blockSignals(False)
        self._on_version_changed()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_version_changed(self):
        inst = self._version_combo.currentData()
        if inst and isinstance(inst, MayaInstallation):
            self._path_label.setText(str(inst.path))
        else:
            self._path_label.setText("")

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Maya ファイルを選択",
            str(Path.home()),
            "Maya Files (*.ma *.mb);;All Files (*.*)"
        )
        if path:
            self._file_edit.setText(path)

    def _on_file_changed(self, text: str):
        self._selected_file = text.strip() or None
        self._auto_detect_label.setText("")

        if not self._selected_file:
            return
        if not os.path.isfile(self._selected_file):
            self._auto_detect_label.setText("⚠ ファイルが見つかりません")
            self._auto_detect_label.setStyleSheet("color: #E87; font-size: 11px;")
            return

        detected = detect_version_from_file(self._selected_file)
        if detected:
            best = best_match(detected, self._installations)
            if best:
                # Select in combo
                for i in range(self._version_combo.count()):
                    inst = self._version_combo.itemData(i)
                    if inst and inst.version == best.version:
                        self._version_combo.setCurrentIndex(i)
                        break
                self._auto_detect_label.setText(
                    f"✓ ファイルの保存バージョン: Maya {detected}  → Maya {best.version} を自動選択"
                )
                self._auto_detect_label.setStyleSheet("color: #4A9; font-size: 11px;")
        else:
            self._auto_detect_label.setText("バージョン情報を取得できませんでした")
            self._auto_detect_label.setStyleSheet("color: #888; font-size: 11px;")

    def _on_launch(self):
        inst = self._version_combo.currentData()
        if not isinstance(inst, MayaInstallation):
            QMessageBox.warning(self, "エラー", "Maya バージョンが選択されていません。")
            return

        if self._selected_file and not os.path.isfile(self._selected_file):
            QMessageBox.warning(self, "エラー",
                                f"ファイルが見つかりません:\n{self._selected_file}")
            return

        try:
            launch_maya(inst, self._selected_file or None)
            self._launch_btn.setText("✓ 起動しました")
            self._launch_btn.setEnabled(False)
        except Exception as e:
            QMessageBox.critical(self, "起動エラー", str(e))
            return

        self.launch_requested.emit(inst, self._selected_file)

        if self._open_manager_cb.isChecked():
            self.accept()
        else:
            self.accept()

    def _on_manager_only(self):
        self.open_manager_only.emit()
        self.accept()

    # ------------------------------------------------------------------
    # Drag and drop (file path onto dialog)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self._file_edit.setText(path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_app_icon(size: int) -> "QPixmap":
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        # Gradient-like squares
        for i, (color, rect) in enumerate([
            ("#4A90D9", (4, 4, size - 8, size - 8)),
            ("#2A6090", (size // 4, size // 4, size // 2, size // 2)),
        ]):
            painter.setBrush(QColor(color))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(*rect, 6, 6)
        painter.setPen(QColor("#FFFFFF"))
        f = QFont()
        f.setPointSize(max(8, size // 5))
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(pm.rect(), Qt.AlignCenter, "MFM")
        painter.end()
        return pm

    @staticmethod
    def _make_divider() -> "QLabel":
        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #444;")
        return line

    # ------------------------------------------------------------------
    # Selected info (for main app to read after accept)
    # ------------------------------------------------------------------

    def selected_installation(self) -> Optional[MayaInstallation]:
        inst = self._version_combo.currentData()
        return inst if isinstance(inst, MayaInstallation) else None

    def selected_file(self) -> Optional[str]:
        return self._selected_file
