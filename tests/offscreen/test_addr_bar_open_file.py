# -*- coding: utf-8 -*-
"""r120: アドレス欄にファイルのフルパスを入れたら «関連付けで開く»。

ユーザー指示 2026-10-04: Explorer のアドレス欄と同じ挙動にしたい。
実行後は直前のパスへ戻るので、カラムの遷移は起きない。
"""
import os
from _common import *  # noqa: F401,F403
from _common import (app, make_panel, find_item_wait as find_item,
                     tmpdir, finish, run, Qt, QTest)
import ui.browser_panel as bp

root = tmpdir()
os.makedirs(os.path.join(root, "sub"))
target = os.path.join(root, "scene.ma")
open(target, "w").close()
open(os.path.join(root, "note.txt"), "w").close()
open(os.path.join(root, "sub", "inner.ma"), "w").close()

b = make_panel(1100, 650)
b.navigate_to(root)

opened = []
bp.open_with_default_app = lambda p: opened.append(p)


def _settle(ms=400):
    QTest.qWait(ms)
    app.processEvents()


def _state():
    """現在地・カラム構成（遷移が起きていないことの確認用）。"""
    cv = b._column_view
    return (b._current_path,
            tuple(cv._path_for_index(c.rootIndex()) for c in cv._live_columns()))


def s1():
    cvw, _r, _i = find_item(b, "scene.ma")
    assert cvw, "カラムに項目が出ていない"
    before = _state()
    hist_len = len(b._history)

    # ── 1) ファイルのフルパス → 関連付けで開く ─────────────────────
    opened.clear()
    b._addr_bar.setText(target)
    b._addr_bar.returnPressed.emit()
    _settle(600)
    assert opened == [os.path.normpath(target)], ("開かれていない", opened)
    # アドレス欄は直前のパスへ戻る
    assert b._addr_bar.text() == b._current_path, \
        ("アドレス欄が戻っていない", b._addr_bar.text(), b._current_path)
    # 現在地もカラム構成も «一切» 変わらない
    assert _state() == before, ("カラムが動いている", before, _state())
    assert len(b._history) == hist_len, "履歴に積まれている（移動していないのに）"
    print("a full file path opens with the associated app and nothing moves: OK")

    # ── 2) 引用符つき（«パスのコピー» で付く）でも通る ─────────────
    opened.clear()
    b._addr_bar.setText('"%s"' % os.path.join(root, "note.txt"))
    b._addr_bar.returnPressed.emit()
    _settle(600)
    assert opened == [os.path.normpath(os.path.join(root, "note.txt"))], \
        ("引用符つきで開けていない", opened)
    assert _state() == before, "カラムが動いている"
    print("a quoted path (as Copy as path gives it) works too: OK")

    # ── 3) フォルダは従来どおり «移動» する ───────────────────────
    opened.clear()
    sub = os.path.join(root, "sub")
    b._addr_bar.setText(sub)
    b._addr_bar.returnPressed.emit()
    _settle(900)
    assert opened == [], ("フォルダを関連付けで開いてしまっている", opened)
    assert os.path.normcase(b._current_path) == os.path.normcase(sub), \
        ("フォルダへ移動していない", b._current_path)
    print("a folder path still navigates as before: OK")

    # ── 4) 存在しないパスは何も開かず、移動もしない ────────────────
    opened.clear()
    keep = b._current_path
    b._addr_bar.setText(os.path.join(root, "no_such_file.ma"))
    b._addr_bar.returnPressed.emit()
    _settle(800)
    assert opened == [], ("存在しないのに開こうとしている", opened)
    assert b._current_path == keep, "存在しないパスで移動している"
    print("a non-existent path opens nothing and does not navigate: OK")
    finish(True)


run(s1, delay=900)
