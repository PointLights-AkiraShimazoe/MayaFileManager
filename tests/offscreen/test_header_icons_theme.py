# -*- coding: utf-8 -*-
"""r148: ヘッダのアイコンは、テーマの色に合わせて描く（ライトで見えなくならない）。

ユーザー報告 2026-11-18:
  「ライトの時に見えなくなってしまってるアイコンがあります」

原因: ヘッダに埋め込んだアイコン（更新・起動・最前面・Maya / Blender のバッジ・
クリック動作 / D&D 動作）の色を、ダークの面に合わせた淡い色の 1 色に固定して
いた。ライトの面は明るいので、淡い色のままだと面に溶けて見えない。

固定すること:
  1) ライトでも、アイコンとヘッダの面（plane_header）の明るさに十分な差がある
  2) ダークは従来の色のまま（見た目を変えない）
  3) モードを切り替えた後は、そのモードの色で作り直す（前の絵を使い回さない）
"""
from _common import *  # noqa: F401,F403
from _common import app, finish
from core import theme_engine as te
from core import hdr_icons
from ui import dcc_header

fails = []
STEMS = ("maya", "blender", "launch", "refresh", "front", "click", "dnd")


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _lum_rgb(rgb):
    def f(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _hex_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def contrast(a, b):
    la, lb = _lum_rgb(a), _lum_rgb(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def icon_rgb(stem):
    """実際にボタンへ載る絵の、線の色（不透明に近い画素の平均）。"""
    pm = dcc_header.header_icon(stem).pixmap(24, 24)
    img = pm.toImage()
    rs = gs = bs = n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() >= 200:
                rs += c.red(); gs += c.green(); bs += c.blue(); n += 1
    assert n > 0, ("icon has no opaque pixel", stem)
    return (rs // n, gs // n, bs // n)


# ── 2) ダークは従来の色 ────────────────────────────────────────────
te.set_mode("dark")
dark_plane = _hex_rgb(te.qss_vars()["plane_header"])
dark_cols = {}
for s in STEMS:
    c = icon_rgb(s)
    dark_cols[s] = c
    near = all(abs(a - b) <= 6 for a, b in zip(c, hdr_icons.COLOR))
    check(near, "ダーク: %s は従来の色のまま（%r）" % (s, c))
    check(contrast(c, dark_plane) >= 3.0,
          "ダーク: %s が面と見分けられる（%.1f:1）" % (s, contrast(c, dark_plane)))

# ── 1) ライトでも見える ────────────────────────────────────────────
te.set_mode("light")
light_plane = _hex_rgb(te.qss_vars()["plane_header"])
for s in STEMS:
    c = icon_rgb(s)
    check(contrast(c, light_plane) >= 3.0,
          "ライト: %s が面と見分けられる（%.1f:1、色 %r）" % (s, contrast(c, light_plane), c))
    # ── 3) 前のモードの絵を使い回していない ────────────────────────
    check(c != dark_cols[s], "ライト: %s はダークの絵の使い回しではない" % s)

# 起動ボタン（CTA）の面の上でも見える
cta = te.qss_vars().get("cta_tint", "")
if cta.startswith("#") and len(cta) == 7:
    c = icon_rgb("launch")
    check(contrast(c, _hex_rgb(cta)) >= 3.0,
          "ライト: 起動ボタンの面の上でも見える（%.1f:1）" % contrast(c, _hex_rgb(cta)))

# ── 3) 戻した時も、そのモードの色 ──────────────────────────────────
te.set_mode("dark")
for s in ("refresh", "launch"):
    check(icon_rgb(s) == dark_cols[s], "ダークへ戻すと %s も元の色" % s)

finish(not fails)
