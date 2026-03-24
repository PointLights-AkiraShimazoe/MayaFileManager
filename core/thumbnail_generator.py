"""
Thumbnail Generator
===================
Background QThread worker that generates QPixmap thumbnails for files.

Architecture
------------
* ThumbnailCache  – LRU dict (path → QPixmap)
* ThumbnailWorker – QRunnable that generates a single thumbnail
* ThumbnailManager– Public API: request(path) → emits thumbnail_ready(path, pixmap)

Supported sources
-----------------
- Image files (.png, .jpg, .tga, .tif, .exr …) → QPixmap.load / OpenCV
- .ma / .mb       → look for workspace .mayaSwatches sidecar first
- Generic files   → category icon from resources
"""

import os
from pathlib import Path
from typing import Dict, Optional

from core.compat import (
    QObject, Signal, QRunnable, QThreadPool, QPixmap, QImage,
    QSize, QPainter, QColor, Qt, QIcon
)
from core.file_operations import THUMBNAIL_EXTENSIONS, get_file_type_category

# ---------------------------------------------------------------------------
# LRU Cache
# ---------------------------------------------------------------------------

class LRUCache:
    """Simple LRU cache backed by an ordered dict."""

    def __init__(self, max_size: int = 256):
        from collections import OrderedDict
        self._cache: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> Optional[QPixmap]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, value: QPixmap):
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def invalidate(self, key: str):
        self._cache.pop(key, None)

    def clear(self):
        self._cache.clear()

    def resize(self, max_size: int):
        self._max_size = max_size
        while len(self._cache) > max_size:
            self._cache.popitem(last=False)


# ---------------------------------------------------------------------------
# Worker Runnable
# ---------------------------------------------------------------------------

class _ThumbnailSignals(QObject):
    ready = Signal(str, QPixmap)   # (file_path, pixmap)
    failed = Signal(str)            # file_path


