# -*- coding: utf-8 -*-
"""r146: D&D 中、落下先が枠で分かる。

ユーザー報告 2026-10-15:
  「移動先でフォルダの上にマウスをもっていっても反応がない」

原因: カラムの DragEnter/DragMove は自前で消費している（Qt 標準の
dropMimeData に任せない設計、r87）ので、Qt 標準の «落下先の強調» が
一切出なかった。自前で «落ちる先» を枠で示す。
  - フォルダ項目の上 … その行を囲む
  - ファイル／空白の上 … そのカラムのフォルダに落ちるのでビュー全体を囲む
  - 外へ出た／落とした … 消える
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, find_item_wait, QTest, Qt
from core.compat import QtCore, QtGui, QUrl, QMimeData

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


root = tmpdir()
os.makedirs(os.path.join(root, "folderA"))
os.makedirs(os.path.join(root, "folderB"))
open(os.path.join(root, "file1.ma"), "w").close()
src = os.path.join(root, "src.ma")
open(src, "w").close()

b = make_panel(1200, 700)
_keep.append(b)
b.navigate_to(root)
QTest.qWait(900)
app.processEvents()

c, rect_a, idx_a = find_item_wait(b, "folderA")
check(c is not None, "前提: カラムに folderA が出ている")
_, rect_f, _ = find_item_wait(b, "file1.ma")
vp = c.viewport()
mime = QMimeData()
mime.setUrls([QUrl.fromLocalFile(src)])


def drag_move(pos):
    ev = QtGui.QDragMoveEvent(pos, Qt.MoveAction | Qt.CopyAction, mime,
                              Qt.LeftButton, Qt.NoModifier)
    b._column_view.eventFilter(vp, ev)
    app.processEvents()
    return ev


def hl():
    return getattr(c, "_mfm_drop_hl", None)


# ── フォルダの上 → その行を囲む ──
ev = drag_move(rect_a.center())
check(ev.isAccepted(), "フォルダの上の DragMove を受理する")
h = hl()
check(h is not None and h.isVisible(), "フォルダの上で枠が出る")
check(h is not None and h.geometry() == c.visualRect(idx_a),
      "枠はフォルダの行に合っている（%r vs %r）"
      % (h.geometry() if h else None, c.visualRect(idx_a)))

# ── ファイルの上 → カラム全体（そのフォルダに落ちる） ──
drag_move(rect_f.center())
h = hl()
check(h is not None and h.isVisible()
      and h.geometry() == vp.rect().adjusted(1, 1, -1, -1),
      "ファイルの上ではカラム全体を囲む（%r）" % (h.geometry() if h else None))

# ── 空白の上 → カラム全体 ──
drag_move(QtCore.QPoint(vp.width() // 2, vp.height() - 4))
h = hl()
check(h is not None and h.isVisible()
      and h.geometry() == vp.rect().adjusted(1, 1, -1, -1),
      "空白の上でもカラム全体を囲む")

# ── 外へ出る → 消える ──
b._column_view.eventFilter(vp, QtGui.QDragLeaveEvent())
app.processEvents()
h = hl()
check(h is not None and not h.isVisible(), "外へ出ると枠が消える")

# ── 落とす → 消える（ドロップ自体は従来どおり受理） ──
drag_move(rect_a.center())
check(hl().isVisible(), "再び入ると枠が出る")
dropped = []
b._column_view.set_file_drop_callback(
    lambda paths, dest, forced, gpos: dropped.append((paths, dest)))
ev = QtGui.QDropEvent(QtCore.QPointF(rect_a.center()), Qt.MoveAction, mime,
                      Qt.LeftButton, Qt.NoModifier, QtCore.QEvent.Drop)
b._column_view.eventFilter(vp, ev)
QTest.qWait(200)
app.processEvents()
check(not hl().isVisible(), "落とすと枠が消える")
check(dropped and os.path.normcase(dropped[-1][1]) == os.path.normcase(
    os.path.join(root, "folderA")), "落下先は folderA（%r）" % (dropped,))

finish(not fails)
