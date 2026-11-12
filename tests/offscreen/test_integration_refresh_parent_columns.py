# -*- coding: utf-8 -*-
"""v0.9.20: 連携状態の自動更新は «開いているカラム全部» を見る。

ユーザー報告 2026-11-12:
  「連携状態を更新を押さないと、ステータスが変わらないのですが、
    自動にならないですか？異常に重くなるとかであれば避けてください。」

実機のログで分かったこと: 自動更新の対象が «今見ているフォルダ»（一番右の
カラム）だけだったので、その左に並んでいる親のカラムは、開いた時の状態の
まま二度と更新されていなかった（起動時の 1 回きり、26 時間そのまま）。

直し方:
  ・対象を «現在地から上へ辿った親フォルダ» まで広げる（ウィジェットには
    触らない。r136b の落ち方を繰り返さない）
  ・ただし親は «連携先がある» と分かっているフォルダだけ（管理外のフォルダを
    毎分問い合わせない）
  ・取り直しは «初めて見るフォルダ» より後回し（新しく開いたフォルダの
    バッジが、取り直しの列の後ろで待たされない）
"""
import os
import time
from _common import *  # noqa: F401,F403
from _common import app, sm, make_panel, tmpdir, finish, run, QTest

from core.integrations.manager import IntegrationManager
from core.integrations.base import norm
from ui.browser_panel import BrowserPanel

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _same(a, b):
    return (os.path.normcase(os.path.abspath(a))
            == os.path.normcase(os.path.abspath(b)))


class _FakeProvider(object):
    key = "fake"
    label = "Fake"
    available = True


