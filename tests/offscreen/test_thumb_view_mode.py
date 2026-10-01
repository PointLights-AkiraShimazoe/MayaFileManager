# -*- coding: utf-8 -*-
"""r84: サムネイル表示にしても «何も出ない» ことがない。
絵が取れなくても、種別アイコンとファイル名がグリッドに並ぶこと。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, QTimer, Qt
from core.compat import QListView

b = make_panel(1000, 600)
d = tmpdir()
names = ["alpha.ma", "bravo.fbx", "charlie_very_long_file_name.txt", "delta.png"]
for n in names:
    open(os.path.join(d, n), "w").close()
b.navigate_to(d)
state = {}


def _column_for(name):
    cv, rect, idx = find_item(b, name)
    return cv, rect, idx


def _ink(view, rect):
    """その項目の矩形に «背景以外の画素» がどれだけあるか（=何か描かれたか）。"""
    img = view.viewport().grab(rect).toImage()
    counts = {}
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y).name()
            counts[c] = counts.get(c, 0) + 1
    bg = max(counts, key=counts.get)
    total = sum(counts.values())
    return total - counts[bg], bg


def s1():
    cv, rect, idx = _column_for("alpha.ma")
    assert cv, "項目が見つからない"
    state["view"] = cv
    state["list_ink"], _ = _ink(cv, rect)
    assert state["list_ink"] > 0, "リスト表示で既に何も描かれていない"
    b._column_view._set_column_view_mode(cv, "thumb")
    QTimer.singleShot(700, s2)


def s2():
    cv = state["view"]
    assert cv.viewMode() == QListView.IconMode, "アイコン表示になっていない"
    m = cv.model(); ri = cv.rootIndex()
    found = {}
    for r in range(m.rowCount(ri)):
        i = m.index(r, 0, ri)
        if i.data() in names:
            rect = cv.visualRect(i)
            assert rect.isValid() and not rect.isEmpty(), (i.data(), "矩形が空")
            ink, _bg = _ink(cv, rect)
            found[i.data()] = ink
    assert len(found) == len(names), ("項目が足りない", sorted(found))
    for n, ink in found.items():
        assert ink > 40, ("サムネイル表示で中身が描かれていない（空グリッド）", n, ink)
    print("icon mode draws icon+name for every item: OK", sorted(found))

    # 名前が «アイコンの下» に入る前提のサイズになっていること
    d0 = b._thumb_delegate if hasattr(b, "_thumb_delegate") else None
    cv2 = state["view"]
    deleg = cv2.itemDelegate()
    from core.compat import QSize
    opt = type("O", (), {})()
    hint = deleg.sizeHint(_fake_option(cv2), m.index(0, 0, ri))
    assert hint.height() > hint.width() - 40, ("名前の行が確保されていない", hint)
    print("size hint reserves room for the label: OK", hint.width(), hint.height())

    # リスト表示へ戻しても描画が壊れないこと
    b._column_view._set_column_view_mode(cv2, "list")
    QTimer.singleShot(500, s3)


def _fake_option(view):
    from core.compat import QtWidgets
    o = QtWidgets.QStyleOptionViewItem()
    o.widget = view
    try:
        o.initFrom(view)
    except Exception:
        pass
    return o


def s3():
    cv = state["view"]
    assert cv.viewMode() == QListView.ListMode
    m = cv.model(); ri = cv.rootIndex()
    i = m.index(0, 0, ri)
    rect = cv.visualRect(i)
    ink, _ = _ink(cv, rect)
    assert ink > 0, "リスト表示へ戻したら何も描かれない"
    print("back to list mode still renders: OK")
    finish(True)


run(s1)
