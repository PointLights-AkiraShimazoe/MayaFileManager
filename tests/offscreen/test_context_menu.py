# -*- coding: utf-8 -*-
"""右クリック対象の決定（未選択項目→その項目、選択済み→選択全体、パンくず除外）。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, QTimer, Qt
from _common import QTest

b = make_panel()
d = tmpdir()
for n in ("aaa.ma", "bbb.ma", "ccc.txt"):
    open(os.path.join(d, n), "w").close()
os.makedirs(os.path.join(d, "sub"))
b.navigate_to(d)
menus = []
b._popup_context_menu = lambda paths, gpos: menus.append(list(paths))


def rclick(name):
    cv, rect, _ = find_item(b, name); assert cv, name
    g = cv.viewport().mapToGlobal(rect.center())
    colpos = b._column_view.viewport().mapFromGlobal(g)
    b.sender = lambda: b._column_view
    menus.clear()
    b._show_context_menu(colpos)
    return menus[-1] if menus else None


def s1():
    for name in ("aaa.ma", "ccc.txt", "bbb.ma", "sub"):
        got = rclick(name)
        assert got and len(got) == 1 and got[0].replace("\\", "/").endswith("/" + name), (name, got)
    print("right-click targets cursor item (all types): OK")
    orig = b._get_selected_paths
    b._get_selected_paths = lambda: [os.path.join(d, "aaa.ma").replace("\\", "/"), d]
    got = rclick("aaa.ma")
    b._get_selected_paths = orig
    assert got and all(p.rstrip("/") != d for p in got), got
    print("selected item: selection minus ancestors: OK")
    QTimer.singleShot(100, s2)


def _click(name, button=Qt.LeftButton, mods=Qt.NoModifier):
    cv, rect, _ = find_item(b, name); assert cv, name
    QTest.mouseClick(cv.viewport(), button, mods, rect.center())
    if button == Qt.RightButton:
        # QTest は ContextMenu イベントを合成しない（実機では QWidgetWindow が
        # マウスイベントの accept 状態に関係なく送る）ので、同じものを手動で送る
        from core.compat import QtGui, QApplication
        ev = QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, rect.center(),
                                     cv.viewport().mapToGlobal(rect.center()))
        QApplication.sendEvent(cv.viewport(), ev)


def _sel():
    # パンくず（祖先フォルダ）は除外して、対象フォルダ直下の選択だけを見る
    return sorted(os.path.basename(p) for p in b._get_selected_paths()
                  if os.path.dirname(p.replace("\\", "/")) == d.replace("\\", "/"))


def s2():
    # r55: 右クリックは複数選択を壊さない（実イベント経路: press→ContextMenu）
    b.sender = type(b).sender.__get__(b)   # 直接呼びのハックを戻す
    _click("aaa.ma")
    _click("bbb.ma", mods=Qt.ControlModifier)
    QTimer.singleShot(150, s3)


def s3():
    sel = _sel()
    assert sel == ["aaa.ma", "bbb.ma"], sel
    menus.clear()
    _click("bbb.ma", button=Qt.RightButton)
    QTimer.singleShot(150, s4)


def s4():
    sel = _sel()
    assert sel == ["aaa.ma", "bbb.ma"], ("右クリックで選択が崩れた", sel)
    assert menus and sorted(os.path.basename(p) for p in menus[-1]) == ["aaa.ma", "bbb.ma"], menus
    print("right-click keeps multi-selection, menu targets both: OK")
    # 未選択項目の右クリック → その項目だけが選択・対象になる
    menus.clear()
    _click("ccc.txt", button=Qt.RightButton)
    QTimer.singleShot(150, s5)


def s5():
    sel = _sel()
    assert sel == ["ccc.txt"], sel
    assert menus and [os.path.basename(p) for p in menus[-1]] == ["ccc.txt"], menus
    print("right-click on unselected item selects only it: OK")
    finish()


run(s1)
