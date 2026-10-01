# -*- coding: utf-8 -*-
"""r105: カラム幅を掴む帯は **必ず縦スクロールバーより左** にある。

右端のカラムでハンドルがスクロールバーと重なっていると、掴み損ねて
スクロールバーを動かしたり、誤って幅を変えて戻せなくなる。
"""
import os
from _common import *  # noqa: F401,F403
from _common import (app, make_panel, find_item_wait as find_item,
                     tmpdir, finish, run, QTest)
from core.compat import QListView
import ui.browser_panel as bp

b = make_panel(1200, 500)
root = tmpdir()
deep = os.path.join(root, "proj", "scenes")
os.makedirs(deep)
for i in range(60):                       # 縦スクロールバーが出る量
    open(os.path.join(deep, "cut%03d.ma" % i), "w").close()
b.navigate_to(deep)


def _settle(ms=400):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    cv = b._column_view
    v, _r, _i = find_item(b, "cut000.ma")
    assert v, "項目が見つからない"
    _settle()

    cols = [c for c in cv._live_columns() if c.isVisible()]
    assert cols, "カラムが無い"
    checked = 0
    for col in cols:
        h = getattr(col, "_mfm_resize_handle", None)
        if h is None or not h.isVisible():
            continue
        sb_w = cv._scrollbar_extent(col)
        assert sb_w > 0, "スクロールバー幅が取れない"
        sb_left = col.x() + col.width() - sb_w
        g = h.geometry()
        assert g.right() <= sb_left, \
            ("掴む帯がスクロールバーに掛かっている",
             g.right(), sb_left, col.width())
        assert g.left() >= col.x(), ("帯がカラムの外へ出ている", g.left(), col.x())
        assert g.width() == bp._ColumnResizeHandle.WIDTH
        checked += 1
    assert checked >= 1, "検査できたハンドルが無い"
    print("resize handle stays left of the scrollbar: OK (%d columns)" % checked)

    # スクロールバーが «出ていない» カラムでも位置は同じ（出た瞬間にずれない）
    v2 = cols[0]
    before = getattr(v2, "_mfm_resize_handle").geometry().right()
    v2.verticalScrollBar().setVisible(False)
    cv._reposition_column_header(v2)
    _settle(150)
    after = getattr(v2, "_mfm_resize_handle").geometry().right()
    assert before == after, ("スクロールバーの表示有無で掴む位置が動く", before, after)
    print("position does not jump when the scrollbar appears: OK")

    # 実際に掴んで幅が変わる（帯が死んでいない）
    h = getattr(cols[0], "_mfm_resize_handle")
    w0 = cols[0].width()
    cv.set_column_width_for_view(cols[0], w0 + 60)
    _settle(200)
    assert cols[0].width() != w0, "幅変更が効かない"
    print("width change still works: OK")
    finish(True)


run(s1, delay=900)
