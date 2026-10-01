# -*- coding: utf-8 -*-
"""r73: Ctrl+Shift+N（新規フォルダ）/ Ctrl+Shift+T（新規テキスト）を «アクティブな
カラム» のフォルダへ作成し、そのままインライン名前変更モードに入る。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, QTimer, Qt
from _common import QTest
from core.compat import QLineEdit

b = make_panel()
d = tmpdir()
sub = os.path.join(d, "sub"); os.makedirs(sub)
open(os.path.join(sub, "x.ma"), "w").close()
b.navigate_to(d)


def _editor():
    return [e for e in b._column_view.findChildren(QLineEdit)
            if e.isVisible() and e.objectName() == "mfmInlineRename"]


def s1():
    # sub をクリック → 子カラムが出る → 子カラムをアクティブ（フォーカス）に
    cv, rect, _ = find_item(b, "sub"); assert cv
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTimer.singleShot(700, s2)


def s2():
    cv, rect, _ = find_item(b, "x.ma"); assert cv, "child column not shown"
    cv.setFocus(Qt.OtherFocusReason); app.processEvents()
    assert b._active_column_folder().replace("\\", "/") == sub.replace("\\", "/"), b._active_column_folder()
    # Ctrl+Shift+N 相当
    b._create_new_item("folder")
    p = os.path.join(sub, "新しいフォルダ")
    assert os.path.isdir(p), os.listdir(sub)
    QTimer.singleShot(1500, s3)


def s3():
    eds = _editor()
    assert eds, "inline editor not opened for new folder"
    assert eds[0].text() == "新しいフォルダ" and eds[0].selectedText() == "新しいフォルダ"
    eds[0].selectAll(); QTest.keyClicks(eds[0], "Assets"); QTest.keyClick(eds[0], Qt.Key_Return)
    QTimer.singleShot(400, s4)


def s4():
    assert os.path.isdir(os.path.join(sub, "Assets")) and not os.path.exists(os.path.join(sub, "新しいフォルダ"))
    print("Ctrl+Shift+N: new folder in active column + inline rename: OK")
    # 2回目は「(2)」になる（既存名との衝突回避）
    os.mkdir(os.path.join(sub, "新しいフォルダ"))
    assert b._unique_new_name(sub, "新しいフォルダ") == "新しいフォルダ (2)"
    # 新規テキスト（空白右クリック相当: フォルダ明示）
    b._create_new_item("text", d)
    p = os.path.join(d, "新しいテキスト ドキュメント.txt")
    assert os.path.isfile(p), os.listdir(d)
    QTimer.singleShot(1500, s5)


def s5():
    eds = _editor()
    assert eds and eds[0].text() == "新しいテキスト ドキュメント.txt", [e.text() for e in eds]
    assert eds[0].selectedText() == "新しいテキスト ドキュメント", eds[0].selectedText()
    QTest.keyClick(eds[0], Qt.Key_Escape)
    print("Ctrl+Shift+T: new text file + inline rename (stem selected): OK")
    finish()


run(s1, delay=2500)
