# -*- coding: utf-8 -*-
"""r120: 平坦カラムでもサムネイル表示が選べること（ユーザー指示）。

平坦カラムは QFileSystemModel ではなく QStandardItemModel + 独自ロールで
動くため、ThumbnailDelegate が filePath() を辿れない。パスの取り出しを
差し替えられるようにした（path_of_index）。ここではその配線と、
▦ ボタンでの切り替え・記憶・サイズ反映を検証する。
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, run, Qt, QTest, sm
from core.compat import QListView, QToolButton
from ui.browser_delegates import ThumbnailDelegate

b = make_panel(1200, 700)
d = tmpdir()
os.makedirs(os.path.join(d, "sub"), exist_ok=True)
for n in ("alpha.ma", "bravo.fbx", "delta.png"):
    open(os.path.join(d, n), "w").close()
open(os.path.join(d, "sub", "echo.ma"), "w").close()
b.navigate_to(d)

fc = b._flat_col


def _settle(ms=300):
    QTest.qWait(ms)
    app.processEvents()


def _view_btn():
    for btn in fc.findChildren(QToolButton):
        if btn.text() == "▦":
            return btn
    raise AssertionError("平坦カラムに ▦ ボタンが無い")


def s1():
    # 平坦カラムを出す（フォルダを直接渡す）
    fc.set_sources([d])
    fc.show()
    _settle()
    assert fc._proxy.rowCount() >= 4, fc._proxy.rowCount()

    # 既定はリスト表示
    assert fc.view_mode() == "list"
    assert fc._view.viewMode() == QListView.ListMode

    # ▦ をクリック → サムネイル表示へ
    btn = _view_btn()
    QTest.mouseClick(btn, Qt.LeftButton)
    _settle(400)
    assert fc.view_mode() == "thumb", "▦ でサムネイル表示に切り替わらない"
    assert fc._view.viewMode() == QListView.IconMode
    dele = fc._view.itemDelegate()
    assert isinstance(dele, ThumbnailDelegate), \
        ("サムネイル用デリゲートが入っていない", type(dele).__name__)
    print("the flat column switches to thumbnails: OK")

    # デリゲートが «平坦カラムのパス» を取り出せること
    # （filePath() を持たないモデルなので、ここが欠けると絵が一切出ない）
    idx = fc._proxy.index(0, 0)
    got = dele._path_of_index(idx)
    assert got and os.path.isfile(got), ("パスを取り出せていない", got)
    assert got in fc.all_paths()
    print("the delegate resolves paths from the flat model: OK")

    # 記憶されること（設定へ書かれる）
    assert sm.get("flat_view_mode", "") == "thumb", sm.get("flat_view_mode", "")

    # サイズはモード別に持ち、現在のモードへ反映される
    fc.set_item_size(128, "thumb")
    _settle(150)
    assert fc.item_size("thumb") == 128
    assert fc._view.iconSize().width() == 128
    assert fc.item_size("list") != 128, "リストの寸法まで変えている"

    # 中身を入れ替えてもサムネイル表示のまま
    fc.set_sources([os.path.join(d, "sub")])
    _settle(300)
    assert fc.view_mode() == "thumb"
    assert fc._view.viewMode() == QListView.IconMode

    # もう一度クリックでリストへ戻る
    QTest.mouseClick(btn, Qt.LeftButton)
    _settle(400)
    assert fc.view_mode() == "list", "リスト表示へ戻らない"
    assert fc._view.viewMode() == QListView.ListMode
    assert not isinstance(fc._view.itemDelegate(), ThumbnailDelegate)
    assert sm.get("flat_view_mode", "") == "list"
    print("clicking again returns to list and is remembered: OK")
    finish(True)


run(s1, delay=800)
