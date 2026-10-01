# -*- coding: utf-8 -*-
"""全ダイアログ/パネルの生成＋主要操作（モーダルはスタブ）。"""
import os, tempfile, traceback
from _common import *  # noqa: F401,F403
from _common import app, sm
from core.compat import QPushButton, QToolButton, QDialog, QMessageBox, QLineEdit, QInputDialog, QFileDialog

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)
QInputDialog.getText = staticmethod(lambda *a, **k: ("smoke_test", True))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
QDialog.exec = lambda self: (self.show(), app.processEvents(), 0)[2]
QDialog.exec_ = QDialog.exec
SKIP = ("キャンセル", "Cancel", "閉じる", "Close", "削除", "Delete", "終了", "参照", "...", "…",
        "保存", "適用", "Apply", "OK", "リセット", "Reset", "クリア", "Clear")


def click_all(w):
    for b in w.findChildren(QPushButton) + w.findChildren(QToolButton):
        if any(s in b.text() for s in SKIP) or not b.isEnabled():
            continue
        b.click(); app.processEvents()


def t_quick_nav():
    from ui.quick_nav_editor import QuickNavPresetEditor
    d = QuickNavPresetEditor(sm); d.show(); app.processEvents()
    n0 = len(d._rows)
    for b in d.findChildren(QPushButton):
        if "ボタンを追加" in b.text() or "Add" in b.text():
            b.click(); app.processEvents()
    assert len(d._rows) == n0 + 1, "add via button failed"
    row = d._rows[-1]
    ups = [x for x in row.findChildren(QToolButton) if x.text() in ("▲", "▼")]
    assert ups, "▲▼ buttons missing"
    # グリフ幅チェック（r30 の QSS padding 回帰の検出）。mayapy オフスクリーンは
    # Qt のフォントディレクトリが無くフォールバック字形が極端に広くなるため、
    # 実フォントが解決できている時だけ判定する（環境依存で落とさない）。
    fm = ups[0].fontMetrics()
    from core.compat import QtGui as _QG
    real_font = len(_QG.QFontDatabase.families()) > 0
    if real_font:
        assert fm.horizontalAdvance("▲") + 8 <= ups[0].width(), "glyph clipped"
    else:
        print("  (font fallback: glyph width check skipped)")
    d._move_row_down(d._rows[0]); d._move_row_up(d._rows[-1]); d._remove_row(d._rows[-1])
    # r63: 新規 → 名前欄で改名 → Enter で «閉じずに» 改名が反映される
    QInputDialog.getText = staticmethod(lambda *a, **k: ("tmp_new", True))
    d._new_preset(); app.processEvents()
    assert d._current_preset == "tmp_new" and "tmp_new" in d._presets
    d._name_edit.setText("renamed_new"); d._name_edit.returnPressed.emit(); app.processEvents()
    assert d.isVisible(), "Enter で閉じてはいけない"
    assert "renamed_new" in d._presets and "tmp_new" not in d._presets, list(d._presets)
    assert d._preset_list.findItems("renamed_new", Qt.MatchExactly), "list item not renamed"
    d._add_item(label="X", path="/tmp"); app.processEvents()
    d._save_and_close()
    saved = sm.get_quick_nav_presets()
    assert saved.get("renamed_new") and saved["renamed_new"][0]["label"] == "X", saved.get("renamed_new")


def t_settings():
    from ui.settings_dialog import SettingsDialog
    d = SettingsDialog(sm); d.show(); app.processEvents()
    for b in d.findChildren(QPushButton):
        if "ルールを追加" in b.text():
            b.click(); app.processEvents()
    click_all(d)


def t_others():
    from ui.launcher_dialog import LauncherDialog; LauncherDialog().show(); app.processEvents()
    from ui.preset_editor import ReferencePresetEditor; d = ReferencePresetEditor(sm); d.show(); click_all(d)
    from ui.reference_editor import ReferenceEditor; ReferenceEditor().show(); app.processEvents()
    from ui.batch_rename_dialog import BatchRenameDialog
    dd = tempfile.mkdtemp(); ps = []
    for n in ("a.ma", "b.ma"):
        p = os.path.join(dd, n); open(p, "w").close(); ps.append(p)
    d = BatchRenameDialog(ps); d.show()
    for e in d.findChildren(QLineEdit):
        e.setText("x"); app.processEvents()
    from core.bookmark_manager import BookmarkManager
    from ui.bookmark_panel import BookmarkPanel; w = BookmarkPanel(BookmarkManager(sm)); w.show(); click_all(w)
    from ui.merge_view import MergePanel; MergePanel().show()
    from ui.flat_column import FlatColumn; f = FlatColumn(); f.show(); f.set_sources([dd]); app.processEvents()
    from ui.common_columns import CommonFolderColumn; CommonFolderColumn(0).show(); app.processEvents()
    from ui.quick_look import QuickLookWindow; q = QuickLookWindow(); q.show()
    p = os.path.join(dd, "t.txt"); open(p, "w").write("hello"); q.show_for(p); app.processEvents()
    from ui.main_window import MainWindow
    w = MainWindow(sm); w.show(); w._add_area(); w._on_area_remove(w._areas[-1]); w._refresh_maya_connections(); w.close()


ok = True
for name, fn in [("quick_nav_editor", t_quick_nav), ("settings_dialog", t_settings), ("others", t_others)]:
    try:
        fn(); app.processEvents(); print("%s: OK" % name)
    except Exception:
        ok = False; print("%s: FAIL\n%s" % (name, traceback.format_exc()))
print("ALL OK" if ok else "FAILED")
