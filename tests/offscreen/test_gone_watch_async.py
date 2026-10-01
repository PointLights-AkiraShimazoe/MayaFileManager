# -*- coding: utf-8 -*-
"""r107: 現在地の消失監視で **UI スレッドを止めない**。

ネットワーク/Perforce のワークスペースでは、同期中の stat が10秒以上
返らないことがある。r98 の監視タイマーは UI スレッドで os.path.isdir を
呼んでいたため、そのまま «最新取得中にフリーズ» になっていた
（mfm_freeze.log: _fallback_to_existing_ancestor 内で停止）。
"""
import os
import time
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, run, QTest

b = make_panel(1000, 600)
root = tmpdir()
deep = os.path.join(root, "proj", "scenes")
os.makedirs(deep)
open(os.path.join(deep, "a.ma"), "w").close()
b.navigate_to(deep)


def _settle(ms=350):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    import ui.browser_panel as bp

    # 監視は «UI スレッドで stat しない» 経路に繋がっている
    assert b._gone_watch.isActive(), "監視タイマーが止まっている"

    # stat が 2 秒返らない状況を作り、UI スレッドが止まらないことを見る
    real_isdir = os.path.isdir
    calls = {"n": 0, "thread": None}
    import threading
    main_thread = threading.current_thread()

    def slow_isdir(p):
        if p and os.path.normcase(str(p)) == os.path.normcase(deep):
            calls["n"] += 1
            calls["thread"] = threading.current_thread()
            time.sleep(2.0)
            return real_isdir(p)
        return real_isdir(p)

    os.path.isdir = slow_isdir
    try:
        t0 = time.monotonic()
        b._check_current_gone_async()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5, ("UI スレッドが stat で止まっている", elapsed)
        _settle(2600)                      # ワーカーの完了を待つ
        assert calls["n"] >= 1, "判定そのものが走っていない"
        assert calls["thread"] is not main_thread, \
            "UI スレッドで stat している"
        print("gone-watch runs off the UI thread: OK (%.3fs)" % elapsed)
    finally:
        os.path.isdir = real_isdir

    # 実際に消えた時はちゃんと «一番近い親» へ移動する
    import shutil
    shutil.rmtree(deep)
    b._gone_probe_busy = False
    b._check_current_gone_async()
    _settle(1200)
    cur = (b.current_path() or "").replace("\\", "/").rstrip("/")
    assert cur == os.path.join(root, "proj").replace("\\", "/"), \
        ("一番近い親へ移動していない", cur)
    print("still falls back to the nearest ancestor: OK")

    # 判定中に別の場所へ移っていたら、結果は捨てる
    b.navigate_to(root)
    _settle(500)
    b._on_gone_result("/nowhere/else", os.path.join(root, "proj"))
    _settle(300)
    assert (b.current_path() or "").rstrip("/\\") == root.rstrip("/\\"), \
        "古い判定結果で勝手に移動した"
    print("stale results are ignored: OK")
    finish(True)


run(s1, delay=900)