class ThumbnailWorker(QRunnable):

    def __init__(self, file_path: str, size: int = 128):
        super().__init__()
        self.file_path = file_path
        self.size = size
        self.signals = _ThumbnailSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            pixmap = self._generate(self.file_path, self.size)
            self.signals.ready.emit(self.file_path, pixmap)
        except Exception as e:
            print(f"[ThumbnailWorker] Error for {self.file_path}: {e}")
            self.signals.failed.emit(self.file_path)

    # ------------------------------------------------------------------

    def _generate(self, path: str, size: int) -> QPixmap:
        ext = Path(path).suffix.lower()

        # 1. Direct image load
        if ext in THUMBNAIL_EXTENSIONS:
            return self._load_image(path, size)

        # 2. Maya sidecar thumbnail (.mayaSwatches)
        if ext in (".ma", ".mb"):
            sidecar = self._find_maya_sidecar(path)
            if sidecar:
                return self._load_image(sidecar, size)

        # 3. Category icon fallback
        return self._icon_pixmap(get_file_type_category(path), size)

    @staticmethod
    def _load_image(path: str, size: int) -> QPixmap:
        ext = Path(path).suffix.lower()

        # Try OpenEXR via OpenCV first (handles .exr, .hdr)
        if ext in (".exr", ".hdr"):
            try:
                import cv2, numpy as np
                img_cv = cv2.imread(path, cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
                if img_cv is not None:
                    img_cv = cv2.normalize(img_cv, None, 0, 255, cv2.NORM_MINMAX)
                    img_cv = img_cv.astype("uint8")
                    img_cv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB)
                    h, w, ch = img_cv.shape
                    qi = QImage(img_cv.data, w, h, ch * w, QImage.Format_RGB888)
                    pm = QPixmap.fromImage(qi)
                    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            except ImportError:
                pass

        pm = QPixmap(path)
        if pm.isNull():
            raise ValueError(f"Cannot load image: {path}")
        return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    @staticmethod
    def _find_maya_sidecar(ma_path: str) -> Optional[str]:
        """
        Maya saves .iff thumbnails inside a .mayaSwatches folder next to the file.
        Pattern: <dir>/.mayaSwatches/<filename>.swatches
        """
        p = Path(ma_path)
        swatch_dir = p.parent / ".mayaSwatches"
        candidates = [
            swatch_dir / (p.stem + ".iff"),
            swatch_dir / (p.name + ".swatches"),
            swatch_dir / (p.stem + ".png"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return None

    @staticmethod
    def _icon_pixmap(category: str, size: int) -> QPixmap:
        """Return a colored placeholder pixmap based on category."""
        COLORS = {
            "maya":    "#4A90D9",
            "fbx":     "#E8832A",
            "3d":      "#7EC850",
            "image":   "#C850C8",
            "script":  "#50C8C8",
            "text":    "#C8C850",
            "archive": "#888888",
            "generic": "#555555",
        }
        color_hex = COLORS.get(category, "#555555")
        color = QColor(color_hex)

        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        margin = size // 8
        painter.drawRoundedRect(margin, margin, size - 2 * margin, size - 2 * margin,
                                size // 6, size // 6)

        # Category label (short)
        label_map = {
            "maya": "MA", "fbx": "FBX", "3d": "3D",
            "image": "IMG", "script": "SCR", "text": "TXT",
            "archive": "ZIP", "generic": "?",
        }
        label = label_map.get(category, "?")
        painter.setPen(QColor("#FFFFFF"))
        font = painter.font()
        font.setPointSize(max(6, size // 8))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pm.rect(), Qt.AlignCenter, label)
        painter.end()
        return pm


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class ThumbnailManager(QObject):
    """
    Public API for asynchronous thumbnail generation.

    Usage
    -----
        mgr = ThumbnailManager(cache_size=256, thumb_size=128)
        mgr.thumbnail_ready.connect(my_slot)   # slot(path: str, pixmap: QPixmap)
        pixmap = mgr.get(path)  # returns cached or None + queues request
    """

    thumbnail_ready = Signal(str, QPixmap)

    def __init__(self, cache_size: int = 256, thumb_size: int = 128, parent=None):
        super().__init__(parent)
        self._cache = LRUCache(cache_size)
        self._thumb_size = thumb_size
        self._pending: set = set()
        self._pool = QThreadPool.globalInstance()

    # ------------------------------------------------------------------

    def get(self, file_path: str) -> Optional[QPixmap]:
        """
        Return cached pixmap immediately, or None (and queue background generation).
        Connect to thumbnail_ready to receive the result.
        """
        cached = self._cache.get(file_path)
        if cached is not None:
            return cached

        if file_path not in self._pending:
            self._pending.add(file_path)
            self._enqueue(file_path)

        return None

    def prefetch(self, paths):
        """Pre-warm the cache for a list of paths."""
        for p in paths:
            if self._cache.get(p) is None and p not in self._pending:
                self._pending.add(p)
                self._enqueue(p)

    def invalidate(self, file_path: str):
        self._cache.invalidate(file_path)

    def clear(self):
        self._cache.clear()
        self._pending.clear()

    def set_thumb_size(self, size: int):
        self._thumb_size = size
        self.clear()

    def set_cache_size(self, size: int):
        self._cache.resize(size)

    # ------------------------------------------------------------------

    def _enqueue(self, file_path: str):
        worker = ThumbnailWorker(file_path, self._thumb_size)
        worker.signals.ready.connect(self._on_ready)
        worker.signals.failed.connect(self._on_failed)
        self._pool.start(worker)

    def _on_ready(self, file_path: str, pixmap: QPixmap):
        self._pending.discard(file_path)
        self._cache.put(file_path, pixmap)
        self.thumbnail_ready.emit(file_path, pixmap)

    def _on_failed(self, file_path: str):
        self._pending.discard(file_path)
        # Put a placeholder so we don't retry endlessly
        placeholder = ThumbnailWorker._icon_pixmap("generic", self._thumb_size)
        self._cache.put(file_path, placeholder)
        self.thumbnail_ready.emit(file_path, placeholder)
