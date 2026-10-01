# -*- coding: utf-8 -*-
"""r66: ファイルのダブルクリックは «1回目» で関連付けアプリ起動（activated）に届く。
従来は1回目のプレスを自前消費するため Qt の pressedIndex が埋まらず、2回目の
ダブルクリックで初めて反応していた。フォルダのダブルクリックも1回で遷移する。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, columns, tmpdir, finish, run, QTimer, Qt, QTest
import ui.browser_panel as bp

d = tmpdir()
open(os.path.join(d, "cut.prproj"), "w").close()
os.makedirs(os.path.join(d, "sub"))
open(os.path.join(d, "sub", "inner.txt"), "w").close()
b = make_panel()
b.navigate_to(d)
opened = []
bp.open_with_default_app = lambda p: opened.append(p)


def s1():
    cv, rect, _ = find_item(b, "cut.prproj"); assert cv
    QTest.mouseDClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTimer.singleShot(300, s2)


def s2():
    assert len(opened) == 1 and opened[0].endswith("cut.prproj"), opened
    print("first double-click opens with default app: OK")
    # もう一度 → 2回目も1回分だけ（重複発火しない）
    cv, rect, _ = find_item(b, "cut.prproj")
    QTest.mouseDClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTimer.singleShot(300, s3)


def s3():
    assert len(opened) == 2, opened
    n0 = len(columns(b))
    cv, rect, _ = find_item(b, "sub"); assert cv
    QTest.mouseDClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTimer.singleShot(700, lambda: s4(n0))


def s4(n0):
    assert len(opened) == 2, "folder double-click must not open an app"
    assert b.current_path().replace("\\", "/").endswith("/sub"), b.current_path()
    assert find_item(b, "inner.txt")[0] is not None, "child column not shown"
    print("folder double-click navigates once, no app launch: OK")
    finish()


run(s1, delay=2500)
