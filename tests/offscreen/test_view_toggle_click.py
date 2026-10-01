# -*- coding: utf-8 -*-
"""r100: ▦ ボタンの «実際のクリック» でリスト⇄サムネイルが切り替わる。

r85 のサイズスライダーは Qt.Popup で出していたため、ボタンにマウスオーバー
した瞬間にポップアップがマウスを grab し、続くクリックが «ポップアップを
閉じる» だけで消費されて切り替えが効かなくなっていた。
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, Qt, QTest
from core.compat import QListView, QToolButton
import ui.browser_panel as bp

b = make_panel(1100, 650)
d = tmpdir()
for n in ("alpha.ma", "bravo.fbx", "delta.png"):
    open(os.path.join(d, n), "w").close()
b.navigate_to(d)


def _settle(ms=350):
    QTest.qWait(ms)
    app.processEvents()


def _view_btn(view):
    hdr = getattr(view, "_mfm_header", None)
    assert hdr is not None, "カラムヘッダが無い"
    for btn in hdr.findChildren(QToolButton):
        if btn.text() == "▦":
            return btn
    raise AssertionError("▦ ボタンが見つからない")


def s1():
    cvw, rect, _ = find_item(b, "alpha.ma")
    assert cvw, "項目が見つからない"
    btn = _view_btn(cvw)
    assert cvw.viewMode() == QListView.ListMode

    # ポップアップは «マウスを grab しない» 種類であること
    popup = b._column_view.size_popup()
    # Qt.Tool は内部的に Popup|Dialog のビットを含むので «型» で見る
    assert popup.windowType() != Qt.Popup, \
        "サイズスライダーが Qt.Popup（マウスを grab してクリックを奪う）"
    # r103: 別ウィンドウではなく «カラムビューの子» であること
    #（トップレベルだと環境次第で表示されず «ツールチップしか出ない» になる）
    assert not popup.isWindow(), "サイズスライダーが別ウィンドウになっている"
    assert popup.parent() is b._column_view, "親がカラムビューでない"

    # ホバー（＝スライダーが出る）→ そのままクリック、を再現する
    QTest.mouseMove(btn, bp.QPoint(btn.width() // 2, btn.height() // 2))
    app.sendEvent(btn, bp._QtCore.QEvent(bp._QtCore.QEvent.Enter))
    _settle(200)
    # r102: ホバーで «全体の表示サイズ» スライダーが実際に出ること
    assert popup.isVisible(), "マウスオーバーでサイズスライダーが出ない"
    assert popup._slider.maximum() > popup._slider.minimum()
    # 親の中に収まっている（はみ出すと子ウィジェットはクリップされて見えない）
    g = popup.geometry()
    assert g.left() >= 0 and g.top() >= 0 \
        and g.right() <= b._column_view.width() \
        and g.bottom() <= b._column_view.height(), \
        ("スライダーが親からはみ出している", g, b._column_view.size())
    # スライダーを動かすと実際に表示サイズが変わる
    before = b._column_view.column_item_size(cvw)
    popup._slider.setValue(min(popup._slider.maximum(), before + 12))
    _settle(250)
    assert b._column_view.column_item_size(cvw) != before, \
        "スライダーを動かしても表示サイズが変わらない"
    QTest.mouseClick(btn, Qt.LeftButton)
    _settle(600)
    assert cvw.viewMode() == QListView.IconMode, \
        "ホバー後のクリックでサムネイル表示に切り替わらない"
    assert getattr(cvw, "_mfm_view_mode", "") == "thumb"
    assert isinstance(cvw.itemDelegate(), bp.ThumbnailDelegate), \
        ("サムネイル用デリゲートが入っていない", type(cvw.itemDelegate()).__name__)
    print("hover then click switches to thumbnails: OK")

    # もう一度クリックでリストへ戻る
    app.sendEvent(btn, bp._QtCore.QEvent(bp._QtCore.QEvent.Enter))
    _settle(200)
    QTest.mouseClick(btn, Qt.LeftButton)
    _settle(600)
    assert cvw.viewMode() == QListView.ListMode, "リスト表示へ戻らない"
    print("clicking again switches back to list: OK")
    finish(True)


run(s1, delay=900)
