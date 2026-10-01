# -*- coding: utf-8 -*-
"""F2 のインライン名前変更（r61）: カラム内でエディタが開き、確定でファイルが
リネームされ、パス欄が追従する。current（列のスライド）は動かない。
あわせて delete_items が読み取り専用ファイルを消せること（r61）。"""
import os, stat
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, columns, tmpdir, finish, run, QTimer, Qt
from _common import QTest
from core.compat import QLineEdit, QAbstractItemView
from core.file_operations import delete_items

d = tmpdir()
for n in ("old_name.ma", "other.ma"):
    open(os.path.join(d, n), "w").close()
b = make_panel()
b.navigate_to(d)


def s1():
    cv, rect, idx = find_item(b, "old_name.ma"); assert cv
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTimer.singleShot(400, s2)


def s2():
    # 切り分け: ファイルクリックでパス欄がファイルまで追従しているか（r71 診断）
    assert b._addr_bar.text().endswith("old_name.ma"), \
        ("click did not update addr bar", b._addr_bar.text(), b._current_path)
    xs0 = [v.x() for v in columns(b)]
    hv0 = b._column_view.horizontalScrollBar().value()
    ok = b._rename_inline()
    assert ok, "inline editor did not open"
    cv, _, _ = find_item(b, "old_name.ma")
    eds = [e for e in cv.findChildren(QLineEdit) if e.isVisible()
           and e.objectName() == "mfmInlineRename"]
    assert eds, "no inline editor widget"
    ed = eds[0]
    # Explorer 同様、拡張子を除いた部分が選択されている
    assert ed.selectedText() == "old_name", ed.selectedText()
    ed.selectAll(); QTest.keyClicks(ed, "new_name.ma"); QTest.keyClick(ed, Qt.Key_Return)
    QTimer.singleShot(500, lambda: s3(xs0, hv0))


def s3(xs0, hv0):
    assert os.path.exists(os.path.join(d, "new_name.ma")), os.listdir(d)
    assert not os.path.exists(os.path.join(d, "old_name.ma"))
    assert b._addr_bar.text().endswith("new_name.ma"), b._addr_bar.text()
    assert [v.x() for v in columns(b)] == xs0 and \
        b._column_view.horizontalScrollBar().value() == hv0, "columns moved on rename"
    print("F2 inline rename: editor / rename / addr bar / no slide: OK")
    # Ctrl+Z 相当（ワーカー経由）で元に戻る
    b._undo_op()
    QTimer.singleShot(600, s3b)


def s3b():
    assert os.path.exists(os.path.join(d, "old_name.ma")), os.listdir(d)
    b._redo_op()
    QTimer.singleShot(600, s3c)


def s3c():
    assert os.path.exists(os.path.join(d, "new_name.ma")), os.listdir(d)
    print("rename undo/redo via panel (worker + progress path): OK")
    # 読み取り専用ファイルの削除（Perforce 同期ファイル相当）
    ro = os.path.join(d, "ro.fbx"); open(ro, "w").close()
    os.chmod(ro, stat.S_IREAD)
    failed = delete_items([ro], use_trash=False)
    assert not failed and not os.path.exists(ro), failed
    sub = os.path.join(d, "ro_dir"); os.makedirs(sub)
    f = os.path.join(sub, "x.ma"); open(f, "w").close(); os.chmod(f, stat.S_IREAD)
    failed = delete_items([sub], use_trash=False)
    assert not failed and not os.path.exists(sub), failed
    failed = delete_items([os.path.join(d, "nope_dir_x", "y")], use_trash=False)
    assert failed == [], failed          # 存在しないものは無視
    print("delete read-only file/dir: OK")
    finish()


run(s1, delay=2500)
