# -*- coding: utf-8 -*-
"""読み込み中スピナーの判定、リサイズハンドル、レイアウト自己修復。"""
import os, time
from _common import *  # noqa: F401,F403
from _common import app, make_panel, columns, tmpdir, finish, run, QTimer, Qt, QPoint, _safe_file_path
from _common import QTest   # Maya の PySide6 には QtTest が無いためシム経由
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import QPointF, QEvent
from core.compat import QModelIndex

b = make_panel(1600, 700)
d = tmpdir(); p = d
for i in range(3):
    p = os.path.join(p, f"lv{i}"); os.makedirs(p)
b.navigate_to(p)


def drag(h, dx):
    QTest.mousePress(h, Qt.LeftButton, Qt.NoModifier, QPoint(6, 30))
    g = h.mapToGlobal(QPoint(6, 30))
    app.sendEvent(h, QMouseEvent(QEvent.MouseMove, QPointF(6 + dx, 30), QPointF(g.x() + dx, g.y()),
                                 Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    QTest.mouseRelease(h, Qt.LeftButton, Qt.NoModifier, QPoint(6 + dx, 30))
    app.processEvents()


def s1():
    view = b._column_view
    cv = columns(b)[0]
    m = cv.model(); idx = cv.rootIndex()
    while hasattr(m, "mapToSource"):
        idx = m.mapToSource(idx); m = m.sourceModel()
    key = os.path.normcase(os.path.normpath(_safe_file_path(m, idx)))
    # 読み込み済み優先で消灯
    view._loaded_dirs.add(key); m.canFetchMore = lambda i: True
    view._update_loading_overlays(); assert not cv._mfm_loading.isVisible()
    # 未記録 → 表示、60秒で消灯
    view._loaded_dirs.discard(key); view._loading_first_seen.pop(key, None)
    view._update_loading_overlays(); assert cv._mfm_loading.isVisible()
    view._loading_first_seen[key] = time.monotonic() - 61
    view._update_loading_overlays(); assert not cv._mfm_loading.isVisible()
    # 無効indexでは fetchMore しない（全ドライブ列挙の誘発防止）
    called = []; m.fetchMore = lambda i: called.append(i)
    proxy = cv.model(); orig = proxy.mapToSource; proxy.mapToSource = lambda i: QModelIndex()
    view._update_loading_overlays(); proxy.mapToSource = orig
    assert not called
    view.note_dir_loaded(_safe_file_path(m, idx))
    print("spinner logic: OK")
    # リサイズ: 右カラムが追従、左に空白なし
    cols = columns(b); i = len(cols) - 3
    left, right = cols[i], cols[i + 1]
    w0, rw0 = left.width(), right.width()
    drag(left._mfm_resize_handle, -80)

    def after():
        cols2 = columns(b); left2, right2 = cols2[i], cols2[i + 1]
        assert left2.width() == w0 - 80
        assert right2.x() == left2.x() + left2.width() and right2.width() == rw0
        assert min(v.x() for v in cols2) <= 0
        print("resize: right follows, no gap: OK")
        for v in cols2:
            v.move(v.x() + 900, v.y())

        def after2():
            cs = columns(b)
            assert min(v.x() for v in cs) <= 2, "self-heal failed"
            xs = sorted((v.x(), v.width()) for v in cs)
            for (x1, w1), (x2, _w) in zip(xs, xs[1:]):
                assert x2 == x1 + w1, xs
            print("layout self-heal: OK")
            finish()
        QTimer.singleShot(700, after2)
    QTimer.singleShot(400, after)


run(s1, delay=4000)
