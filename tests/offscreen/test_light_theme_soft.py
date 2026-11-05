# -*- coding: utf-8 -*-
"""ユーザー指示 2026-11-05「テーマがライトの場合に白が明るすぎます。
ライトだとしても目に優しい色合いでお願いします」

以前は主役の面（カラム）・ボタン・入力欄・ツールチップが純白（#ffffff）で、
文字は純黒に近い onyx（コントラスト 17.8:1）だった。

確かめること:
  1) ライトの面（カラム・下地・見出し・入力欄・エリアの色）に純白を使わない。
     明るさ（相対輝度）は 0.85 以下（純白は 1.0）
  2) 文字は読める: 本文 7:1 以上（AAA）、補助文字 4.5:1 以上（AA）、
     ただし眩しくないよう本文は 15:1 以下
  3) 実際にアプリへ当てた時の QPalette と QSS にも純白の面が無い
  4) 選択・下階層表示中の色は面と見分けがつく
"""
import re
from _common import *  # noqa: F401,F403
from _common import app, finish
from core import theme_engine as te

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _lum(h):
    h = h.strip().lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _cr(a, b):
    la, lb = sorted([_lum(a), _lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


c = te.get_colors("light")
SURFACES = ["surface", "surface_container_low", "surface_container",
            "surface_container_high", "plane_focus", "plane_side", "plane_header",
            "plane_flat", "plane_flat_header", "plane_merge_header", "column_header",
            "plane_common", "plane_archive", "artwork_backdrop"]
bright = [(k, c[k], round(_lum(c[k]), 3)) for k in SURFACES if _lum(c[k]) > 0.85]
check(not bright, "ライトの面に眩しい色（相対輝度 0.85 超）が無い: %r" % (bright,))
acc = [a[0] for a in te.area_accents("light")]
check(all(_lum(x) <= 0.85 for x in acc), "エリアの色も眩しくない: %r" % (acc,))

main = c["plane_focus"]
r_body = _cr(c["on_surface"], main)
check(7.0 <= r_body <= 15.0, "本文は読めて眩しくない（%.1f:1、7〜15）" % r_body)
for k in ("on_surface_variant", "on_surface_dim"):
    for bg in ("plane_focus", "surface", "surface_container"):
        r = _cr(c[k], c[bg])
        check(r >= 4.5, "%s は %s の上で読める（%.1f:1）" % (k, bg, r))
check(_cr(c["on_primary_container"], c["primary_container"]) >= 4.5,
      "強調の面の文字も読める")
check(_cr(c["on_selection"], c["selection"]) >= 7.0, "選択中の文字も読める")
check(abs(_lum(c["selection"]) - _lum(main)) > 0.15 and
      abs(_lum(c["selection_open"]) - _lum(main)) > 0.08,
      "選択・下階層表示中の色は面と見分けがつく")

te.apply_theme(app, "light")
pal = app.palette()
from core.compat import QPalette
for role in ("Window", "Base", "AlternateBase", "Button", "ToolTipBase"):
    col = pal.color(getattr(QPalette, role)).name()
    check(_lum(col) <= 0.85, "QPalette.%s が眩しくない（%s）" % (role, col))
q = te.build_qss("light").lower()
whites = re.findall(r"background(?:-color)?\s*:\s*(#fff\b|#ffffff|white)", q)
check(not whites, "ライトの QSS に純白の背景が無い（%d 件）" % len(whites))
te.apply_theme(app, "dark")
finish(not fails)
