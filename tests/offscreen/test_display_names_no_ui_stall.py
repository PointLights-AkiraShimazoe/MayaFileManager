# -*- coding: utf-8 -*-
"""ユーザー報告 2026-11-02「P4V で GetLatest をしていると Manager がフリーズします」

mfm_freeze.log: カラムを作るたびに、表示名の隠しファイル
（.mfm_display_names.json）の有無を UI スレッドで os.stat していて、P4V の
書き込みで詰まった外付けドライブ（ジャンクションの先）で 20 秒以上止まった。

確かめること（stat が 2.5 秒かかるドライブを模す）:
  1) has_file / alias_map は待たずに返る（UI スレッドでディスクに触らない）
  2) 答えが出たら知らせが来て、その後は正しい答えになる
  3) そのフォルダへ移動しても UI が止まらない
  4) 答えが出た後、カラムに表示名の操作列（フッタ）が出る
  5) 自分で保存した直後は、待たずに保存した中身が返る
"""
import os
import time
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, QTest
from core import display_names as dn
from core.compat import QTimer

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


root = tmpdir()
slow = os.path.join(root, "slow_drive_dir")
os.makedirs(os.path.join(slow, "CH001"))
os.makedirs(os.path.join(slow, "CH002"))
with open(os.path.join(slow, dn.FILE_NAME), "w", encoding="utf-8") as f:
    f.write('{"enabled": true, "title": "T", "names": {"CH001": "Alias1"}}')

SLOW_SEC = 2.5
_orig_sig = dn._stat_sig


def _slow_sig(path):
    if os.path.normcase(slow) in os.path.normcase(path):
        time.sleep(SLOW_SEC)
    return _orig_sig(path)


dn._stat_sig = _slow_sig
heard = []
if hasattr(dn, "add_listener"):
    dn.add_listener(lambda d: heard.append(d))

# 1) 待たずに返る
t = time.perf_counter()
first = dn.has_file(slow)
dt = time.perf_counter() - t
check(dt < 0.5, "遅いドライブでも has_file は待たない（%.2f 秒）" % dt)

# 2) 答えが出たら知らせが来る
t_end = time.perf_counter() + SLOW_SEC + 3
while time.perf_counter() < t_end and not heard:
    time.sleep(0.05)
check(any(os.path.normcase(h) == os.path.normcase(slow) for h in heard),
      "答えが出たら知らせが来る（%r）" % (heard,))
t = time.perf_counter()
check(dn.has_file(slow) and dn.alias_map(slow) == {"CH001": "Alias1"},
      "その後は正しい答え（%r）" % (dn.alias_map(slow),))
check(time.perf_counter() - t < 0.3, "2 回目以降も待たない")

# 3)(4) 移動しても UI が止まらず、後でフッタが出る
dn.invalidate()
b = make_panel(1200, 650)
lates = []
last = [time.perf_counter()]


def _beat():
    now = time.perf_counter()
    lates.append(now - last[0] - 0.05)
    last[0] = now


beat = QTimer()
beat.setInterval(50)
beat.timeout.connect(_beat)
beat.start()
last[0] = time.perf_counter()
# 親を開いた時点で、子のカラムが先に作られることがある（実機の経路と同じ）。
# 親への移動から計る。
_t_nav = time.perf_counter()
b.navigate_to(root)
nav_dt = time.perf_counter() - _t_nav
_t0 = time.perf_counter()
while time.perf_counter() - _t0 < 0.8:
    _tp = time.perf_counter()
    app.processEvents()
    lates.append(time.perf_counter() - _tp)
    time.sleep(0.01)
_t_nav = time.perf_counter()
b.navigate_to(slow)
nav_dt = max(nav_dt, time.perf_counter() - _t_nav)
t_end = time.perf_counter() + SLOW_SEC + 4
footer = None
while time.perf_counter() < t_end:
    _tp = time.perf_counter()
    app.processEvents()
    lates.append(time.perf_counter() - _tp)      # 1 回の処理で止まっていた時間
    time.sleep(0.01)
    for v in b._column_view._live_columns():
        p = b._column_view._path_for_index(v.rootIndex()) or ""
        if os.path.normcase(p) == os.path.normcase(slow) and getattr(v, "_mfm_dn_footer", None) is not None:
            footer = v._mfm_dn_footer
    if footer is not None:
        break
beat.stop()
worst = max([nav_dt] + lates)
check(worst < 1.0, "遅いドライブのフォルダへ移動しても UI が止まらない（最大 %.2f 秒）" % worst)
check(footer is not None, "答えが出た後、表示名の操作列が出る")

# 5) 自分で保存した直後は待たずにその中身
t = time.perf_counter()
dn.save(slow, {"CH002": "Alias2"}, title="T2")
got = dn.alias_map(slow)
check(got == {"CH002": "Alias2"} and time.perf_counter() - t < SLOW_SEC,
      "保存した直後は待たずに保存した中身（%r）" % (got,))

dn._stat_sig = _orig_sig
finish(not fails)
