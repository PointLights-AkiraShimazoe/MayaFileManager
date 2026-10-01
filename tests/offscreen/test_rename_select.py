# -*- coding: utf-8 -*-
"""r98: リネーム後は «新しい名前の項目» が選択状態になる（インライン／ダイアログ）。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, Qt, QTest

b = make_panel(1200, 700)
root = tmpdir()
for n in ("aaa.ma", "bbb.ma", "ccc.ma"):
    open(os.path.join(root, n), "w").close()
b.navigate_to(root)


def _settle(ms=350):
    QTest.qWait(ms)
    app.processEvents()


def _selected():
    return sorted(os.path.basename(p) for p in b._get_selected_paths())


def s1():
    # ── インライン名前変更 ────────────────────────────────────────
    cvw, rect, _ = find_item(b, "bbb.ma")
    assert cvw, "bbb.ma が見つからない"
    QTest.mouseClick(cvw.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    _settle()
    assert b._rename_inline(os.path.join(root, "bbb.ma")), "インライン編集が開かない"
    ed = b._inline_editor
    assert ed is not None
    ed.setText("bbb_renamed.ma")
    ed.returnPressed.emit()
    _settle(900)
    assert os.path.isfile(os.path.join(root, "bbb_renamed.ma")), "リネームされていない"
    assert "bbb_renamed.ma" in _selected(), \
        ("リネーム後の項目が選択されていない", _selected())
    assert "bbb.ma" not in _selected()
    print("inline rename selects the renamed item: OK")

    # ── ダイアログ経路（_on_fs_file_renamed 共通後処理） ──────────
    os.rename(os.path.join(root, "ccc.ma"), os.path.join(root, "ccc_2.ma"))
    b._on_fs_file_renamed(root, "ccc.ma", "ccc_2.ma", record=False)
    _settle(900)
    assert "ccc_2.ma" in _selected(), ("ダイアログ経路で選択されない", _selected())
    print("dialog path selects the renamed item: OK")

    # ── 編集中は選択を奪わない（Tab 送りの連続リネームを壊さない） ──
    b._inline_editor = object()          # 編集中の模擬
    b._select_when_visible(os.path.join(root, "aaa.ma"))
    _settle(400)
    assert "aaa.ma" not in _selected(), ("編集中に選択を奪った", _selected())
    b._inline_editor = None
    print("does not steal selection while editing: OK")
    finish(True)


run(s1, delay=900)
