# -*- coding: utf-8 -*-
"""r146: 平坦ビューの D&D は «選択したものを正確に» 運ぶ。

ユーザー報告 2026-10-15:
  「平坦ビューの複数選択から D&D の挙動は直ってません。D&D を始めたら
    一つ欠けます。また、その後 1 点だけを D&D したいのに、その場合だけ
    全てが選択される」

原因: _DragListView が «閾値未満の MouseMove» を Qt に渡していた。
QAbstractItemView は左ボタン押下中の移動で DragSelectingState に入り、
«前回の押下位置〜カーソル» の矩形で選択を組み替える（Clear|SelectCurrent）。
実機のマウスは最初に 1〜9px の移動イベントを出すので、
- 複数選択を掴む → 範囲外が落ちる（一つ欠ける）
- 未選択の 1 点を掴んで動かす → ドラッグにならず範囲選択（全部選ばれる）
になっていた。従来のテストは閾値を一跨ぎ（60px）していたので再現しなかった。

ここでは «実機と同じ» に、押下 → 小さな移動 ×3 → 大きな移動の順で送る。
"""
import os
from _common import *  # noqa: F401,F403
from _common import make_panel, tmpdir, app, QTest, Qt, finish
from core.compat import QtCore, QtGui

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


root = tmpdir()
NAMES = ["f1.ma", "f2.ma", "f3.ma", "f4.ma", "f5.ma", "f6.ma"]
for n in NAMES:
    open(os.path.join(root, n), "w").close()

b = make_panel(1300, 700)
_keep.append(b)
b.navigate_to(root)
QTest.qWait(600)
app.processEvents()
b._on_flat_request([root])
QTest.qWait(600)
app.processEvents()

fv = b._flat_col._view
vp = fv.viewport()
m = fv.model()
sm = fv.selectionModel()
check(m.rowCount() == len(NAMES), "前提: 平坦ビューに %d 件（%d）" % (len(NAMES), m.rowCount()))

dragged = []


def _fake_start_drag():
    dragged.append(sorted(os.path.basename(p) for p in fv._selected_paths()))


fv._start_drag = _fake_start_drag


def ev(kind, pos, mods=Qt.NoModifier, button=Qt.LeftButton):
    buttons = Qt.LeftButton if kind != QtCore.QEvent.MouseButtonRelease else Qt.NoButton
    if kind == QtCore.QEvent.MouseMove:
        button = Qt.NoButton
    try:
        return QtGui.QMouseEvent(kind, QtCore.QPointF(pos),
                                 QtCore.QPointF(vp.mapToGlobal(pos)),
                                 button, buttons, mods)
    except TypeError:
        return QtGui.QMouseEvent(kind, pos, vp.mapToGlobal(pos), button, buttons, mods)


def send(kind, pos, mods=Qt.NoModifier):
    app.sendEvent(vp, ev(kind, pos, mods))
    app.processEvents()


def center(row):
    return fv.visualRect(m.index(row, 0)).center()


def selected_names():
    return sorted(os.path.basename(m.data(i, fv._path_role))
                  for i in sm.selectedIndexes() if i.column() == 0)


def click(row, mods=Qt.NoModifier):
    p = center(row)
    send(QtCore.QEvent.MouseButtonPress, p, mods)
    send(QtCore.QEvent.MouseButtonRelease, p, mods)


def drag_from(pos, mods=Qt.NoModifier):
    """実機の順: 押下 → 1〜3px の移動 → 閾値超えの移動。"""
    send(QtCore.QEvent.MouseButtonPress, pos, mods)
    for d in (1, 2, 3):
        send(QtCore.QEvent.MouseMove, pos + QtCore.QPoint(d, d), mods)
    send(QtCore.QEvent.MouseMove, pos + QtCore.QPoint(80, 40), mods)
    app.processEvents()


