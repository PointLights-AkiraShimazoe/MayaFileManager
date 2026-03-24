"""
make_icon.py
============
MayaFileManager 用の app.ico を生成するスクリプト。
Pillow が使える場合は 256/128/64/48/32/16 px のマルチサイズ ICO を作成。
Pillow が無い場合は ICO ヘッダーを手書きして 32px の最小 ICO を生成。

使い方:
    python build_tools/make_icon.py
    -> resources/icons/app.ico が生成される
"""

import os
import struct
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent / "resources" / "icons"
OUT_ICO = OUT_DIR / "app.ico"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Color palette for the icon
# ---------------------------------------------------------------------------
BG_COLOR    = (42, 80, 128)       # Dark navy
ACCENT      = (74, 144, 217)      # Blue
LIGHT       = (220, 230, 240)     # Near white
DARK_ACCENT = (20, 50, 90)        # Deep navy


def _render_icon_rgba(size: int) -> bytes:
    """
    Render an MFM icon as raw RGBA bytes (size × size × 4).
    Draws:
      - Rounded-rect background
      - Two overlapping folder-like shapes
      - "MFM" text (pixel font at small sizes)
    """
    img = bytearray(size * size * 4)

    def px(x, y, r, g, b, a=255):
        if 0 <= x < size and 0 <= y < size:
            i = (y * size + x) * 4
            img[i]   = r
            img[i+1] = g
            img[i+2] = b
            img[i+3] = a

    def fill_rect(x0, y0, x1, y1, color, alpha=255):
        for yy in range(max(0, y0), min(size, y1)):
            for xx in range(max(0, x0), min(size, x1)):
                px(xx, yy, *color, alpha)

    def circle_aa(cx, cy, r, color):
        for yy in range(int(cy - r) - 1, int(cy + r) + 2):
            for xx in range(int(cx - r) - 1, int(cx + r) + 2):
                d = ((xx - cx) ** 2 + (yy - cy) ** 2) ** 0.5
                a = max(0, min(255, int((r - d + 1) * 255)))
                if a > 0:
                    px(xx, yy, *color, a)

    # Background
    m = max(1, size // 8)
    fill_rect(m, m, size - m, size - m, BG_COLOR)

    # Accent band (top)
    band = max(1, size // 6)
    fill_rect(m, m, size - m, m + band, ACCENT)

    # Highlight square
    sq = size // 3
    cx = size // 2
    cy = size * 5 // 8
    fill_rect(cx - sq // 2, cy - sq // 2, cx + sq // 2, cy + sq // 2, ACCENT)

    # Inner square
    i2 = sq // 3
    fill_rect(cx - i2, cy - i2, cx + i2, cy + i2, LIGHT)

    return bytes(img)


def _make_ico_pillow():
    from PIL import Image, ImageDraw, ImageFont

    sizes = [256, 128, 64, 48, 32, 16]
    images = []

    for s in sizes:
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        m = max(2, s // 10)
        r = max(4, s // 8)

        # Rounded background
        draw.rounded_rectangle([m, m, s - m, s - m], radius=r,
                                fill=(*BG_COLOR, 255))
        # Top accent
        band = max(2, s // 6)
        draw.rounded_rectangle([m, m, s - m, m + band + r], radius=r,
                                fill=(*ACCENT, 255))
        if m + band > m + r:
            draw.rectangle([m, m + r, s - m, m + band], fill=(*ACCENT, 255))

        # Inner decoration
        sq = s // 3
        cx, cy = s // 2, s * 5 // 8
        draw.rectangle([cx - sq // 2, cy - sq // 2,
                         cx + sq // 2, cy + sq // 2],
                        fill=(*DARK_ACCENT, 255), outline=(*ACCENT, 200), width=max(1, s // 32))

        # Text
        if s >= 32:
            text = "MFM"
            try:
                font_size = max(6, s // 5)
                try:
                    from PIL import ImageFont
                    font = ImageFont.truetype("arial.ttf", font_size)
                except Exception:
                    font = ImageFont.load_default()
                bbox = draw.textbbox((0, 0), text, font=font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                draw.text((cx - tw // 2, cy - th // 2), text,
                          fill=(*LIGHT, 255), font=font)
            except Exception:
                pass

        images.append(img)

    # Save as multi-size ICO
    images[0].save(
        OUT_ICO,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[1:]
    )
    print(f"[make_icon] Pillow ICO saved: {OUT_ICO}  ({OUT_ICO.stat().st_size // 1024} KB)")


def _make_ico_pure_python():
    """
    Build a minimal ICO file with a single 32×32 RGBA image
    without any external dependencies.
    ICO format: https://en.wikipedia.org/wiki/ICO_(file_format)
    """
    SIZE = 32
    BITS = 32

    raw_rgba = _render_icon_rgba(SIZE)

    # BITMAPINFOHEADER (40 bytes) — standard RGBQUAD not needed for 32-bit
    def dword(v):  return struct.pack("<I", v)
    def word(v):   return struct.pack("<H", v)

    # In ICO, the height in BITMAPINFOHEADER is 2× (image + mask)
    bih = (
        dword(40)        # biSize
        + dword(SIZE)    # biWidth
        + dword(SIZE * 2)  # biHeight (2x for XOR + AND mask)
        + word(1)        # biPlanes
        + word(BITS)     # biBitCount
        + dword(0)       # biCompression = BI_RGB
        + dword(0)       # biSizeImage
        + dword(0) * 4   # resolution + colours
    )

    # Pixel data: bottom-up BGRA (ICO stores rows inverted)
    pixels = bytearray()
    for row in range(SIZE - 1, -1, -1):
        for col in range(SIZE):
            i = (row * SIZE + col) * 4
            r, g, b, a = raw_rgba[i], raw_rgba[i+1], raw_rgba[i+2], raw_rgba[i+3]
            pixels += bytes([b, g, r, a])

    # AND mask (all zeros = fully opaque), 4-byte row aligned
    row_bytes = ((SIZE + 31) // 32) * 4
    and_mask = bytes(row_bytes * SIZE)

    img_data = bih + bytes(pixels) + and_mask
    img_size = len(img_data)

    # ICONDIR + ICONDIRENTRY (6 + 16 bytes)
    icon_dir   = struct.pack("<HHH", 0, 1, 1)   # reserved, type=1(ICO), count=1
    dir_entry  = struct.pack(
        "<BBBBHHII",
        SIZE, SIZE,  # width, height
        0,           # color count (0 for >8bit)
        0,           # reserved
        1,           # planes
        BITS,        # bit count
        img_size,    # size of image data
        6 + 16,      # offset of image data
    )

    ico_bytes = icon_dir + dir_entry + img_data

    with open(OUT_ICO, "wb") as f:
        f.write(ico_bytes)

    print(f"[make_icon] Pure-Python ICO saved: {OUT_ICO}  ({len(ico_bytes)} bytes, 32×32)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        import PIL
        _make_ico_pillow()
    except ImportError:
        print("[make_icon] Pillow not found, using pure-Python fallback (32×32 only)")
        _make_ico_pure_python()
