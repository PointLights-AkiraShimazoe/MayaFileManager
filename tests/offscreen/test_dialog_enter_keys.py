# -*- coding: utf-8 -*-
"""r106: プリセット設定のどこで Enter を押しても «新規» が立ち上がらない。

QDialog 内の QPushButton は既定で autoDefault=True のため、Enter が
«フォーカス連鎖で最初の autoDefault ボタン»（＝「✚ 新規」）を押していた。
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest
from core.compat import Qt, QPushButton, QLineEdit, QListWidget
from ui.quick_nav_editor import QuickNavPresetEditor

sm.save_quick_nav_presets({"default": [{"label": "A", "path": "/tmp"}],
                           "MM": [{"label": "B", "path": "/tmp"}]})
OPENED = []


def s1():
    import ui.quick_nav_editor as qn
    # 「新しいプリセット」ダイアログが開いたら記録する
    orig = qn.QInputDialog.getText

    def spy(*a, **k):
        OPENED.append(a[1] if len(a) > 1 else "?")
        return ("", False)
    qn.QInputDialog.getText = staticmethod(spy)
    try:
        d = QuickNavPresetEditor(sm)
        d.show()
        app.processEvents()

        # 1) 全ボタンの autoDefault が切れている
        bad = [b.text() for b in d.findChildren(QPushButton)
               if b.autoDefault() or b.isDefault()]
        assert not bad, ("autoDefault が残っているボタン", bad)

        # 2) 名前欄で Enter → 改名されるだけ。新規ダイアログは出ない
        d._preset_list.setCurrentRow(0)
        app.processEvents()
        cur = d._current_preset
        d._name_edit.setText("renamed_x")
        QTest.keyClick(d._name_edit, Qt.Key_Return)
        app.processEvents()
        assert not OPENED, ("名前欄の Enter で新規ダイアログが開いた", OPENED)
        assert d._current_preset == "renamed_x", ("改名されていない", d._current_preset)
        assert "renamed_x" in d._presets and cur not in d._presets
        assert d.isVisible(), "Enter でダイアログが閉じた"

        # 3) 他のウィジェット（リスト・項目欄）で Enter を押しても出ない
        for w in [d._preset_list] + d.findChildren(QLineEdit):
            w.setFocus()
            QTest.keyClick(w, Qt.Key_Return)
            app.processEvents()
        assert not OPENED, ("どこかの Enter で新規ダイアログが開いた", OPENED)
        assert d.isVisible(), "Enter でダイアログが閉じた"
        print("Enter never triggers 'new preset' in the quick-nav editor: OK")

        # 4) 「✚ 新規」ボタン自体はちゃんと動く
        for b in d.findChildren(QPushButton):
            if "新規" in b.text():
                b.click()
                break
        app.processEvents()
        assert OPENED, "新規ボタンが機能していない"
        print("the new-preset button itself still works: OK")
        d.close()
    finally:
        qn.QInputDialog.getText = orig

    # 参照プリセットエディタも同じ対策が入っている
    from ui.preset_editor import ReferencePresetEditor
    d2 = ReferencePresetEditor(sm)
    d2.show()
    app.processEvents()
    bad2 = [b.text() for b in d2.findChildren(QPushButton)
            if b.autoDefault() or b.isDefault()]
    assert not bad2, ("参照プリセット側に autoDefault が残っている", bad2)
    QTest.keyClick(d2, Qt.Key_Return)
    app.processEvents()
    assert d2.isVisible(), "Enter で閉じた"
    d2.close()
    print("reference preset editor is guarded too: OK")
    finish(True)


run(s1, delay=500)
