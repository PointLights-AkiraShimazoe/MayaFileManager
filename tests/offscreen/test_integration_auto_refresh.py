# -*- coding: utf-8 -*-
"""r136: 連携状態（Git/SVN/Perforce/クラウド）を手動更新なしでも新しくする。

ユーザー質問 2026-10-08:
  「Perforceの連携状態を更新ボタンを押さずとも、更新がされる様にはできますか？」

従来は «表示時に 1 回、あとは手動更新» だった。p4 fstat はサーバ往復なので
無制限には回さない。有効期限（既定 60 秒）を付け、

  ・開いているカラムのフォルダだけ
  ・期限切れのものだけ
  ・窓が見えている時だけ

取り直す。窓が前面に戻った時は «他アプリで p4 を触って戻ってきた» 典型なので
短い期限（5 秒）で見る。設定 0 で従来どおりへ戻せる。
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


def _settle(ms=150):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    # ── 1) 期限切れの判定（マネージャ単体。ワーカーは起こさない）────────
    m = IntegrationManager()
    m.enabled = True
    d = tmpdir()
    key_dir = d

    check(m.ttl == 0.0, "既定は従来どおり期限なし（%r）" % m.ttl)
    check(m.is_stale(key_dir), "未取得なら常に «古い»")

    # 取得済みに見せかける
    m._dir_ts[norm(key_dir)] = time.monotonic()
    check(not m.is_stale(key_dir), "期限なしなら取得済みは «古くない»")

    m.set_ttl(60)
    check(m.ttl == 60.0, "期限を秒で設定できる（%r）" % m.ttl)
    check(not m.is_stale(key_dir), "60 秒以内は取り直さない")
    # «窓が前面に戻った時» は短い期限で見る。実時間に頼ると
    # 実行速度で結果が変わるので、取得時刻をずらして見る。
    m._dir_ts[norm(key_dir)] = time.monotonic() - 10
    check(not m.is_stale(key_dir), "10 秒前の取得は 60 秒期限ならまだ生きている")
    check(m.is_stale(key_dir, max_age=5.0),
          "短い期限（窓が前面に戻った時）では «古い» と見る")

    # 期限切れにすると要求がキューに入る
    m._dir_ts[norm(key_dir)] = time.monotonic() - 999
    check(m.is_stale(key_dir), "期限を過ぎたら «古い»")
    before = m._queue.qsize()
    m.request_status(key_dir)
    check(m._queue.qsize() == before + 1,
          "期限切れなら取り直しが積まれる（%d → %d）" % (before, m._queue.qsize()))
    m._pending.clear()

    # 取りたてなら積まれない
    m._dir_ts[norm(key_dir)] = time.monotonic()
    before = m._queue.qsize()
    m.request_status(key_dir)
    check(m._queue.qsize() == before,
          "取りたてなら何もしない（%d → %d）" % (before, m._queue.qsize()))

    # force は期限を無視する（手動更新の経路）
    before = m._queue.qsize()
    m.request_status(key_dir, force=True)
    check(m._queue.qsize() == before + 1, "force は期限を無視する")
    m._pending.clear()

    # ── 2) パネル側: 設定で入/切でき、対象は開いているカラム ────────────
    sm.set(BrowserPanel.INTEG_AUTO_KEY, BrowserPanel.INTEG_AUTO_DEFAULT,
           save=False)
    root = tmpdir()
    sub = os.path.join(root, "sub")
    os.makedirs(sub)
    open(os.path.join(sub, "a.ma"), "w").close()

    b = make_panel(1100, 650)
    b.navigate_to(sub)
    _settle(600)

    t = getattr(b, "_integ_auto_timer", None)
    check(t is not None, "自動更新のタイマーがある")
    check(t is not None and t.isActive(), "既定では動いている")

    # r136: BrowserPanel 自身が IntegrationManager を取れること。
    # （従来ここに定義が無く、平坦ビューの状態要求が AttributeError で
    #   握り潰されていた＝平坦ビューのバッジと右クリックが出ていなかった）
    mgr = None
    try:
        mgr = b._integrations()
    except Exception as e:
        check(False, "BrowserPanel から連携マネージャを取れない: %r" % (e,))
    check(mgr is not None, "BrowserPanel._integrations() が使える")

    # r136b: 対象は «今見ているフォルダ»。ウィジェットを歩く実装は
    # カラムの生成・破棄と重なって落ちた（実機で ACCESS_VIOLATION）。
    targets = b._auto_refresh_targets()
    check(len(targets) == 1 and os.path.normcase(os.path.abspath(targets[0]))
          == os.path.normcase(os.path.abspath(sub)),
          "今見ているフォルダだけが対象（%s）" % (targets,))

    # 切る → タイマーが止まり、期限も 0 に戻る
    sm.set(BrowserPanel.INTEG_AUTO_KEY, 0, save=False)
    b.apply_integration_auto_refresh()
    _settle()
    check(not b._integ_auto_timer.isActive(), "設定 0 でタイマーが止まる")
    check(b._integrations().ttl == 0.0,
          "設定 0 で従来どおり «表示時＋手動» へ戻る（%r）"
          % b._integrations().ttl)
    # 切っている間は何も要求しない
    b._tick_integration_auto_refresh()
    b.refresh_integrations_on_activate()
    print("auto refresh stays quiet while switched off: OK", flush=True)

    sm.set(BrowserPanel.INTEG_AUTO_KEY, BrowserPanel.INTEG_AUTO_DEFAULT,
           save=False)
    b.apply_integration_auto_refresh()
    _settle()
    check(b._integ_auto_timer.isActive(), "入れ直すと動き出す")

    finish(not fails)


run(s1, delay=900)
