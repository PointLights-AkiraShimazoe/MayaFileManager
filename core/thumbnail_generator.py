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
from core.diag import swallow as _swallow  # r112

import os
from pathlib import Path
from typing import Optional

from core.compat import (
    QObject, Signal, QRunnable, QThreadPool, QPixmap, QImage,
    Qt
)
from core.file_operations import THUMBNAIL_EXTENSIONS

_THUMB_DEBUG = bool(os.environ.get("MFM_DEBUG"))
_THUMB_LOG = os.path.join(os.path.expanduser("~"), "mfm_debug.log")


def _thumb_log(msg: str):
    """サムネイルが «出ない» 時の切り分け用（r99）。MFM_DEBUG=1 の時だけ。"""
    if not _THUMB_DEBUG:
        return
    try:
        import datetime
        with open(_THUMB_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] thumb: %s\n"
                    % (datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], msg))
    except Exception as _e:
        _swallow(_e, "core/thumbnail_generator.py:42 _thumb_log")

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
    # r99: ワーカースレッドから渡すのは **QImage**。QPixmap は GUI スレッド
    # 以外で触ってはいけない（Qt: "It is not safe to use pixmaps outside the
    # GUI thread"）。実機でサムネイルが 1 枚も出なかった原因のひとつ。
    ready = Signal(str, QImage)    # (file_path, image)
    failed = Signal(str)           # file_path


class ThumbnailWorker(QRunnable):

    def __init__(self, file_path: str, size: int = 128):
        super().__init__()
        self.file_path = file_path
        self.size = size
        self.signals = _ThumbnailSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            image = self._generate(self.file_path, self.size)
            self.signals.ready.emit(self.file_path, image)
        except Exception as e:
            _thumb_log("error %s: %r" % (self.file_path, e))
            self.signals.failed.emit(self.file_path)

    # ------------------------------------------------------------------

    def _generate(self, path: str, size: int) -> QImage:
        """サムネイル画像を返す（r99: QImage）。**絵が作れない場合は «空» を返す**
        （r85）。呼び出し側（ThumbnailDelegate）はそれを見て «通常のファイル
        アイコン» を描く。以前は種別チップ（MA/FBX/"?"）を返していたが、
        「見つからないときは ? ではなく通常のアイコンにしてほしい」という
        ユーザー指示により、判断を描画側へ一本化した。"""
        ext = Path(path).suffix.lower()

        # 0. クラウドの «オンラインのみ» は内容に触れない（r83）。
        #    読むとその場でダウンロードが走り、フォルダを開いただけで
        #    大量ダウンロード＋UI 停滞になる。
        #    r99: 属性だけでなく «実体サイズ» まで見て判定する
        #    （OneDrive はダウンロード済みでも属性が残ることがある）。
        from core.cloud_state import is_online_only
        if is_online_only(path):
            _thumb_log("skip(cloud online-only): %s" % path)
            return QImage()

        # 1. Direct image load
        if ext in THUMBNAIL_EXTENSIONS:
            img = self._load_image(path, size)
            if img is None or img.isNull():
                _thumb_log("load failed: %s" % path)
            return img

        # 2. Maya sidecar thumbnail (.mayaSwatches)
        if ext in (".ma", ".mb"):
            sidecar = self._find_maya_sidecar(path)
            if sidecar and not is_online_only(sidecar):
                return self._load_image(sidecar, size)

        # 3. サムネイル無し → 通常のファイルアイコンを使わせる
        return QImage()

    @staticmethod
    def _load_image(path: str, size: int) -> QImage:
        ext = Path(path).suffix.lower()

        # Try OpenEXR via OpenCV first (handles .exr, .hdr)
        if ext in (".exr", ".hdr"):
            try:
                import cv2
                img_cv = cv2.imread(path, cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
                if img_cv is not None:
                    img_cv = cv2.normalize(img_cv, None, 0, 255, cv2.NORM_MINMAX)
                    img_cv = img_cv.astype("uint8")
                    img_cv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB)
                    h, w, ch = img_cv.shape
                    # numpy のバッファは関数を抜けると無効になるため必ず copy()
                    qi = QImage(img_cv.data, w, h, ch * w,
                                QImage.Format_RGB888).copy()
                    return qi.scaled(size, size, Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation)
            except ImportError:
                pass

        img = QImage(path)
        if img.isNull():
            raise ValueError(f"Cannot load image: {path}")
        return img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

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
        """Pre-warm the cache for a list of paths.
        クラウドの «オンラインのみ» は先読みしない（r83。ダウンロードを誘発
        するため。表示時に種別アイコンで出る）。"""
        from core.cloud_state import filter_local
        for p in filter_local(paths):
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

    def _on_ready(self, file_path: str, image):
        # r99: QPixmap への変換は «GUI スレッド» のここで行う
        pixmap = QPixmap.fromImage(image) if image is not None and \
            not image.isNull() else QPixmap()
        self._pending.discard(file_path)
        self._cache.put(file_path, pixmap)
        self.thumbnail_ready.emit(file_path, pixmap)

    def _on_failed(self, file_path: str):
        self._pending.discard(file_path)
        # Put a placeholder so we don't retry endlessly.
        # r85: 空の QPixmap を «サムネイル無し» の印としてキャッシュする
        # （再試行を止めつつ、描画側で通常のファイルアイコンにフォールバック）。
        placeholder = QPixmap()
        self._cache.put(file_path, placeholder)
        self.thumbnail_ready.emit(file_path, placeholder)
