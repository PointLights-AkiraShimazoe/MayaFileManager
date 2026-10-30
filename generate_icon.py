"""
generate_icon.py
================
app.ico を «採用したデザイン» の原画から作り直すスクリプト（r146）。

原画: resources/icons/app_icon_source.png（1024px・透過。2026-10-30 採用の X-3）
出力: resources/icons/app.ico（16〜256px のマルチサイズ）
      resources/icons/app_icon.png（256px。ウィンドウのアイコン用）
      resources/icons/wizard_small_*.bmp / wizard_large_*.bmp
        （インストーラの画面の絵。v0.9.13。以前は Inno Setup の既定の絵のままで、
          インストール時に新しいアイコンが出ていなかった）

以前は Qt で «古いデザイン» を描いていたため、ビルド手順どおりに実行すると
新しいアイコンが古いものに戻ってしまう。原画から作るだけにした。

実行:
    python generate_icon.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ICON_DIR = ROOT / "resources" / "icons"
SOURCE = ICON_DIR / "app_icon_source.png"
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
# Inno Setup の画面の絵の大きさ（表示倍率 100 / 150 / 200% 等。Inno が近いものを選ぶ）
WIZARD_SMALL_SIZES = [(55, 55), (83, 80), (110, 106), (138, 140)]
WIZARD_LARGE_SIZES = [(164, 314), (246, 459), (328, 604)]
WIZARD_SMALL_BG = (255, 255, 255)       # ページ上部の白い帯に置く
WIZARD_LARGE_BG = (23, 23, 33)          # ようこそ画面の左の帯（UI と同じ onyx）


def _png_bytes(img):
    """QImage を PNG のバイト列にする。"""
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    except ImportError:
        from PySide2.QtCore import QBuffer, QByteArray, QIODevice
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba.data())


def build(source=SOURCE, icon_dir=ICON_DIR):
    """原画から app.ico と app_icon.png を作る。

    Pillow は使わない（Maya の mayapy には入っていない。Windows の回帰
    スイート 2026-10-30 で発覚）。縮小は Qt、.ico の入れ物は自前で書く。
    中身は各サイズとも PNG（Windows Vista 以降の .ico が受け付ける形式）。"""
    import struct
    try:
        from PySide6.QtGui import QImage
        from PySide6.QtCore import Qt
    except ImportError:
        from PySide2.QtGui import QImage
        from PySide2.QtCore import Qt
    src = QImage(str(source))
    if src.isNull():
        raise IOError("cannot read %s" % source)
    src = src.convertToFormat(QImage.Format_ARGB32)

    def scaled(n):
        return src.scaled(n, n, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    blobs = [(n, _png_bytes(scaled(n))) for n in ICO_SIZES]
    head = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    data = b""
    for n, blob in blobs:
        wh = 0 if n >= 256 else n           # 256 は 0 と書く決まり
        entries += struct.pack("<BBBBHHII", wh, wh, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    ico = Path(icon_dir) / "app.ico"
    png = Path(icon_dir) / "app_icon.png"
    with open(str(ico), "wb") as f:
        f.write(head + entries + data)
    scaled(256).save(str(png), "PNG")
    return ico, png


def wizard_names(kind):
    """インストーラ用の絵のファイル名（installer.iss と同じ並び）。"""
    sizes = WIZARD_SMALL_SIZES if kind == "small" else WIZARD_LARGE_SIZES
    return ["wizard_%s_%dx%d.bmp" % (kind, w, h) for w, h in sizes]


def build_wizard(source=SOURCE, icon_dir=ICON_DIR):
    """インストーラ（Inno Setup）の画面の絵を原画から作る。

    Inno が読めるのは BMP なので、背景色の上にアイコンを置いて 24bit の
    BMP にする（透過は使わない）。"""
    try:
        from PySide6.QtGui import QImage, QPainter, QColor
        from PySide6.QtCore import Qt, QRect
    except ImportError:
        from PySide2.QtGui import QImage, QPainter, QColor
        from PySide2.QtCore import Qt, QRect
    src = QImage(str(source))
    if src.isNull():
        raise IOError("cannot read %s" % source)
    out = []
    for kind, sizes, bg in (("small", WIZARD_SMALL_SIZES, WIZARD_SMALL_BG),
                            ("large", WIZARD_LARGE_SIZES, WIZARD_LARGE_BG)):
        for (w, h), name in zip(sizes, wizard_names(kind)):
            img = QImage(w, h, QImage.Format_RGB32)
            img.fill(QColor(*bg))
            if kind == "small":
                n = int(min(w, h) * 0.92)
                cx, cy = w // 2, h // 2
            else:
                n = int(w * 0.72)
                cx, cy = w // 2, int(h * 0.42)
            icon = src.scaled(n, n, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            p = QPainter(img)
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawImage(QRect(cx - n // 2, cy - n // 2, n, n), icon)
            p.end()
            path = Path(icon_dir) / name
            if not img.convertToFormat(QImage.Format_RGB888).save(str(path), "BMP"):
                raise IOError("cannot write %s" % path)
            out.append(path)
    return out


def main():
    if not SOURCE.is_file():
        print("source not found: %s" % SOURCE)
        return 1
    ico, png = build()
    print("ICO : %s" % ico)
    print("PNG : %s" % png)
    for p in build_wizard():
        print("BMP : %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
