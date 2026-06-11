"""
Quick Look — Spaceキー即時プレビュー (N-2)
==========================================

選択ファイルをモードレスウィンドウで即時プレビューする。
macOS Finder / OneCommander の Quick Look 相当。

対応コンテンツ:
- 画像 (.png .jpg .jpeg .bmp .gif .webp .tga ...): Qt が読める形式はスケール表示
- テキスト (.txt .md .json .py .mel .log ...): 先頭 64KB を等幅表示
- .ma: Maya ASCII バージョン + 先頭プレビュー
- .mb: Maya バージョン + ファイル情報
- その他: ファイル情報カード（名前・サイズ・更新日時）

操作: Space で開閉トグル / Esc で閉じる / 選択変更で内容追従
"""

import os
from datetime import datetime
from pathlib import Path

from core.compat import (
    Qt, QSize,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QStackedWidget,
    QPixmap, QFont,
)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp",
               ".tga", ".tif", ".tiff", ".ico", ".svg"}
_TEXT_EXTS = {".txt", ".md", ".json", ".py", ".mel", ".log", ".csv",
              ".xml", ".yaml", ".yml", ".ini", ".cfg", ".usda", ".obj"}
_TEXT_PREVIEW_BYTES = 65536


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{num_bytes} B"


class QuickLookWindow(QWidget):
    """モードレスのプレビューウィンドウ。show_for(path) で内容を切替える。"""

    def __init__(self, parent=None):
        # Qt.Tool: タスクバーに出さない補助ウィンドウ
        super().__init__(parent, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Quick Look")
        self.resize(760, 540)
        self._current_path = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # ── ヘッダー: ファイル名 + メタ情報 ──
        self._title_label = QLabel("")
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        self._title_label.setFont(f)
        self._title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._title_label)

        self._meta_label = QLabel("")
        self._meta_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._meta_label)

        # ── コンテンツ: 画像 / テキスト / 情報カード ──
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, stretch=1)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setMinimumSize(QSize(200, 150))
        self._stack.addWidget(self._image_label)          # index 0

        self._text_view = QTextEdit()
        self._text_view.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self._text_view.setFont(mono)
        self._text_view.setLineWrapMode(QTextEdit.NoWrap)
        self._stack.addWidget(self._text_view)            # index 1

        self._info_label = QLabel()
        self._info_label.setAlignment(Qt.AlignCenter)
        self._stack.addWidget(self._info_label)           # index 2

        hint = QLabel("Space / Esc で閉じる")
        hint.setAlignment(Qt.AlignRight)
        layout.addWidget(hint)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_for(self, path: str):
        """指定パスのプレビューを表示（既に表示中なら内容を差し替え）。"""
        if not path or not os.path.exists(path):
            return
        self._current_path = path
        self._populate(path)
        if not self.isVisible():
            self.show()
        self.raise_()

    def toggle_for(self, path: str):
        if self.isVisible() and path == self._current_path:
            self.close()
        else:
            self.show_for(path)

    # ------------------------------------------------------------------
    # Content dispatch
    # ------------------------------------------------------------------

    def _populate(self, path: str):
        p = Path(path)
        ext = p.suffix.lower()

        try:
            st = p.stat()
            meta = (f"{_human_size(st.st_size)}　|　"
                    f"更新: {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M}")
        except OSError:
            meta = ""

        self._title_label.setText(p.name)
        self.setWindowTitle(f"Quick Look — {p.name}")

        if p.is_dir():
            self._show_info("📁", "フォルダ", meta)
            return

        if ext in _IMAGE_EXTS:
            pix = QPixmap(path)
            if not pix.isNull():
                self._meta_label.setText(f"{pix.width()}×{pix.height()} px　|　{meta}")
                self._set_scaled_pixmap(pix)
                self._stack.setCurrentIndex(0)
                return
            # Qtが読めない形式（EXR等）は情報カードへフォールスルー

        if ext == ".ma":
            self._meta_label.setText(f"Maya ASCII　|　{meta}　|　{self._maya_version_text(path)}")
            self._text_view.setPlainText(self._read_text_head(path))
            self._stack.setCurrentIndex(1)
            return

        if ext == ".mb":
            self._show_info("🗂", f"Maya Binary　{self._maya_version_text(path)}", meta)
            return

        if ext in _TEXT_EXTS:
            self._meta_label.setText(meta)
            self._text_view.setPlainText(self._read_text_head(path))
            self._stack.setCurrentIndex(1)
            return

        self._show_info("📄", ext.upper().lstrip(".") or "ファイル", meta)

    def _show_info(self, icon: str, kind: str, meta: str):
        self._meta_label.setText(meta)
        self._info_label.setText(
            f"<div style='font-size:48px'>{icon}</div>"
            f"<div style='font-size:14px; margin-top:8px'>{kind}</div>"
        )
        self._stack.setCurrentIndex(2)

    def _set_scaled_pixmap(self, pix: "QPixmap"):
        area = self._stack.size()
        scaled = pix.scaled(
            max(area.width() - 16, 100), max(area.height() - 16, 100),
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._image_label.setPixmap(scaled)
        self._raw_pixmap = pix

    @staticmethod
    def _read_text_head(path: str) -> str:
        try:
            with open(path, "rb") as f:
                data = f.read(_TEXT_PREVIEW_BYTES)
            text = data.decode("utf-8", errors="replace")
            if len(data) == _TEXT_PREVIEW_BYTES:
                text += "\n\n… (先頭 64KB のみ表示)"
            return text
        except OSError as e:
            return f"(読み込みエラー: {e})"

    @staticmethod
    def _maya_version_text(path: str) -> str:
        try:
            from core.maya_version import detect_version_from_file
            ver = detect_version_from_file(path)
            return f"Maya {ver}" if ver else "バージョン不明"
        except Exception:
            return "バージョン不明"

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Escape):
            self.close()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        # 画像表示中はリサイズに追従して再スケール
        if self._stack.currentIndex() == 0 and getattr(self, "_raw_pixmap", None):
            self._set_scaled_pixmap(self._raw_pixmap)
        super().resizeEvent(event)
