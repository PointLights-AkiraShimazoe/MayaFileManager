# -*- coding: utf-8 -*-
"""r92: D&D の対象は «ドラッグ前に選んだものだけ»。
QColumnView は全カラムで選択モデルを共有するため、放っておくと左のカラムの
パンくず（祖先フォルダ）まで一緒にドラッグされる（ユーザー報告 2026-09-23）。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, QTimer, Qt
from _common import QTest
import ui.browser_panel as bp

b = make_panel(1100, 600)
cv = b._column_view
root = tmpdir()
deep = os.path.join(root, "proj", "scenes")
os.makedirs(deep)
FILES = ["chr_A.ma", "chr_B.ma", "note.txt"]
for n in FILES:
    open(os.path.join(deep, n), "w").close()
os.makedirs(os.path.join(deep, "sub"))
b.navigate_to(deep)
DRAGGED = []


class _FakeDrag:
    """QDrag を差し替えて «何がドラッグされたか» を捕まえる。"""
    def __init__(self, *a, **k):
        self._mime = None

    def setMimeData(self, m):
        self._mime = m
        DRAGGED.append([os.path.basename(u.toLocalFile()) for u in m.urls()])

    def setPixmap(self, *a, **k):
        pass

    def exec(self, *a, **k):
        return 0
    exec_ = exec


def _press_move(name, mods=Qt.NoModifier):
    """その項目を押して、ドラッグ開始のしきい値を超えて動かす。"""
    cvw, rect, _ = find_item(b, name); assert cvw, name
    p0 = rect.center()
    QTest.mousePress(cvw.viewport(), Qt.LeftButton, mods, p0)
    DRAGGED.clear()
    p1 = p0 + bp.QPoint(80, 0)
    from core.compat import QtGui
    ev = QtGui.QMouseEvent(QtCoreEvent(), bp._QtCore.QPointF(p1),
                           Qt.NoButton, Qt.LeftButton, mods)
    cv.eventFilter(cvw.viewport(), ev)
    QTest.mouseRelease(cvw.viewport(), Qt.LeftButton, mods, p1)


def QtCoreEvent():
    return bp._QtCore.QEvent.MouseMove


def s1():
    # 前提: 深い階層に居るので、左のカラムでは祖先（proj / scenes）が選択状態
    sel_all = [os.path.basename(p.rstrip("/\\")) for p in b._get_selected_paths()]
    assert any(x in sel_all for x in ("proj", "scenes")), ("前提が崩れた", sel_all)

    orig = bp.QDrag
    bp.QDrag = _FakeDrag
    try:
        # 1) ファイルを1つクリック → そのままドラッグ
        cvw, rect, _ = find_item(b, "chr_A.ma")
        QTest.mouseClick(cvw.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
        app.processEvents()
        _press_move("chr_A.ma")
        assert DRAGGED and DRAGGED[-1] == ["chr_A.ma"], \
            ("選んだファイル以外までドラッグされた", DRAGGED)
        print("single file drag carries only that file: OK")

        # 2) Ctrl で2つ選択 → その2つだけ
        cvw, r1, _ = find_item(b, "chr_A.ma")
        QTest.mouseClick(cvw.viewport(), Qt.LeftButton, Qt.NoModifier, r1.center())
        cvw2, r2, _ = find_item(b, "note.txt")
        QTest.mouseClick(cvw2.viewport(), Qt.LeftButton, Qt.ControlModifier, r2.center())
        app.processEvents()
        _press_move("note.txt")
        assert DRAGGED and sorted(DRAGGED[-1]) == ["chr_A.ma", "note.txt"], \
            ("複数選択の対象が違う", DRAGGED)
        print("multi selection drag carries exactly the selection: OK")

        # 3) 未選択のフォルダをそのままドラッグ → そのフォルダだけ
        #    （ネイティブ任せだと祖先フォルダまで含まれていた）
        _press_move("sub")
        assert DRAGGED and DRAGGED[-1] == ["sub"], ("フォルダのドラッグ対象が違う", DRAGGED)
        print("unselected folder drag carries only that folder: OK")
    finally:
        bp.QDrag = orig
    finish(True)


run(s1, delay=800)