def s1():
    # ── 1) 対象: 現在地 ＋ 連携先のある親カラム（手前から）─────────────
    sm.set(BrowserPanel.INTEG_AUTO_KEY, BrowserPanel.INTEG_AUTO_DEFAULT,
           save=False)
    root = tmpdir()
    mid = os.path.join(root, "mid")
    deep = os.path.join(mid, "deep")
    os.makedirs(deep)
    open(os.path.join(mid, "in_parent.ma"), "w").close()
    open(os.path.join(deep, "in_current.ma"), "w").close()

    b = make_panel(1100, 650)
    b.navigate_to(deep)
    QTest.qWait(600)
    app.processEvents()
    mgr = b._integrations()

    # 何も管理されていないうちは、これまでどおり現在地だけ
    t0 = b._auto_refresh_targets()
    check(len(t0) == 1 and _same(t0[0], deep),
          "連携先が無い親は対象にしない（%d 件）" % len(t0))

    # 親 2 つを «連携先あり» にする（root の更に上は管理外のまま）
    fake = _FakeProvider()
    added = []
    with mgr._lock:
        for d in (mid, root):
            k = norm(d)
            added.append((k, mgr._dir_providers.get(k)))
            mgr._dir_providers[k] = [(fake, root, d)]
    try:
        t1 = b._auto_refresh_targets()
        check(len(t1) == 3, "現在地＋連携先のある親 2 つが対象（%d 件）" % len(t1))
        check(len(t1) == 3 and _same(t1[0], deep) and _same(t1[1], mid)
              and _same(t1[2], root),
              "順番は手前（深い方）から")
        above = os.path.dirname(root)
        check(not any(_same(x, above) for x in t1),
              "管理外の親（一時フォルダの上）は問い合わせない")

        # ── 2) 定期の見直しと «窓が前面に戻った時» の両方が親も見る ────
        calls = []
        orig = mgr.request_status

        def _rec(directory, force=False, max_age=None):
            calls.append((directory, force, max_age))
        mgr.request_status = _rec
        try:
            b.show()
            QTest.qWait(50)
            b._tick_integration_auto_refresh()
            got = [c[0] for c in calls]
            check(any(_same(x, mid) for x in got),
                  "定期の見直しが親カラムも取り直しに出す")
            check(all(c[1] is False for c in calls),
                  "定期の見直しは強制しない（期限切れだけ走る）")
            del calls[:]
            b.refresh_integrations_on_activate()
            got = [c[0] for c in calls]
            check(any(_same(x, mid) for x in got) and any(_same(x, deep) for x in got),
                  "窓が前面に戻った時も親カラムを見る")
            check(all(c[2] == 5.0 for c in calls),
                  "前面に戻った時は短い期限（5 秒）で見る＝連打しない")

            # コンパクトで畳んでいる間は、これまでどおり現在地だけ（見えていない
            # 親カラムのために毎分問い合わせない）。開いている間は親も見る。
            class _Ctl(object):
                def __init__(self, active, expanded):
                    self._a, self._e = active, expanded

                def is_active(self):
                    return self._a

                def is_expanded(self):
                    return self._e
            win = b.window()
            del calls[:]
            win._compact_ctl = _Ctl(True, False)        # 畳んでいる
            b._tick_integration_auto_refresh()
            check(len(calls) == 1 and _same(calls[0][0], deep),
                  "コンパクトで畳んでいる間の定期の見直しは現在地だけ（%d 件）" % len(calls))
            del calls[:]
            win._compact_ctl = _Ctl(True, True)         # コンパクトだが開いている
            b._tick_integration_auto_refresh()
            check(len(calls) == 3, "開いている間は親カラムも見る（%d 件）" % len(calls))
            del calls[:]
            win._compact_ctl = _Ctl(True, False)        # 畳んだまま前面に戻った時
            b.refresh_integrations_on_activate()
            check(len(calls) == 3, "前面に戻った時は畳んでいても全部を見直す（%d 件）" % len(calls))
            del win._compact_ctl

            # 設定で切ってあれば何もしない（従来どおり）
            del calls[:]
            sm.set(BrowserPanel.INTEG_AUTO_KEY, 0, save=False)
            b._tick_integration_auto_refresh()
            b.refresh_integrations_on_activate()
            check(not calls, "自動更新を切ってあれば親も現在地も問い合わせない")
            sm.set(BrowserPanel.INTEG_AUTO_KEY, BrowserPanel.INTEG_AUTO_DEFAULT,
                   save=False)
        finally:
            mgr.request_status = orig
    finally:
        with mgr._lock:
            for k, old in added:
                if old is None:
                    mgr._dir_providers.pop(k, None)
                else:
                    mgr._dir_providers[k] = old

    # ── 3) 取り直しは «初めて見るフォルダ» より後回し ─────────────────
    m = IntegrationManager()          # ワーカーは起こさない（キューだけ見る）
    m.enabled = True
    m.set_ttl(60)
    old_a = os.path.join(root, "old_a")
    old_b = os.path.join(root, "old_b")
    fresh = os.path.join(root, "fresh")
    for d in (old_a, old_b):
        m._dir_ts[norm(d)] = time.monotonic() - 999      # 取得済み・期限切れ
    m.request_status(old_a)
    m.request_status(old_b)
    m.request_status(fresh)                               # 初めて見るフォルダ
    check(m._queue.qsize() == 3, "3 本とも積まれる（%d）" % m._queue.qsize())
    order = []
    while not m._queue.empty():
        item = m._queue.get()
        order.append(item[-1])
    check(order and _same(order[0], fresh),
          "初めて見るフォルダが先に処理される（%s）"
          % [os.path.basename(x) for x in order])
    check(len(order) == 3 and _same(order[1], old_a) and _same(order[2], old_b),
          "取り直し同士は積んだ順のまま")

    # 手動更新（force）は後回しにしない
    m._pending.clear()
    m.request_status(old_a)                               # 取り直し
    m.request_status(old_b, force=True)                   # 手動
    first = m._queue.get()[-1]
    check(_same(first, old_b), "手動更新は取り直しより先")
    while not m._queue.empty():
        m._queue.get()

    # 検出は積んだ順（先に積んだ検出が、後の状態取得に抜かれない）
    m2 = IntegrationManager()
    m2._enqueue("detect", None)
    m2.enabled = True
    m2.request_status(fresh)
    k = m2._queue.get()
    check(k[-2] == "detect", "検出は先に積んだ順で処理される（%r）" % (k[-2],))

    b.close()
    finish(not fails)


run(s1, delay=900)
