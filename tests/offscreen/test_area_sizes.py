# -*- coding: utf-8 -*-
"""r119g: エリアの «縦の高さ» が再起動後も保たれること。

ユーザー報告 2026-10-02:
  「各エリアの縦の高さが保存されていないようです。再起動で均等にされます。」

原因: _save_areas_state() は各エリアの get_state()（パス・エリア内の分割幅）
だけを保存しており、**エリア間の QSplitter が持つ高さ** を保存していなかった。
高さは splitter 側の値なので、別に保存・復元する必要がある。

保存は «割合» で持つ。ピクセルのまま持つと、ウィンドウの大きさが変わった
時に比率が狂う。
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest

from ui.main_window import MainWindow


def _ratios(w):
    sizes = [int(x) for x in w._areas_split.sizes()]
    total = float(sum(sizes)) or 1.0
    return [round(x / total, 3) for x in sizes]


def s1():
    sm.set("browser_areas_state", None, save=False)
    sm.set(MainWindow.AREA_SIZES_KEY, None, save=False)

    w = MainWindow(settings_manager=sm)
    w.resize(1100, 1400)
    w.show()
    QTest.qWait(200)
    app.processEvents()

    # エリアを 3 つにする
    w._on_area_add_below(w._areas[0])
    w._on_area_add_below(w._areas[1])
    app.processEvents()
    assert len(w._areas) == 3, len(w._areas)

    # 高さを «偏らせる»
    h = w._areas_split.height()
    assert h > 60, h
    want = [int(h * 0.55), int(h * 0.28), h - int(h * 0.55) - int(h * 0.28)]
    w._areas_split.setSizes(want)
    app.processEvents()
    before = _ratios(w)
    # エリアには最小高さがあるので «要求どおり» にはならないことがある。
    # 大事なのは «均等ではない» ことと «それが再現される» こと。
    assert max(before) - min(before) > 0.05, ("偏らせられていない", before)
    w._save_areas_state()
    saved = sm.get(MainWindow.AREA_SIZES_KEY)
    assert isinstance(saved, list) and len(saved) == 3, saved
    assert abs(sum(saved) - 1.0) < 0.01, saved
    assert all(isinstance(x, float) for x in saved), "割合（比率）で保存していない"
    w.close()
    app.processEvents()

    # 再起動相当: 同じ設定から作り直す
    w2 = MainWindow(settings_manager=sm)
    w2.resize(1100, 1400)
    w2.show()
    QTest.qWait(300)
    app.processEvents()
    w2._restore_area_sizes()
    app.processEvents()
    assert len(w2._areas) == 3, len(w2._areas)
    after = _ratios(w2)
    for a, b in zip(before, after):
        assert abs(a - b) < 0.06, ("高さが戻っていない", before, after)
    assert max(after) - min(after) > 0.05, ("均等割りに戻っている", after)
    print("area heights survive a restart (kept as ratios): OK")

    # ウィンドウの高さが変わっても «割合» を当てはめ直せる。
    # ただしエリアには最小高さがあるので、縮めると割合どおりには収まらない
    # （物理的な制約であってバグではない）。保てるのは «大小の順» まで。
    w2.resize(1100, 1150)
    app.processEvents()
    w2._restore_area_sizes()
    app.processEvents()
    resized = _ratios(w2)
    import builtins
    rank = lambda v: sorted(range(len(v)), key=lambda i: v[i])
    assert rank(resized) == rank(after), ("大小の順が入れ替わった", after, resized)
    assert max(resized) - min(resized) > 0.03, ("均等割りに潰れた", resized)
    print("the stored ratio is re-applied when the window height changes: OK")

    # エリア数が変わったら «前の高さ» を当てはめない（ずれる方が困る）
    w2._on_area_remove(w2._areas[2])
    app.processEvents()
    sizes = [int(x) for x in w2._areas_split.sizes()]
    assert len(sizes) == 2, sizes
    saved2 = sm.get(MainWindow.AREA_SIZES_KEY)
    assert len(saved2) == 2, ("エリアを消したのに古い件数のまま", saved2)
    w2.close()
    app.processEvents()
    print("removing an area updates the stored heights: OK")
    finish(True)


run(s1, delay=300)
