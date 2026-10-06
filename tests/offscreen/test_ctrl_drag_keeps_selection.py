# -*- coding: utf-8 -*-
"""r125: Ctrl を押したままドラッグしても選択が外れない。

ユーザー報告 2026-10-06:
  「複数選択をし、D&D をすると、一つ選択が外れる場合が多発してます」

原因: 押下ハンドラが «Ctrl が押されている» だけで _multi_select（トグル）へ
入っていた。Ctrl+クリックで複数選択した直後、指を Ctrl から離さずにドラッグを
始めるのは自然な操作で（Explorer では Ctrl+ドラッグ＝コピー）、そのたびに
**掴んだ項目だけが選択から外れ、しかもドラッグも始まらなかった**。

Explorer と同じく «離した時に初めてトグル» にする。ドラッグに至ったら
トグルしない。
"""
import os
from _common import *  # noqa: F401,F403
from _common import make_panel, tmpdir, find_item_wait, app, QTest, Qt, finish
from core.compat import QtCore, QtGui

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


root = tmpdir()
NAMES = ["a1.ma", "a2.ma", "a3.ma", "a4.ma", "a5.ma"]
for n in NAMES:
    open(os.path.join(root, n), "w").close()

b = make_panel(1300, 700)
_keep.append(b)
cv = b._column_view
b.navigate_to(root)
QTest.qWait(900)
app.processEvents()

dragged = {}
cv._start_multi_drag = lambda view, indexes: dragged.update(
    n=len([i for i in indexes if i.isValid()]))


def item(name):
    c, rect, idx = find_item_wait(b, name)
    assert c is not None, "項目が見つからない: " + name
    return c, rect, idx


def click(name, mods=Qt.NoModifier):
    c, rect, _ = item(name)
    QTest.mouseClick(c.viewport(), Qt.LeftButton, mods, rect.center())
    QTest.qWait(250)
    app.processEvents()


def rows(view):
    sm = view.selectionModel()
    if sm is None:
        return []
    root_idx = view.rootIndex()
    return sorted({i.row() for i in sm.selectedIndexes()
                   if i.column() == 0 and i.parent() == root_idx})


def ev(view, kind, pos, mods):
    return QtGui.QMouseEvent(
        kind, QtCore.QPointF(pos),
        QtCore.QPointF(view.viewport().mapToGlobal(pos)),
        Qt.LeftButton,
        Qt.LeftButton if kind != QtCore.QEvent.MouseButtonRelease else Qt.NoButton,
        mods)


def select_three():
    click("a1.ma")
    click("a3.ma", Qt.ControlModifier)
    click("a5.ma", Qt.ControlModifier)


# ── 1) Ctrl を押したまま掴む → 選択は変わらず、全件ドラッグ ───────────
select_three()
c, rect, _ = item("a3.ma")
before = rows(c)
check(len(before) == 3, "前提: 3 件選択できている（%r）" % before)

pos = rect.center()
cv.eventFilter(c.viewport(), ev(c, QtCore.QEvent.MouseButtonPress, pos, Qt.ControlModifier))
check(rows(c) == before, "Ctrl+押下だけでは選択が変わらない（%r）" % rows(c))
check(cv._pending_multi_drag is not None, "Ctrl+押下でもドラッグ候補になる")

dragged.clear()
cv.eventFilter(c.viewport(),
               ev(c, QtCore.QEvent.MouseMove, pos + QtCore.QPoint(60, 0), Qt.ControlModifier))
app.processEvents()
check(dragged.get("n") == 3,
      "Ctrl ドラッグでも 3 件すべて渡る（%r 件）" % dragged.get("n"))
check(rows(c) == before, "ドラッグ後も選択は 3 件のまま（%r）" % rows(c))

# ── 2) Ctrl+クリック（動かさず離す）は従来どおりトグルする ────────────
select_three()
c, rect, _ = item("a3.ma")
pos = rect.center()
cv.eventFilter(c.viewport(), ev(c, QtCore.QEvent.MouseButtonPress, pos, Qt.ControlModifier))
cv.eventFilter(c.viewport(), ev(c, QtCore.QEvent.MouseButtonRelease, pos, Qt.ControlModifier))
QTest.qWait(250)
app.processEvents()
after = rows(c)
check(len(after) == 2 and before[1] not in after,
      "Ctrl+クリックは離した時にトグルする（%r → %r）" % (before, after))

