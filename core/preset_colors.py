# -*- coding: utf-8 -*-
"""プリセットのボタンの色（r151、ユーザー指示 2026-11-18）。

「プリセット設定でボタンに色を付けられる様にしてください」
- ボタンの項目 {label, path} に任意の "color"（"#RRGGBB"）を足す。無ければ今までどおり。
- 選べる色は、ライト／ダークのどちらのテーマでも沈まない中間の明るさの 8 色＋任意の色。
- 文字の色は背景の明るさから白か黒を選ぶ（読めない組み合わせを作らない）。
ここは Qt を使わない（色の計算とスタイル文字列だけ）。
"""
import re

PALETTE = [
    ("#C0392B", "赤", "Red"),
    ("#C8661E", "橙", "Orange"),
    ("#D4AC2B", "黄", "Yellow"),
    ("#2E8B57", "緑", "Green"),
    ("#1F8A8A", "青緑", "Teal"),
    ("#2F6FC9", "青", "Blue"),
    ("#7A4FC4", "紫", "Purple"),
    ("#C2477F", "桃", "Pink"),
]

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize(color) -> str:
    """"#RRGGBB" に揃える。使えない値は ""（色なし）。"""
    s = str(color or "").strip()
    if len(s) == 4 and s.startswith("#"):            # #RGB
        s = "#" + "".join(c * 2 for c in s[1:])
    return s.upper() if _HEX.match(s) else ""


def _rgb(c):
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def text_color(bg: str) -> str:
    """背景に対して読める文字色（相対輝度で白か黒）。"""
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(bg)
    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    # ボタンの文字は太めで短いので、白とのコントラスト比 3:1 以上なら白（色の上の
    # 白文字が一番見慣れている）。それより明るい色（黄など）は黒。
    return "#FFFFFF" if (1.05 / (lum + 0.05)) >= 3.0 else "#111111"


def shade(c: str, f: float) -> str:
    """f>1 で明るく、f<1 で暗く。"""
    r, g, b = _rgb(c)
    if f >= 1:
        k = f - 1
        r, g, b = (int(v + (255 - v) * k) for v in (r, g, b))
    else:
        r, g, b = (int(v * f) for v in (r, g, b))
    return "#%02X%02X%02X" % (r, g, b)


def button_qss(color: str) -> str:
    """プリセットのボタン 1 つ分のスタイル（色なしなら ""＝テーマのまま）。"""
    c = normalize(color)
    if not c:
        return ""
    return ("QToolButton{background:%s;color:%s;border:1px solid %s;}"
            "QToolButton:hover{background:%s;}"
            "QToolButton:pressed{background:%s;}"
            % (c, text_color(c), shade(c, 0.8), shade(c, 1.15), shade(c, 0.85)))
