# -*- coding: utf-8 -*-
"""子フォルダカラム（緑バー）の深さスイッチ: 全階層 ⇔ 選択フォルダ直下のみ。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, make_panel, tmpdir, finish, run, QTimer
from core.compat import QtCore as _QC
QItemSelectionModel = _QC.QItemSelectionModel

sm.set("flat_recursive", True, save=False)
b = make_panel(1600, 700)
d = tmpdir()
A, B = os.path.join(d, "A"), os.path.join(d, "B")
for base in (A, B):
    os.makedirs(os.path.join(base, "Edit", "deep"))
    open(os.path.join(base, "Edit", "direct_%s.ma" % os.path.basename(base)), "w").close()
    open(os.path.join(base, "Edit", "deep", "deep_%s.ma" % os.path.basename(base)), "w").close()
b.navigate_to(d)


def flat_names():
    m = b._flat_col._proxy
    return sorted(m.index(r, 0).data() for r in range(m.rowCount()))


def s1():
    b._on_flat_request([A, B])
    QTimer.singleShot(800, s2)


def s2():
    assert b._common_cols, "common column not built"
    col = b._common_cols[0]
    assert col.is_recursive() and "全階層" in col._depth_btn.toolTip()
    assert not col._depth_btn.icon().isNull(), "depth icon not loaded"
    # 「Edit」を選択
    idx = col._model.index(0, 0)
    assert "Edit" in idx.data(), idx.data()
    col._view.selectionModel().select(idx, QItemSelectionModel.ClearAndSelect)
    QTimer.singleShot(800, s3)


def s3():
    names = flat_names()
    assert set(names) == {"direct_A.ma", "direct_B.ma", "deep_A.ma", "deep_B.ma"}, names
    print("recursive (default): all levels: OK")
    b._common_cols[0]._depth_btn.setChecked(False)   # 直下のみ
    QTimer.singleShot(500, s4)


def s4():
    names = flat_names()
    assert set(names) == {"direct_A.ma", "direct_B.ma"}, names
    assert sm.get("flat_recursive") is False
    assert "直下" in b._common_cols[0]._depth_btn.toolTip()
    print("direct-only mode: OK")
    b._common_cols[0]._depth_btn.setChecked(True)
    QTimer.singleShot(500, s5)


def s5():
    assert len(flat_names()) == 4
    print("back to recursive: OK")
    finish()


run(s1, delay=3000)
