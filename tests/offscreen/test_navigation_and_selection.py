# -*- coding: utf-8 -*-
"""クリック遷移・選択済み再クリック・Ctrl複数選択/解除・ファイルクリック無スクロール。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, columns, tmpdir, finish, run, QTimer, Qt
from _common import QTest

b = make_panel(900, 600)
d = tmpdir()
p = d
for i in range(4):
    p = os.path.join(p, f"lv{i}"); os.makedirs(p)
for n in ("aa", "bb", "cc"):
    os.makedirs(os.path.join(p, n))
for n in ("scene_a.ma", "scene_b.ma"):
    open(os.path.join(p, n), "w").close()
b.navigate_to(p)


def s1():
    cv, rect, _ = find_item(b, "bb"); assert cv, "bb"
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTimer.singleShot(1200, s2)


def s2():
    assert b.current_path().replace("\\", "/").endswith("/bb"), b.current_path()
    print("click nav: OK")
    cv, rect, _ = find_item(b, "bb")
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTimer.singleShot(1000, s3)


def s3():
    assert b.current_path().replace("\\", "/").endswith("/bb")
    assert b._column_view._pending_multi_drag is None
    print("re-click on selected: OK")
    cv, rect, _ = find_item(b, "cc")
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTimer.singleShot(1000, s4)


def s4():
    assert b.current_path().replace("\\", "/").endswith("/cc")
    cv, rect, _ = find_item(b, "aa")
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.ControlModifier, rect.center())

    def chk():
        sel = [x.data() for x in cv.selectionModel().selectedIndexes() if x.column() == 0]
        assert "aa" in sel and "cc" in sel, sel
        print("ctrl multi-select: OK")
        QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.ControlModifier, rect.center())

        def chk2():
            sel2 = [x.data() for x in cv.selectionModel().selectedIndexes() if x.column() == 0]
            assert "aa" not in sel2, sel2
            print("ctrl toggle-off: OK")
            # 平坦ビューを閉じて通常状態に戻してからファイルクリック検証
            b._on_flat_request([])
            QTimer.singleShot(800, s5)
        QTimer.singleShot(800, chk2)
    QTimer.singleShot(800, chk)


def s5():
    xs0 = [(v.x(), v.width()) for v in columns(b)]
    n0 = len(columns(b))
    cv, rect, _ = find_item(b, "scene_a.ma"); assert cv, "file"
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())

    def after():
        xs1 = [(v.x(), v.width()) for v in columns(b)]
        assert len(columns(b)) == n0 and xs1 == xs0, "columns moved on file click"
        assert b._addr_bar.text().endswith("scene_a.ma")
        # 同じ階層（このカラム）で選択されているのはこのファイルだけ
        # （パンくず＝祖先の選択は仕様として残る）
        sel = [x.data() for x in cv.selectionModel().selectedIndexes()
               if x.column() == 0 and x.parent() == cv.rootIndex()]
        assert sel == ["scene_a.ma"], sel
        print("file click: no scroll, selected, addr: OK")
        finish()
    QTimer.singleShot(600, after)


run(s1)
