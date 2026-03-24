"""
generate_icon.py
================
アイコンファイルを生成するスクリプト。
PySide6/2 のみで .png を生成し、
さらに Windows 用 .ico と macOS 用 .icns を作成する。

実行:
    python generate_icon.py
"""

import os
import struct
import zlib
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ICON_DIR = ROOT / "resources" / "icons"
ICON_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# PNG 生成（stdlib のみ・Qt使用）
# ---------------------------------------------------------------------------

def generate_png_qt(size: int, out_path: str):
    """Qt で app アイコンを描画して PNG 保存。"""
    # Minimal QApplication（headless）
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QPixmap, QPainter, QColor, QFont, QLinearGradient
        from PySide6.QtCore import Qt, QRectF
    except ImportError:
        from PySide2.QtWidgets import QApplication
        from PySide2.QtGui import QPixmap, QPainter, QColor, QFont, QLinearGradient
        from PySide2.QtCore import Qt, QRectF

    app = QApplication.instance() or QApplication(sys.argv)

    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))  # transparent

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    # Background rounded rect
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor("#2A5080"))
    grad.setColorAt(1.0, QColor("#1A3050"))
    p.setBrush(grad)
    p.setPen(QColor("#4A90D9"))
    from PySide6.QtGui import QPen
    pen = QPen(QColor("#4A90D9"))
    pen.setWidth(max(1, size // 32))
    p.setPen(pen)
    radius = size * 0.18
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), radius, radius)

    # Inner decorative grid (file manager feel)
    p.setPen(QColor(255, 255, 255, 40))
    for i in range(1, 3):
        x = size * i // 3
        p.drawLine(x, size // 6, x, size * 5 // 6)
    for i in range(1, 3):
        y = size * i // 3
        p.drawLine(size // 6, y, size * 5 // 6, y)

    # "MFM" text
    p.setPen(QColor("#FFFFFF"))
    f = QFont("Arial", max(8, size // 5))
    f.setBold(True)
    p.setFont(f)
    from PySide6.QtCore import Qt as Qt6
    p.drawText(pm.rect(), Qt6.AlignCenter, "MFM")

    p.end()
    pm.save(out_path, "PNG")
    print(f"  生成: {out_path}")


# ---------------------------------------------------------------------------
# ICO ファイル生成（stdlib のみ）
# ---------------------------------------------------------------------------

def png_to_ico(png_paths_with_sizes: list, ico_path: str):
    """
    複数サイズの PNG バイト列を ICO ファイルにまとめる。
    ICO フォーマット仕様に従い純粋な stdlib で実装。
    """
    images = []
    for size, png_path in png_paths_with_sizes:
        with open(png_path, "rb") as f:
            data = f.read()
        images.append((size, data))

    n = len(images)
    # ICO header: 6 bytes
    header = struct.pack("<HHH", 0, 1, n)

    # Directory entries: 16 bytes each
    offset = 6 + n * 16
    dir_entries = b""
    for size, data in images:
        w = size if size < 256 else 0
        h = size if size < 256 else 0
        dir_entries += struct.pack(
            "<BBBBHHII",
            w, h,          # width, height (0 = 256)
            0,             # color count
            0,             # reserved
            1,             # planes
            32,            # bit count
            len(data),     # size of image data
            offset,        # offset
        )
        offset += len(data)

    with open(ico_path, "wb") as f:
        f.write(header + dir_entries)
        for _, data in images:
            f.write(data)

    print(f"  生成: {ico_path}")


# ---------------------------------------------------------------------------
# ICNS 生成（macOS）
# ---------------------------------------------------------------------------

def pngs_to_icns(size_path_map: dict, icns_path: str):
    """
    ICNS フォーマット。最低限 ic07(128), ic08(256), ic09(512) を含める。
    """
    ICNS_TYPES = {
        16:   b"icp4",
        32:   b"icp5",
        64:   b"icp6",
        128:  b"ic07",
        256:  b"ic08",
        512:  b"ic09",
        1024: b"ic10",
    }

    chunks = b""
    for size, png_path in size_path_map.items():
        if size in ICNS_TYPES and Path(png_path).exists():
            with open(png_path, "rb") as f:
                data = f.read()
            tag  = ICNS_TYPES[size]
            length = 8 + len(data)
            chunks += tag + struct.pack(">I", length) + data

    total_length = 8 + len(chunks)
    icns_data = b"icns" + struct.pack(">I", total_length) + chunks

    with open(icns_path, "wb") as f:
        f.write(icns_data)
    print(f"  生成: {icns_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("アイコン生成中...")

    sizes = [16, 32, 48, 64, 128, 256, 512]
    png_files = {}

    for size in sizes:
        out = str(ICON_DIR / f"app_{size}.png")
        generate_png_qt(size, out)
        png_files[size] = out

    # Windows ICO（16,32,48,256 を使用）
    ico_sizes = [(s, png_files[s]) for s in [16, 32, 48, 256] if s in png_files]
    png_to_ico(ico_sizes, str(ICON_DIR / "app.ico"))

    # macOS ICNS
    pngs_to_icns(png_files, str(ICON_DIR / "app.icns"))

    print("\n完了！")
    print(f"  ICO  : {ICON_DIR / 'app.ico'}")
    print(f"  ICNS : {ICON_DIR / 'app.icns'}")
    print(f"  PNG  : {ICON_DIR / 'app_256.png'} （代表サイズ）")


if __name__ == "__main__":
    main()
