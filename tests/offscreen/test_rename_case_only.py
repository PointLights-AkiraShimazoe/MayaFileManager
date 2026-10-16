# -*- coding: utf-8 -*-
"""r146: 大文字小文字だけの名前変更を «同名がある» と弾かない。

ユーザー報告 2026-10-16「リネームの際、大文字小文字が区別されず同名と
されるのを解消して」: Windows は大文字小文字を区別しないので
abc.ma → Abc.ma は os.path.lexists(新) が True になり、警告で止まっていた。
ここでは lexists を Windows 相当（大文字小文字を無視）に差し替えて再現する。
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, QTest, Qt
from core.compat import QLineEdit
import ui.browser_rename_mixin as rm
from core.file_operations import is_case_only_rename

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


check(is_case_only_rename("/x/abc.ma", "/x/Abc.ma") is (os.path.normcase("A") == os.path.normcase("a")),
      "判定は OS の normcase に従う（Windows では True）")
# Windows 相当の判定に差し替えて本体を通す
real_normcase = os.path.normcase
os.path.normcase = lambda p: real_normcase(p).lower()
real_lexists = os.path.lexists
def ci_lexists(path):
    d, n = os.path.split(path)
    try:
        return any(x.lower() == n.lower() for x in os.listdir(d))
    except OSError:
        return real_lexists(path)
os.path.lexists = ci_lexists
warned = []
rm.QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a[2] if len(a) > 2 else a))
try:
    check(is_case_only_rename("/x/abc.ma", "/x/Abc.ma"), "差し替え後: abc.ma → Abc.ma は自分自身への付け替え")
    check(not is_case_only_rename("/x/abc.ma", "/x/abd.ma"), "別名は違う")
    d = tmpdir()
    open(os.path.join(d, "abc.ma"), "w").close()
    b = make_panel(1200, 700)
    b.navigate_to(d)
    QTest.qWait(500)
    app.processEvents()
    cv, rect, _ = find_item(b, "abc.ma")
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTest.qWait(300)
    app.processEvents()
    assert b._rename_inline()
    QTest.qWait(100)
    app.processEvents()
    ed = [e for e in b.findChildren(QLineEdit) if e.isVisible() and e.objectName() == "mfmInlineRename"][0]
    ed.selectAll()
    QTest.keyClicks(ed, "Abc.ma")
    QTest.keyClick(ed, Qt.Key_Return)
    QTest.qWait(500)
    app.processEvents()
    check(not warned, "«同名がある» の警告を出さない（%r）" % warned)
    check("Abc.ma" in os.listdir(d) and "abc.ma" not in os.listdir(d),
          "大文字小文字だけの名前変更ができる（%r）" % os.listdir(d))
finally:
    os.path.normcase = real_normcase
    os.path.lexists = real_lexists
finish(not fails)
