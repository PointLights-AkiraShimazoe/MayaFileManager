# -*- coding: utf-8 -*-
"""r146: 読み込み中（accept が止まっている）の Maya を一覧から落とさない。

ユーザー報告 2026-10-20「複数起動のコネクトリスト追加のエラーは直ってません」
実機 mfm_maya.log 15:40: 1 台目（:20261）にシーンを開かせた直後に 2 台目を
起動すると、scan が [20261] → [] → [20262] と変わり、1 台目が一覧から消えた。

原因: Maya は本体が忙しいと commandPort の accept が止まる。見張りが
1.5 秒ごとに繋ぎに行って待ち行列（backlog）を埋め、スキャンの接続が
«時間切れ» になる。閉じたポートは即«拒否»で返るので、時間切れは
«相手はいるが詰まっている» 状態なのに、«閉じた» と見なして落としていた。
同じ理由で空きポート探しも時間切れのポートを «空き» と見て、起動した Maya が
隣のポートへ逃げる（10/19 の «起動中… のまま固まる» の遠因）。

固定すること:
  1) scan_ports は (open, busy) を返し、LISTEN しているのに繋がらない相手は busy
     （接続の時間切れでは決めない。この PC では閉じたポートも時間切れになる）
  2) busy のポートで前回識別できていた Maya は «…» 付きで一覧に残る
  3) 本当に閉じた（open にも busy にも無い）ポートだけ落ちる
  4) find_free_port は busy も使用中と見る
  5) 見張りは応答が無かった後しばらく繋ぎに行かない（backlog を埋めない）
"""
import socket
import threading
import time

from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest
import core.maya_bridge as _mb
import ui.main_window_dcc as _mwd
from ui.main_window import MainWindow

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def s1():
    # ── 1) scan_ports: 聞いているが受け付けない相手は busy ──────────
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(0)                               # 待ち行列を最小に。accept はしない
    fillers = []
    for _ in range(8):                          # 待ち行列を埋める
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.settimeout(0.2)
        try:
            c.connect(("127.0.0.1", port))
        except OSError:
            pass
        fillers.append(c)
    orig_range = _mb.PORT_RANGE
    _mb.PORT_RANGE = (port, port + 1)
    try:
        lp = _mb.listening_ports()
        check(lp is not None and port in lp, "OS の LISTEN 表が読める（%r）" % (None if lp is None else port in lp))
        open_, busy = _mb.scan_ports(timeout=0.15)
        closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        closed.bind(("127.0.0.1", 0)); closed_port = closed.getsockname()[1]; closed.close()
        _mb.PORT_RANGE = (closed_port,)
        open2, busy2 = _mb.scan_ports(timeout=0.15)
        check(closed_port not in open2 and closed_port not in busy2,
              "閉じたポートは open にも busy にも入らない（LISTEN 表に無い）")
        # 待ち行列が埋まった相手は環境によって «繋がる» か «時間切れ» のどちらかになる。
        # どちらにせよ «閉じた» 扱いにはならないことが要点。
        check(port in open_ or port in busy,
              "聞いている相手は閉じた扱いにならない（open=%r busy=%r）" % (open_, busy))
    finally:
        _mb.PORT_RANGE = orig_range
        for c in fillers:
            try:
                c.close()
            except OSError:
                pass
        srv.close()

    # ── 4) find_free_port は busy も使用中 ───────────────────────────
    orig_scan_mb = _mb.scan_ports
    _mb.scan_ports = lambda *a, **k: ([], [_mb.PORT_RANGE[0]])
    try:
        check(_mb.find_free_port() == _mb.PORT_RANGE[1],
              "時間切れのポートは空きと見ない（%r）" % _mb.find_free_port())
    finally:
        _mb.scan_ports = orig_scan_mb

    # ── 2)/3) 一覧: busy の Maya は «…» で残り、閉じたら落ちる ─────────
    w = MainWindow(sm); w.resize(1100, 700); w.show()
    w._scan_running["maya"] = True          # 本物の起動スキャンを止める
    QTest.qWait(250); app.processEvents()
    w._scan_running["maya"] = False
    orig_scan, orig_send = _mwd.scan_ports, _mb.MayaBridge.send_python
    _mb.MayaBridge.send_python = lambda self_, code, timeout=1.0, **k: (
        True, "MFMID<2027><C:/x/a.ma><111>MFMID" if self_.port == 20261
        else "MFMID<2027><C:/x/b.ma><222>MFMID")
    got = []
    w._bridge_notify.conn_list.connect(lambda items, dcc: got.append((list(items), dcc)))

    def wait_lists(n):
        for _ in range(300):
            app.processEvents()
            if len([1 for _it, d in got if d == "maya"]) >= n:
                return
            time.sleep(0.02)

    try:
        _mwd.scan_ports = lambda *a, **k: ([20261], [])
        w._refresh_maya_connections(); wait_lists(1)
        _mwd.scan_ports = lambda *a, **k: ([20262], [20261])    # 1 台目が読み込み中、2 台目が来た
        w._refresh_maya_connections(); wait_lists(2)
        _mwd.scan_ports = lambda *a, **k: ([20262], [])          # 1 台目が本当に閉じた
        w._refresh_maya_connections(); wait_lists(3)
    finally:
        _mwd.scan_ports, _mb.MayaBridge.send_python = orig_scan, orig_send
    lists = [it for it, d in got if d == "maya"]
    check(len(lists) >= 3, "一覧が 3 回届く（%d）" % len(lists))
    if len(lists) >= 3:
        ports1 = [it[0] for it in lists[0]]
        ports2 = [it[0] for it in lists[1]]
        ports3 = [it[0] for it in lists[2]]
        check(ports1 == [20261], "1 台目が出る（%r）" % ports1)
        check(sorted(ports2) == [20261, 20262],
              "2 台目が増えても、読み込み中の 1 台目は消えない（%r）" % ports2)
        busy_item = [it for it in lists[1] if it[0] == 20261]
        check(busy_item and busy_item[0][1].endswith("…") and busy_item[0][3] == 111,
              "読み込み中の 1 台目は «…» 付きで前回の識別を保つ（%r）" % (busy_item,))
        check(ports3 == [20262], "本当に閉じたら落ちる（%r）" % ports3)

    # ── 5) 見張りの後退 ───────────────────────────────────────────────
    w._dcc_pending = (("open", "C:/x/a.ma", "maya"), time.monotonic())
    w._dcc_pending_port = 20261
    started = []
    orig_thread = threading.Thread

    class _NoThread(orig_thread):
        def start(self):
            started.append(self.name)
    threading.Thread = _NoThread
    try:
        w._on_dcc_idle(20261, False)                 # 応答が無かった
        w._poll_dcc_idle()
        check(not started, "応答が無かった直後は繋ぎに行かない（backlog を埋めない）")
        w._dcc_watch_backoff_until = 0.0
        w._dcc_watch_busy = False
        w._poll_dcc_idle()
        check(bool(started), "間を空けたらまた聞く")
    finally:
        threading.Thread = orig_thread
        w._dcc_watch_busy = False
        w._clear_dcc_pending()
    finish(not fails)


run(s1, delay=800)
