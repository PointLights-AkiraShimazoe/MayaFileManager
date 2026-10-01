# -*- coding: utf-8 -*-
"""r72: 平坦ビュー／共通子フォルダカラムにフォーカスがある時、キーボード操作
（Delete / Ctrl+C/X/V / F2）の対象は «最下層の選択物» だけ（元になった上位
フォルダは対象にしない）。あわせて平坦ビューがファイル操作後に作り直されること。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, make_panel, tmpdir, finish, run, QTimer, Qt
from core.compat import QtCore as _QC
QItemSelectionModel = _QC.QItemSelectionModel

sm.set("flat_recursive", True, save=False)
b = make_panel(1600, 700)
d = tmpdir()
A, B = os.path.join(d, "A"), os.path.join(d, "B")
for base in (A, B):
    os.makedirs(os.path.join(base, "Edit"))
    open(os.path.join(base, "Edit", "f_%s.ma" % os.path.basename(base)), "w").close()
b.navigate_to(d)


def _flat_index_of(name):
    m = b._flat_col._proxy
    for r in range(m.rowCount()):
        if m.index(r, 0).data() == name:
            return m.index(r, 0)
    return None


def s1():
    # 本体で A, B を複数選択した状態を再現（平坦ビューの元）
    b._column_view._selected_dir_paths = {A, B}
    b._on_flat_request([A, B])
    QTimer.singleShot(800, s2)


def s2():
    fc = b._flat_col
    assert fc.isVisible() and fc._proxy.rowCount() == 2, fc._proxy.rowCount()
    # 平坦ビューにフォーカス＋1件選択 → 対象はそのファイルだけ
    idx = _flat_index_of("f_A.ma"); assert idx is not None
    fc._view.selectionModel().select(idx, QItemSelectionModel.ClearAndSelect)
    fc._view.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    assert app.focusWidget() is fc._view, app.focusWidget()
    t = b._operation_targets()
    assert [os.path.basename(p) for p in t] == ["f_A.ma"], t
    assert all(os.path.basename(p.rstrip("/\\")) not in ("A", "B") for p in t), "上位フォルダが対象になった"
    # 貼り付け先はファイル選択なので現在地（A/B ではない）
    pd = b._paste_target_dir()
    assert pd and os.path.basename(pd.rstrip("/\\")) not in ("A", "B"), pd
    print("flat view focus → targets = selected file only: OK")
    # 共通子フォルダカラムにフォーカス → 選択項目の実フォルダ群
    if b._common_cols:
        col = b._common_cols[0]
        ci = col._model.index(0, 0)
        col._view.selectionModel().select(ci, QItemSelectionModel.ClearAndSelect)
        col._view.setFocus(Qt.OtherFocusReason)
        app.processEvents()
        t2 = b._operation_targets()
        assert t2 and all(os.path.basename(p.rstrip("/\\")) == "Edit" for p in t2), t2
        print("common column focus → targets = real subfolders: OK")
    # フォーカスが本体に戻れば従来どおり（上位フォルダ）
    b._column_view.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    assert b._deep_view_targets() is None
    # r74: 平坦ビューの複数選択 D&D — 選択済み項目のプレスで選択が崩れず、
    # 閾値を超えて動かすと選択全体（2件）でドラッグが始まる
    from _common import QTest
    from core.compat import QtGui, QPoint
    fc._view.selectAll(); app.processEvents()
    assert len(fc.selected_paths()) == 2
    dragged = []
    fc._view._start_drag = lambda: dragged.append(fc._view._selected_paths())
    i0 = fc._proxy.index(0, 0)
    r = fc._view.visualRect(i0)
    QTest.mousePress(fc._view.viewport(), Qt.LeftButton, Qt.NoModifier, r.center())
    assert len(fc.selected_paths()) == 2, "プレスで複数選択が崩れた"
    far = r.center() + QPoint(QApplication.startDragDistance() + 8, 0)
    mv = QtGui.QMouseEvent(_QC.QEvent.MouseMove, _QC.QPointF(far),
                           Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(fc._view.viewport(), mv)
    assert dragged and len(dragged[0]) == 2, dragged
    QTest.mouseRelease(fc._view.viewport(), Qt.LeftButton, Qt.NoModifier, far)
    # 動かさずに離すと単一選択に確定
    QTest.mousePress(fc._view.viewport(), Qt.LeftButton, Qt.NoModifier, r.center())
    QTest.mouseRelease(fc._view.viewport(), Qt.LeftButton, Qt.NoModifier, r.center())
    assert len(fc.selected_paths()) == 1, fc.selected_paths()
    print("flat view multi-select drag start / click-to-single: OK")
    fc._view.selectionModel().select(_flat_index_of("f_A.ma"), QItemSelectionModel.ClearAndSelect)
    # ファイル操作後の作り直し: ファイルを消して _refresh_flat_view → 1件に
    os.remove(os.path.join(A, "Edit", "f_A.ma"))
    b._refresh_flat_view()
    app.processEvents()
    assert fc._proxy.rowCount() == 1, fc._proxy.rowCount()
    print("flat view rebuilt after file op: OK")
    finish()


run(s1, delay=2500)
