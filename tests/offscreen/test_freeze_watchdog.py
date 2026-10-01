# -*- coding: utf-8 -*-
"""r108: «意図して UI スレッドを握っている» 区間はフリーズ記録の対象外。

Windows の D&D（OLE DoDragDrop）はドラッグ中ずっとスレッドを握るため、
フリーズ監視がそれを «停止» として大量に記録し、本物のフリーズが
mfm_freeze.log から埋もれていた（現行ビルドの記録の約半分）。
"""
import os
from _common import *  # noqa: F401,F403
from _common import (app, make_panel, find_item_wait as find_item,
                     tmpdir, finish, run, Qt, QTest)
import ui.browser_panel as bp

b = make_panel(1000, 600)
d = tmpdir()
for n in ("a.ma", "b.ma"):
    open(os.path.join(d, n), "w").close()
b.navigate_to(d)


def _settle(ms=300):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    # 既定では «占有中» ではない
    assert bp.mfm_blocking_reason() == "", "初期状態で占有扱いになっている"

    # 宣言すると理由が取れ、入れ子でも正しく戻る
    bp.mfm_blocking_begin("テスト占有")
    assert bp.mfm_blocking_reason() == "テスト占有"
    bp.mfm_blocking_begin()
    assert bp.mfm_blocking_reason() == "テスト占有", "入れ子で理由が消えた"
    bp.mfm_blocking_end()
    assert bp.mfm_blocking_reason() == "テスト占有", "入れ子の途中で解除された"
    bp.mfm_blocking_end()
    assert bp.mfm_blocking_reason() == "", "解除されていない"
    bp.mfm_blocking_end()                 # 余分な解除で壊れない
    assert bp.mfm_blocking_reason() == ""
    print("blocking section bookkeeping: OK")

    # 実際のドラッグ中に «占有中» になっている
    seen = {}
    orig = bp.QDrag

    class _Drag:
        def __init__(self, *a, **k):
            pass

        def setMimeData(self, m):
            pass

        def setPixmap(self, *a, **k):
            pass

        def setHotSpot(self, *a, **k):
            pass

        def exec(self, *a, **k):
            seen["reason"] = bp.mfm_blocking_reason()
            return 0
        exec_ = exec

    bp.QDrag = _Drag
    try:
        cvw, rect, idx = find_item(b, "a.ma")
        assert cvw, "項目が見つからない"
        b._column_view._start_multi_drag(cvw, [idx])
        _settle(200)
    finally:
        bp.QDrag = orig
    assert seen.get("reason"), ("ドラッグ中に占有宣言が立っていない", seen)
    assert bp.mfm_blocking_reason() == "", "ドラッグ後に解除されていない"
    print("drag declares a blocking section: OK (%s)" % seen["reason"])
    finish(True)


run(s1, delay=900)
