# -*- coding: utf-8 -*-
"""r94: ドラッグ開始前の MouseMove で «対象より上の項目» が選択されない。

QAbstractItemView::mouseMoveEvent は «左ボタンを押したまま動いた» だけで
DragSelectingState に入り、自分が受け取っていない押下位置（既定値 = ビュー
左上）を矩形選択の起点にする。押下を自前で消費しているのに閾値未満の
MouseMove を素通しすると、カーソルまでの範囲＝対象より上の全てが選択されて
しまう（ユーザー報告 2026-09-23）。
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, Qt
from _common import QTest
import ui.browser_panel as bp

b = make_panel(1100, 600)
cv = b._column_view
root = tmpdir()
deep = os.path.join(root, "proj", "scenes")
os.makedirs(deep)
FILES = ["a_01.ma", "b_02.ma", "c_03.ma", "d_04.ma", "e_05.ma"]
for n in FILES:
    open(os.path.join(deep, n), "w").close()
b.navigate_to(deep)


def _move(view, pt):
    """eventFilter へ直接 MouseMove を流し、消費されたか（True）を返す。"""
    from core.compat import QtGui
    ev = QtGui.QMouseEvent(bp._QtCore.QEvent.MouseMove,
                           bp._QtCore.QPointF(pt),
                           Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    return cv.eventFilter(view.viewport(), ev)


def s1():
    # 一番下の項目を押す（押下は自前で消費され、pending drag になる）
    cvw, rect, _ = find_item(b, "e_05.ma")
    assert cvw, "e_05.ma が見つからない"
    p0 = rect.center()
    QTest.mousePress(cvw.viewport(), Qt.LeftButton, Qt.NoModifier, p0)
    app.processEvents()
    before = sorted(os.path.basename(p) for p in b._get_selected_paths())

    # 閾値未満のわずかな移動 → 必ず消費されること（素通し＝矩形選択の原因）
    consumed = _move(cvw, p0 + bp.QPoint(1, 1))
    app.processEvents()
    assert consumed is True, "閾値未満の MouseMove が素通ししている（矩形選択が走る）"

    after = sorted(os.path.basename(p) for p in b._get_selected_paths())
    assert after == before, ("ドラッグ前の移動で選択が変わった", before, after)
    for n in ("a_01.ma", "b_02.ma", "c_03.ma", "d_04.ma"):
        assert n not in after, ("対象より上の項目が選択された", n, after)
    print("sub-threshold move consumed, selection untouched: OK")

    QTest.mouseRelease(cvw.viewport(), Qt.LeftButton, Qt.NoModifier, p0)
    finish(True)


run(s1, delay=800)