# ── 3) 修飾キー無しのドラッグは従来どおり全件 ─────────────────────────
select_three()
c, rect, _ = item("a3.ma")
pos = rect.center()
cv.eventFilter(c.viewport(), ev(c, QtCore.QEvent.MouseButtonPress, pos, Qt.NoModifier))
dragged.clear()
cv.eventFilter(c.viewport(),
               ev(c, QtCore.QEvent.MouseMove, pos + QtCore.QPoint(60, 0), Qt.NoModifier))
app.processEvents()
check(dragged.get("n") == 3, "通常ドラッグも 3 件すべて渡る（%r 件）" % dragged.get("n"))

# ── 4) 平坦ビューでも同じこと ─────────────────────────────────────────
# r125: 平坦カラムの _DragListView は «修飾キー無し» の時だけドラッグ候補に
# しており、Ctrl を押したまま掴むと Qt 標準の処理に落ちて «その場でトグル»＝
# 掴んだ項目が選択から外れていた。
fv = b._flat_col._view
b._on_flat_request([root])
QTest.qWait(600)
app.processEvents()

fsm = fv.selectionModel()
m = fv.model()
n = m.rowCount()
check(n >= 3, "前提: 平坦ビューに項目が出ている（%d 件）" % n)
if n >= 3:
    from core.compat import QtCore as _QC
    QISM = _QC.QItemSelectionModel
    for r in (0, 2, 4):
        if r < n:
            fsm.select(m.index(r, 0), QISM.Select | QISM.Rows)
    want = sorted({i.row() for i in fsm.selectedIndexes() if i.column() == 0})
    check(len(want) >= 3, "前提: 平坦ビューで 3 件以上選べている（%r）" % want)

    target = m.index(2, 0)
    rect = fv.visualRect(target)
    pos = rect.center()

    dragged.clear()
    fv._start_drag = lambda: dragged.update(
        n=len([i for i in fv.selectedIndexes() if i.column() == 0]))

    fv.mousePressEvent(QtGui.QMouseEvent(
        _QC.QEvent.MouseButtonPress, QtCore.QPointF(pos),
        QtCore.QPointF(fv.mapToGlobal(pos)),
        Qt.LeftButton, Qt.LeftButton, Qt.ControlModifier))
    got = sorted({i.row() for i in fsm.selectedIndexes() if i.column() == 0})
    check(got == want, "平坦: Ctrl+押下だけでは選択が変わらない（%r）" % got)

    fv.mouseMoveEvent(QtGui.QMouseEvent(
        _QC.QEvent.MouseMove, QtCore.QPointF(pos + QtCore.QPoint(60, 0)),
        QtCore.QPointF(fv.mapToGlobal(pos + QtCore.QPoint(60, 0))),
        Qt.LeftButton, Qt.LeftButton, Qt.ControlModifier))
    app.processEvents()
    check(dragged.get("n") == len(want),
          "平坦: Ctrl ドラッグでも全件渡る（%r / 期待 %d）"
          % (dragged.get("n"), len(want)))

    # Ctrl+クリック（動かさず離す）は従来どおりトグル解除
    fv.mousePressEvent(QtGui.QMouseEvent(
        _QC.QEvent.MouseButtonPress, QtCore.QPointF(pos),
        QtCore.QPointF(fv.mapToGlobal(pos)),
        Qt.LeftButton, Qt.LeftButton, Qt.ControlModifier))
    fv.mouseReleaseEvent(QtGui.QMouseEvent(
        _QC.QEvent.MouseButtonRelease, QtCore.QPointF(pos),
        QtCore.QPointF(fv.mapToGlobal(pos)),
        Qt.LeftButton, Qt.NoButton, Qt.ControlModifier))
    app.processEvents()
    got = sorted({i.row() for i in fsm.selectedIndexes() if i.column() == 0})
    check(2 not in got and len(got) == len(want) - 1,
          "平坦: Ctrl+クリックは離した時にトグル解除（%r → %r）" % (want, got))

finish(not fails)
