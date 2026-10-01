# -*- coding: utf-8 -*-
"""r67: ヘッダ埋め込みアイコンの整合（7個が 24x24 に復元できる）、DccHeader の
スイッチ、複製（Ctrl+D）の命名規則と Search/Replace、Tab での名前変更移動。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, QTimer, Qt, QTest
from core import hdr_icons
from core.file_operations import default_copy_name, apply_replace_name, duplicate_items
from core.compat import QLineEdit

# 1) アイコン
for n in ("maya", "blender", "launch", "refresh", "front", "click", "dnd"):
    a = hdr_icons.decode_alpha(n)
    assert a is not None and len(a) == 576, ("icon data broken", n, None if a is None else len(a))
    assert sum(1 for v in a if v > 0) > 40, ("icon empty", n)
    pm = hdr_icons.make_pixmap(n)
    assert pm is not None and pm.width() == 24, n
from ui.dcc_header import header_icon, DccHeader
assert not header_icon("launch").isNull()
print("header icons (7, 24x24): OK")

# 2) DccHeader スイッチ
h = DccHeader("maya"); h.show(); app.processEvents()
got = []
h.dcc_changed.connect(got.append)
h.switch.set_value("blender")
assert got == ["blender"] and h.dcc() == "blender"
h.maya_badge.click(); app.processEvents()
assert got[-1] == "maya" and h.maya_badge.property("active") == "1"
print("DccHeader switch/badges: OK")

# 3) 複製の命名
d = tmpdir()
for n in ("a.ma", "a_Copy.ma"):
    open(os.path.join(d, n), "w").close()
os.makedirs(os.path.join(d, "chr_A", "sub"))
open(os.path.join(d, "chr_A", "chr_A_model.ma"), "w").close()
open(os.path.join(d, "chr_A", "sub", "chr_A_rig.ma"), "w").close()
assert default_copy_name(os.path.join(d, "a.ma")) == "a_Copy2.ma"
assert default_copy_name(os.path.join(d, "chr_A")) == "chr_A_Copy"
assert apply_replace_name("chr_A_model.ma", "chr_A", "chr_B") == "chr_B_model.ma"
res = duplicate_items([(os.path.join(d, "chr_A"), os.path.join(d, "chr_B"))],
                      rename_inside=("chr_A", "chr_B"))
assert os.path.exists(os.path.join(d, "chr_B", "chr_B_model.ma")), os.listdir(os.path.join(d, "chr_B"))
assert os.path.exists(os.path.join(d, "chr_B", "sub", "chr_B_rig.ma"))
assert os.path.exists(os.path.join(d, "chr_A", "chr_A_model.ma"))      # 元は不変
print("duplicate naming / replace / recursive: OK")

# 4) 複製ダイアログの specs
from ui.duplicate_dialog import DuplicateDialog
dlg = DuplicateDialog([os.path.join(d, "a.ma")])
assert dlg._name_edit.text() == "a_Copy2.ma" and dlg._name_edit.selectedText() == "a_Copy2"
assert dlg.specs()[0][1].endswith("a_Copy2.ma") and dlg.rename_inside() is None
dlg._mode_btn.setChecked(True); dlg._search_edit.setText("a"); dlg._replace_edit.setText("z")
dlg._recursive_cb.setChecked(True)
assert os.path.basename(dlg.specs()[0][1]) == "z.mz" and dlg.rename_inside() == ("a", "z")
print("duplicate dialog: OK")

# 5) Tab で次の項目の名前変更へ
b = make_panel()
for n in ("t1.ma", "t2.ma"):
    open(os.path.join(d, n), "w").close()
b.navigate_to(d)


def s1():
    cv, rect, _ = find_item(b, "t1.ma"); assert cv
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTimer.singleShot(300, s2)


def s2():
    assert b._rename_inline()
    cv, _, _ = find_item(b, "t1.ma")
    ed = [e for e in cv.findChildren(QLineEdit) if e.objectName() == "mfmInlineRename"][0]
    ed.selectAll(); QTest.keyClicks(ed, "u1.ma"); QTest.keyClick(ed, Qt.Key_Tab)
    QTimer.singleShot(500, s3)


def s3():
    assert os.path.exists(os.path.join(d, "u1.ma")), os.listdir(d)
    cv, _, _ = find_item(b, "t2.ma")
    eds = [e for e in cv.findChildren(QLineEdit)
           if e.objectName() == "mfmInlineRename" and e.isVisible()]
    assert eds and eds[0].text() == "t2.ma", "Tab did not move rename to next item"
    QTest.keyClick(eds[0], Qt.Key_Escape)
    print("rename Tab → next item: OK")
    finish()


run(s1, delay=2500)