# ── 1) Ctrl+クリックで飛び飛びに 3 件 → 真ん中を掴んで D&D → 3 件全部 ──
click(0)
click(2, Qt.ControlModifier)
click(4, Qt.ControlModifier)
want = ["f1.ma", "f3.ma", "f5.ma"]
check(selected_names() == want, "前提: Ctrl+クリックで 3 件（%r）" % selected_names())

dragged[:] = []
drag_from(center(2))
check(dragged and dragged[-1] == want,
      "飛び飛び 3 件を掴むと 3 件そのまま運ぶ（%r）" % (dragged[-1] if dragged else None))
check(selected_names() == want, "ドラッグ後も選択は 3 件のまま（%r）" % selected_names())

# ── 2) 直後に «未選択の 1 点» を掴んで D&D → その 1 点だけ ────────────
dragged[:] = []
drag_from(center(1))
check(dragged and dragged[-1] == ["f2.ma"],
      "未選択の 1 点を掴むとその 1 点だけ運ぶ（%r）" % (dragged[-1] if dragged else None))
check(selected_names() == ["f2.ma"], "選択もその 1 点だけ（%r）" % selected_names())

# ── 3) Shift で範囲 → 端を掴んで D&D → 範囲全部 ─────────────────────
# Qt 6.11 の Shift 範囲選択は «QGuiApplication::keyboardModifiers()» も見る。
# sendEvent で作った押下だけでは Shift が立たないので、本物の QTest で
# キーを押した状態にしてからクリックする（実機の入力ではもちろん立つ）。
click(1)
try:
    QTest.keyPress(vp, Qt.Key_Shift, Qt.ShiftModifier)
    QTest.mouseClick(vp, Qt.LeftButton, Qt.ShiftModifier, center(4))
    QTest.keyRelease(vp, Qt.Key_Shift, Qt.NoModifier)
except Exception as _e:
    print("SKIP Shift 範囲（この環境の QTest では再現できない: %r）" % (_e,))
app.processEvents()
want = ["f2.ma", "f3.ma", "f4.ma", "f5.ma"]
if selected_names() == want:
    print("OK   前提: Shift で 4 件")
    dragged[:] = []
    drag_from(center(4))
    check(dragged and dragged[-1] == want,
          "範囲の端を掴んでも 4 件そのまま運ぶ（%r）" % (dragged[-1] if dragged else None))
    check(selected_names() == want, "ドラッグ後も 4 件のまま（%r）" % selected_names())
else:
    print("SKIP Shift 範囲（前提が作れない: %r）" % selected_names())
    for r in (1, 2, 3, 4):
        sm.select(m.index(r, 0), QtCore.QItemSelectionModel.Select
                  | QtCore.QItemSelectionModel.Rows)
    want = selected_names()

# ── 4) 選択済みを動かさず離す → その 1 点に確定（clicked も出る） ────
clicked = []
fv.clicked.connect(lambda i: clicked.append(i.row()))
p = center(2)
send(QtCore.QEvent.MouseButtonPress, p)
check(selected_names() == want, "押下だけでは %d 件のまま（%r）" % (len(want), selected_names()))
send(QtCore.QEvent.MouseButtonRelease, p)
check(selected_names() == ["f3.ma"], "動かさず離すと単一選択（%r）" % selected_names())
check(clicked == [2], "clicked が 1 回出る（%r）" % clicked)

# ── 5) 未選択を Ctrl で掴んで D&D → 既存選択＋その 1 点 ────────────
click(0)
dragged[:] = []
drag_from(center(5), Qt.ControlModifier)
check(dragged and dragged[-1] == ["f1.ma", "f6.ma"],
      "Ctrl で未選択を掴むと既存＋その 1 点（%r）" % (dragged[-1] if dragged else None))

# ── 6) 未選択をクリック（動かさず） → 単一選択、clicked ─────────────
clicked[:] = []
click(3)
check(selected_names() == ["f4.ma"], "未選択クリックは単一選択（%r）" % selected_names())
check(clicked == [3], "未選択クリックでも clicked が出る（%r）" % clicked)

finish(not fails)
