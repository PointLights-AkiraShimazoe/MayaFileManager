# -*- coding: utf-8 -*-
"""r138: 起動した Maya / Blender へ接続を向ける（待たない）。

ユーザー指示 2026-10-08:
  「mayaとBlenderを起動した際には、最新で開いたものへコネクトを切り替えて
    ください。この挙動の実装により、managerが拘束されるようであれば、
    不採用になります。」

**拘束しないことが条件**なので待たない。起動時に «このポートに来るはず» と
いう予約だけ置き、検出は従来どおりワーカースレッドのスキャンに任せる。
UI はその結果（conn_list シグナル）を受けるだけ。

従来の不具合: 起動直後のスキャンでは新しい Maya のポートはまだ開いていない。
_on_conn_list は «現在の接続先が一覧に無い» と見て、**別の Maya へ勝手に
乗り換えて** bridge.set_port までしていた（＝起動した方へ送れない）。
"""
import time
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest

from ui.main_window import MainWindow

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _settle(ms=120):
    QTest.qWait(ms)
    app.processEvents()


def _items(ports):
    """(port, label, identified, pid, dcc) の一覧を作る。"""
    return [(p, "Maya 2027 — scene (:%d)" % p, True, 1000 + p, "maya")
            for p in ports]


def s1():
    w = MainWindow(settings_manager=sm)
    w.resize(1100, 700)
    w.show()
    # r146: 起動 1 秒後の本物のポートスキャン（別スレッド）が、このテストの
    # 途中で «空の一覧» を返してコンボを組み替えることがあり、手順 5 で
    # 20261 の行が見つからず落ちていた（Linux で間欠）。走行中の印を
    # 立てておけばスキャンは始まらない（本体の重複防止ガードを使う）。
    w._scan_running["maya"] = True
    _settle(250)
    combo = w._conn_combo

    # ── 前提: 既に別の Maya が 1 つ繋がっている ──────────────────────
    w._bridge.set_port(20261)
    w._on_conn_list(_items([20261]), "maya")
    _settle()
    check(w._bridge.port == 20261, "前提: 20261 に繋がっている（%r）" % w._bridge.port)

    # ── 1) 起動 → 予約が立ち、bridge は新ポートを向く ────────────────
    NEW = 20299
    w._bridge.set_port(NEW)                 # _launch_maya がやるのと同じ
    w._claim_launched_port("maya", NEW)
    check(w._launch_claim("maya") == NEW,
          "起動したポートの予約が立つ（%r）" % w._launch_claim("maya"))
    t = w._launch_watch_timer
    check(t is not None and t.isActive(), "様子見のタイマーが回り始める")
    check(t.interval() >= 2000,
          "様子見の間隔が短すぎない（%dms）" % t.interval())

    # ── 2) **まだ来ていない間に、別の Maya へ乗り換えない** ───────────
    w._on_conn_list(_items([20261]), "maya")   # 新ポートはまだ出てこない
    _settle()
    check(w._bridge.port == NEW,
          "起動待ちの間に別の Maya へ乗り換えない（%r）" % w._bridge.port)
    check(combo.itemData(combo.currentIndex()) == NEW,
          "コンボも起動中のポートを指したまま（%r）"
          % combo.itemData(combo.currentIndex()))
    check("20299" in combo.currentText() or "起動中" in combo.currentText()
          or "starting" in combo.currentText(),
          "«起動中…» と分かる表示（%r）" % combo.currentText())

    # 1 つも見つからない時も同じ（«なし» と言い切らない）
    w._on_conn_list([], "maya")
    _settle()
    check(w._bridge.port == NEW, "1 つも見つからなくても乗り換えない")
    check(combo.isEnabled(), "起動待ちの間はコンボを殺さない")

    # ── 3) 来たら、そこへ切り替えて予約を終える ──────────────────────
    w._on_conn_list(_items([20261, NEW]), "maya")
    _settle()
    check(w._bridge.port == NEW, "起動した方へ繋がる（%r）" % w._bridge.port)
    check(combo.itemData(combo.currentIndex()) == NEW, "コンボも起動した方")
    check(w._launch_claim("maya") is None, "予約は片付く")
    check(not w._launch_watch_timer.isActive(), "様子見のタイマーも止まる")

    # ── 4) 予約が無ければ従来どおり（一覧に無ければ乗り換える）────────
    w._on_conn_list(_items([20261]), "maya")
    _settle()
    check(w._bridge.port == 20261,
          "予約が無い時は従来どおり生きている方へ繋ぐ（%r）" % w._bridge.port)

    # ── 5) 手で選び直したら、予約よりユーザーの意思が勝つ ─────────────
    w._bridge.set_port(20299)
    w._claim_launched_port("maya", 20299)
    w._on_conn_list(_items([20261, 20299]), "maya")
    _settle()
    w._claim_launched_port("maya", 20299)      # もう一度予約を立てる
    for i in range(combo.count()):
        if combo.itemData(i) == 20261:
            combo.setCurrentIndex(i)
            break
    _settle()
    check(w._launch_claim("maya") is None,
          "手で選び直したら予約を捨てる（%r）" % w._launch_claim("maya"))
    check(w._bridge.port == 20261, "選んだ方へ繋がる（%r）" % w._bridge.port)

    # ── 6) 予約は期限切れで消える（起動に失敗しても詰まらない）────────
    w._claim_launched_port("maya", 20299)
    w._launch_claims["maya"]["until"] = time.monotonic() - 1
    check(w._launch_claim("maya") is None, "期限が来たら予約は消える")
    w._watch_launch_claims()
    check(not w._launch_watch_timer.isActive(),
          "予約が無くなれば様子見も止まる（回りっぱなしにしない）")

    finish(not fails)


run(s1, delay=900)
