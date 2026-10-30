"""
generate_icon.py
================
app.ico を «採用したデザイン» の原画から作り直すスクリプト（r146）。

原画: resources/icons/app_icon_source.png（1024px・透過。2026-10-30 採用の X-3）
出力: resources/icons/app.ico（16〜256px のマルチサイズ）
      resources/icons/app_icon.png（256px。ウィンドウのアイコン用）

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


def main():
    if not SOURCE.is_file():
        print("source not found: %s" % SOURCE)
        return 1
    ico, png = build()
    print("ICO : %s" % ico)
    print("PNG : %s" % png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
