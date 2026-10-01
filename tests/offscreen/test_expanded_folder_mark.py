# -*- coding: utf-8 -*-
"""r81: 右隣のカラムに中身を出しているフォルダ（展開中）は、同じカラムで
ファイルを選んで選択が移った後も、選択とは別の淡い色で目印が付く。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, QTimer, Qt
from _common import QTest

b = make_panel()
d = tmpdir()
os.makedirs(os.path.join(d, "sub"))
open(os.path.join(d, "sub", "x.ma"), "w").close()
for n in ("aaa.ma", "bbb.ma"):
    open(os.path.join(d, n), "w").close()
b.navigate_to(d)
state = {}


def _click(name):
    cv, rect, _ = find_item(b, name); assert cv, name
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())


def _px(name):
    """その行の左端（帯の位置）と右寄り（面）の色"""
    cv, rect, idx = find_item(b, name); assert cv, name
    img = cv.viewport().grab().toImage()
    y = rect.center().y()
    return (img.pixelColor(rect.left() + 1, y).name(),
            img.pixelColor(rect.right() - 8, y).name(), cv, idx)


def s1():
    _click("sub")
    QTimer.singleShot(400, s2)


def s2():
    cv, rect, idx = find_item(b, "sub")
    assert b._column_view._is_expanded_index(idx), "sub の子カラムが出ていない"
    _click("aaa.ma")            # 同じカラムでファイルを選ぶ
    QTimer.singleShot(400, s3)


def s3():
    _, _, idx = find_item(b, "sub")
    assert b._column_view._is_expanded_index(idx), "ファイル選択後も子カラムは残る前提"
    bar, face, cv, _ = _px("sub")
    bar2, face2, _, _ = _px("bbb.ma")     # 未選択・未展開の行
    sel_bar, sel_face, _, _ = _px("aaa.ma")
    assert bar != bar2 and face != face2, ("展開中フォルダに目印が無い", bar, bar2, face, face2)
    assert face != sel_face, ("選択色と同じになっている", face, sel_face)
    print("expanded folder gets a subtle mark distinct from selection: OK")
    # 展開中フォルダを選択すると目印ではなく通常の選択色
    _click("sub")
    QTimer.singleShot(400, s4)


def s4():
    bar, face, _, _ = _px("sub")
    _, sel_face, _, _ = _px("sub")
    assert face == sel_face
    print("selected expanded folder uses normal selection color: OK")
    finish(True)


run(s1)
