# -*- coding: utf-8 -*-
"""r104: リスト表示／グリッド表示で表示サイズを **別々に覚える**。

切り替えるたびに調整し直さなくて済むこと。特に «▦ を押してモードが
変わった直後のスライダー» が前のモードのレンジのまま適用されないこと
（リストの 40px がグリッドのセル寸法として保存され、グリッドが小さくなる）。
"""
import os
from _common import *  # noqa: F401,F403
from _common import (app, sm, make_panel, find_item_wait as find_item,
                     tmpdir, finish, run, Qt, QTest)
from core.compat import QToolButton
import ui.browser_panel as bp

b = make_panel(1100, 700)
d = tmpdir()
for n in ("a.ma", "b.png", "c.txt"):
    open(os.path.join(d, n), "w").close()
b.navigate_to(d)


def _settle(ms=350):
    QTest.qWait(ms)
    app.processEvents()


def _view_btn(view):
    hdr = getattr(view, "_mfm_header", None)
    for btn in hdr.findChildren(QToolButton):
        if btn.text() == "▦":
            return btn
    raise AssertionError("▦ ボタンが無い")


def s1():
    cv = b._column_view
    v, _r, _i = find_item(b, "a.ma")
    assert v, "項目が見つからない"

    # グリッドで 150px、リストで 40px に設定する
    cv._set_column_view_mode(v, "thumb"); _settle()
    cv.set_item_size_all(150, "thumb"); _settle()
    cv._set_column_view_mode(v, "list"); _settle()
    cv.set_item_size_all(40, "list"); _settle()

    # 行き来しても互いを壊さない
    cv._set_column_view_mode(v, "thumb"); _settle()
    assert cv.column_item_size(v) == 150, ("グリッドの値が失われた", cv.column_item_size(v))
    cv._set_column_view_mode(v, "list"); _settle()
    assert cv.column_item_size(v) == 40, ("リストの値が失われた", cv.column_item_size(v))
    assert int(sm.get("column_icon_size_thumb")) == 150
    assert int(sm.get("column_icon_size_list")) == 40
    print("sizes are remembered per mode: OK")

    # 新しく作られるカラム（再ナビゲーション）にも効く
    b.navigate_to(os.path.dirname(d)); _settle(600)
    b.navigate_to(d); _settle(800)
    v2, _r2, _i2 = find_item(b, "a.ma")
    assert cv.column_item_size(v2) == 40, ("新しいカラムに効いていない",
                                           cv.column_item_size(v2))
    print("new columns pick up the saved size: OK")

    # ── 本命: スライダーを出したまま ▦ でモードを切り替えた場合 ──────
    btn = _view_btn(v2)
    popup = cv.size_popup()
    popup.show_for(v2, btn)          # list 用（レンジ 16-128、値 40）
    _settle(200)
    assert popup._mode == "list"
    lo, hi = popup._slider.minimum(), popup._slider.maximum()
    assert (lo, hi) == popup.LIST_RANGE, (lo, hi)

    cv._toggle_view_mode(v2)         # → thumb（スライダーは出たまま）
    _settle(300)
    assert getattr(v2, "_mfm_view_mode", "") == "thumb"
    assert popup._mode == "thumb", "スライダーが新しいモードへ更新されていない"
    assert (popup._slider.minimum(), popup._slider.maximum()) == popup.THUMB_RANGE, \
        "スライダーのレンジが前のモードのまま"
    assert popup._slider.value() == 150, ("グリッドの保存値を読み直していない",
                                          popup._slider.value())
    assert cv.column_item_size(v2) == 150, "グリッドのサイズが壊れた"
    assert int(sm.get("column_icon_size_thumb")) == 150, \
        "リストの値がグリッドとして保存された"
    print("switching mode with the slider open keeps both sizes: OK")
    finish(True)


run(s1, delay=900)
