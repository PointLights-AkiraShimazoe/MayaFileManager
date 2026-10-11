# -*- coding: utf-8 -*-
"""r141: DCC へ送ったら、その送り先のウィンドウを最前面に出す。

ユーザー指示 2026-10-08:
  「MayaやBlenderへ送る関連のツールは、実行時に送った先のウィンドウを
    最前面に出してください。」

送信の経路は複数ある（クリック動作 / 右クリック / D&D / 保存・書き出し /
スクリプト実行）が、**実際に DCC へ渡るのは _bridge_send_async だけ**なので
そこへ置く。経路が増えても取りこぼさない。

固定すること:
  1) 送信のたびに «送り先の» DCC を最前面にしようとする
  2) Maya と Blender を取り違えない（bridge で判別する）
  3) 設定で切れる（既定は入）
  4) つまずき（PID 未取得・ウィンドウ無し）では黙る。本体の操作は成功して
     いるので、ついでの最前面化で騒がない／再スキャンも走らせない
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest

from ui.main_window import MainWindow

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def s1():
    sm.set(MainWindow.RAISE_ON_SEND_KEY, True, save=False)
    w = MainWindow(settings_manager=sm)
    w.resize(1100, 700)
    w.show()
    QTest.qWait(200)
    app.processEvents()

    calls = []
    w._focus_pid_window = lambda pid, name, rescan, quiet=False: \
        calls.append((pid, name, quiet))

    w._conn_pids = {20261: 4242}
    w._bl_conn_pids = {20270: 7373}
    w._bridge.set_port(20261)
    w._bl_bridge.set_port(20270)

    # ── 1) Maya へ送ったら Maya を最前面に ───────────────────────────
    w._raise_sent_dcc(w._bridge)
    check(calls == [(4242, "Maya", True)],
          "Maya へ送ったら Maya の PID を静かに最前面へ（%s）" % (calls,))

    # ── 2) Blender を取り違えない ──────────────────────────────────
    calls[:] = []
    w._raise_sent_dcc(w._bl_bridge)
    check(calls == [(7373, "Blender", True)],
          "Blender へ送ったら Blender の PID（%s）" % (calls,))

    # ── 3) 設定で切れる ───────────────────────────────────────────
    calls[:] = []
    sm.set(MainWindow.RAISE_ON_SEND_KEY, False, save=False)
    w._raise_sent_dcc(w._bridge)
    check(calls == [], "切っていれば何もしない（%s）" % (calls,))
    sm.set(MainWindow.RAISE_ON_SEND_KEY, True, save=False)

    # ── 4) PID が分からなくても静かに諦める ─────────────────────────
    calls[:] = []
    w._conn_pids = {}
    w._raise_sent_dcc(w._bridge)
    check(calls == [(None, "Maya", True)],
          "PID 不明でも quiet で呼ぶ（騒がない）（%s）" % (calls,))

    # 本物の _focus_pid_window が quiet で «黙る» こと
    del w._focus_pid_window
    msgs = []
    w.statusBar().showMessage = lambda *a, **k: msgs.append(a[0] if a else "")
    rescans = []
    w._focus_pid_window(None, "Maya", lambda: rescans.append(1), quiet=True)
    check(not msgs and not rescans,
          "quiet なら何も言わず再スキャンもしない（%s / %s）" % (msgs, rescans))
    # quiet でなければ従来どおり知らせる
    w._focus_pid_window(None, "Maya", lambda: rescans.append(1))
    check(msgs and rescans, "手動（⟳）なら従来どおり知らせて再スキャンする")

    # ── 5) 送信の choke point に繋がっていること ──────────────────────
    import inspect
    src = inspect.getsource(MainWindow._bridge_send_async)
    check("_raise_sent_dcc" in src,
          "実際に送る所（_bridge_send_async）から呼ばれている")

    finish(not fails)


run(s1, delay=900)
