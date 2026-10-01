# -*- coding: utf-8 -*-
"""r85: 表示サイズのスライダー（▦ ボタンにマウスオーバーで出る）。
リスト／サムネイルのどちらも同じスライダーで調整でき、値は保存される。
併せて r85: サムネイルが無いときは «?» チップではなく通常アイコンになること。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, QTimer, Qt
from core.compat import QListView, QtCore
from core.thumbnail_generator import ThumbnailWorker

b = make_panel(1000, 600)
d = tmpdir()
for n in ("alpha.ma", "bravo.fbx", "charlie.txt", "delta.png"):
    open(os.path.join(d, n), "w").close()
b.navigate_to(d)
cvw = b._column_view
state = {}


def test_no_thumbnail_returns_empty_pixmap():
    """絵が作れないファイルは «空の QPixmap»（＝通常アイコンで描け）を返す。"""
    for n in ("alpha.ma", "bravo.fbx", "charlie.txt"):
        pm = ThumbnailWorker(os.path.join(d, n), 64)._generate(
            os.path.join(d, n), 64)
        assert pm.isNull(), (n, "種別チップを返している（? の出どころ）")
    assert not hasattr(ThumbnailWorker, "_icon_pixmap"), \
        "チップ生成が残っている"
    print("no thumbnail -> empty pixmap (delegate draws the normal icon): OK")


def s1():
    test_no_thumbnail_returns_empty_pixmap()
    cv, rect, _ = find_item(b, "alpha.ma"); assert cv
    state["cv"] = cv
    # 既定はリスト表示。スライダーの値はリスト用
    assert cv.viewMode() == QListView.ListMode
    base = cvw.column_item_size(cv)
    cvw.set_column_item_size(cv, 48)
    assert cv.iconSize().width() == 48, cv.iconSize()
    assert cvw.column_item_size(cv) == 48
    state["list_row_h"] = cv.sizeHintForRow(0)
    cvw.set_column_item_size(cv, base)
    assert cv.sizeHintForRow(0) < state["list_row_h"], "リストの行高がサイズに追従しない"
    print("list mode follows the slider: OK")
    # サムネイル表示へ
    cvw._set_column_view_mode(cv, "thumb")
    QTimer.singleShot(500, s2)


def s2():
    cv = state["cv"]
    assert cv.viewMode() == QListView.IconMode
    # モードごとに別の値（リスト16 / サムネ96 が既定）
    assert cvw.column_item_size(cv) >= 48, cvw.column_item_size(cv)
    g0 = cv.gridSize().width()
    cvw.set_column_item_size(cv, 160)
    assert cv.gridSize().width() > g0, (g0, cv.gridSize())
    assert cv.itemDelegate()._thumb_size == 160, "デリゲートに反映されていない"
    print("thumbnail mode follows the same slider: OK")
    # ポップアップ：ボタンのホバーで出て、値を動かすとビューに効く
    hdr = getattr(cv, "_mfm_header", None); assert hdr, "ヘッダが無い"
    btns = [w for w in hdr.findChildren(type(hdr.findChild(QtCore.QObject).__class__))] \
        if False else None
    from core.compat import QToolButton
    vb = [x for x in hdr.findChildren(QToolButton) if x.text() == "▦"]
    assert vb, "表示切替ボタンが見つからない"
    state["btn"] = vb[0]
    pop = cvw.size_popup()
    pop.show_for(cv, vb[0])
    assert pop.isVisible(), "ホバーで出るポップアップが表示されない"
    assert pop._slider.value() == 160, pop._slider.value()
    pop._slider.setValue(120)
    QTimer.singleShot(200, s3)


def s3():
    cv = state["cv"]
    pop = cvw.size_popup()
    assert cv.itemDelegate()._thumb_size == 120, "スライダー操作がビューに届かない"
    assert cvw.column_item_size(cv) == 120
    # 保存されること
    assert int(b._sm.get("column_icon_size_thumb", 0)) == 120, "設定に保存されない"
    pop.hide()
    # リストへ戻すとリスト用の値に戻る（相互に汚染しない）
    cvw._set_column_view_mode(cv, "list")
    QTimer.singleShot(400, s4)


def s4():
    cv = state["cv"]
    assert cv.viewMode() == QListView.ListMode
    assert cvw.column_item_size(cv) < 100, ("リストの値がサムネ側に汚染された",
                                            cvw.column_item_size(cv))
    print("per-mode sizes are kept separately and persisted: OK")
    finish(True)


run(s1)
