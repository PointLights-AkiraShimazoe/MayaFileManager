# -*- coding: utf-8 -*-
"""r146: その場編集中に別の場所へ移ったら、確定してから移る。

ユーザー報告 2026-10-16:
  「リネームを確定させないまま別のフォルダを表示すると、リネーム表示状態で
    残ってしまいます。別の場所が選択されたときには、確定処理をしてから
    移ってください。」

固定すること:
  1) 編集欄の外をマウスで押す → 押下がビューへ届く前に確定（名前が変わる）、
     編集欄は消える、クリックした先への移動はそのまま行われる
  2) アドレス欄・ブックマーク等からの navigate_to でも確定してから移る
  3) 編集欄の中を押しても確定しない（続けて打てる）
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, QTest, Qt
from core.compat import QLineEdit

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


d = tmpdir()
os.makedirs(os.path.join(d, "sub_a"))
os.makedirs(os.path.join(d, "sub_b"))
open(os.path.join(d, "old_1.ma"), "w").close()
open(os.path.join(d, "old_2.ma"), "w").close()

b = make_panel(1300, 700)
b.navigate_to(d)
QTest.qWait(600)
app.processEvents()


def editor():
    eds = [e for e in b.findChildren(QLineEdit) if e.isVisible()
           and e.objectName() == "mfmInlineRename"]
    return eds[0] if eds else None


def start_edit(name, new):
    cv, rect, _ = find_item(b, name)
    assert cv is not None, name
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTest.qWait(300)
    app.processEvents()
    assert b._rename_inline(), "editor did not open"
    QTest.qWait(100)
    app.processEvents()
    ed = editor()
    assert ed is not None, "no editor"
    ed.selectAll()
    QTest.keyClicks(ed, new)
    return ed


# ── 1) 編集中に別のフォルダをクリック → 確定してから移る ─────────
ed = start_edit("old_1.ma", "new_1.ma")
check(ed.text() == "new_1.ma", "前提: 打ちかけの名前がある")
cv, rect, _ = find_item(b, "sub_b")
QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
QTest.qWait(700)
app.processEvents()
check(os.path.exists(os.path.join(d, "new_1.ma")) and not os.path.exists(os.path.join(d, "old_1.ma")),
      "別の場所を押した時点で名前が確定している（%r）" % sorted(os.listdir(d)))
check(editor() is None, "編集欄が取り残されない")
check(os.path.normcase(b._current_path) == os.path.normcase(os.path.join(d, "sub_b")),
      "クリックした先（sub_b）へ移っている（%r）" % b._current_path)

# ── 2) navigate_to（アドレス欄・ブックマーク相当）でも確定 ────────
b.navigate_to(d)
QTest.qWait(500)
app.processEvents()
ed = start_edit("old_2.ma", "new_2.ma")
b.navigate_to(os.path.join(d, "sub_a"))
QTest.qWait(700)
app.processEvents()
check(os.path.exists(os.path.join(d, "new_2.ma")), "navigate_to の前に確定する（%r）" % sorted(os.listdir(d)))
check(editor() is None, "編集欄が残らない")

# ── 3) 編集欄の中を押しても確定しない ─────────────────────────────
b.navigate_to(d)
QTest.qWait(500)
app.processEvents()
ed = start_edit("new_2.ma", "typing.ma")
QTest.mouseClick(ed, Qt.LeftButton, Qt.NoModifier, ed.rect().center())
QTest.qWait(200)
app.processEvents()
check(editor() is not None and editor().text() == "typing.ma",
      "編集欄の中を押しても確定しない（続けて打てる）")
QTest.keyClick(editor(), Qt.Key_Escape)
QTest.qWait(200)
app.processEvents()
check(editor() is None and os.path.exists(os.path.join(d, "new_2.ma")), "Esc で取り消し")

finish(not fails)
