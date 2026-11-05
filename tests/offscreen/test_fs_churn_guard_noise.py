# -*- coding: utf-8 -*-
"""r146（2026-11-05）: 祖先フォルダの «数個のファイルを作っては消す» だけの
変化で、P4V 対策の監視停止が働いてしまわないこと。

Windows の回帰スイートと実機の mfm_fswatch.log（watching=[…Temp]）で発覚:
Temp やドライブ直下では他のアプリが小さなファイルを作っては消し続ける。
その通知だけで «変化 8 件以上» に届き、UI が少し忙しいと大量更新と
取り違えて監視を止めた。止めた後もその祖先は変わり続けるので、30 秒の
安全弁まで戻らず、その間は表示が自動で更新されなかった。

確かめること:
  1) 祖先で 3 つのファイルを素早く作っては消し続けても、UI が忙しくても止めない
  2) 表示中のフォルダで P4V と同じ書き方（毎回別のファイル）なら止める
"""
import os
import stat
import subprocess
import sys
import time
from _common import *  # noqa: F401,F403
from _common import app, make_panel, finish, tmpdir
from core.compat import QTimer

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def pump(sec):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.003)


root = tmpdir("mfm_noise_only_")
work = os.path.join(root, "proj", "work")
os.makedirs(work)
for j in range(300):
    open(os.path.join(root, "proj", "a_%04d.ma" % j), "w").close()

b = make_panel(1300, 650)
g = b._churn_guard
pauses = []
_orig = g.pause
g.pause = lambda why="": (pauses.append(why), _orig(why))[1]
b.navigate_to(root)
pump(1.5)
b.navigate_to(work)
pump(3.0)
# 表示中のフォルダの変化が届くようになってから（切れたネットワークドライブ対策。
# test_fs_churn_guard と同じ）
open(os.path.join(work, "ready.txt"), "w").close()
t0 = time.perf_counter()
while time.perf_counter() - t0 < 90:
    pump(0.25)
    mi = b._fs_model.index(work)
    if any(b._fs_model.index(r, 0, mi).data() == "ready.txt" for r in range(b._fs_model.rowCount(mi))):
        break
pump(3.0)       # 移動直後の猶予を抜ける

# UI を忙しくする（100ms ごとに 110ms ふさぐ＝判定の «30% 以上» を十分に満たす）
busy = QTimer()
busy.setInterval(100)
busy.timeout.connect(lambda: time.sleep(0.11))
busy.start()

NOISE = r'''
import os, sys, time
d = sys.argv[1]; end = time.time() + float(sys.argv[2]); i = 0
while time.time() < end:
    p = os.path.join(d, "noise_%d.tmp" % (i % 3)); i += 1
    with open(p, "w") as f: f.write("n")
    time.sleep(0.12)
    try: os.remove(p)
    except OSError: pass
    time.sleep(0.04)
'''
nf = os.path.join(tmpdir("mfm_noise_script_"), "_noise.py")
with open(nf, "w") as f:
    f.write(NOISE)
noise = subprocess.Popen([sys.executable, nf, root, "10"])
while noise.poll() is None:
    pump(0.2)
pump(1.0)
check(not pauses, "祖先で数個のファイルを作っては消すだけなら止めない（%r）" % (pauses,))

# P4V と同じ書き方（毎回別のファイル）なら止める
WRITER = r'''
import os, sys, stat, time
n = int(sys.argv[1]); d = sys.argv[2]
for k in range(n):
    t = os.path.join(d, "tmp.%d.%d" % (os.getpid(), k))
    with open(t, "w") as f: f.write("y")
    dst = os.path.join(d, "g%04d.ma" % k)
    os.rename(t, dst); os.chmod(dst, stat.S_IREAD)
    time.sleep(0.01)
'''
wf = os.path.join(tmpdir("mfm_writer_script_"), "_writer.py")
with open(wf, "w") as f:
    f.write(WRITER)
w = subprocess.Popen([sys.executable, wf, "500", work])
paused_during = False
while w.poll() is None:
    pump(0.05)
    paused_during = paused_during or g.is_paused()
check(paused_during, "P4V と同じ書き方なら止める（%r）" % (pauses,))
busy.stop()
for n in os.listdir(work):
    try:
        os.chmod(os.path.join(work, n), stat.S_IWRITE)
    except OSError:
        pass
finish(not fails)
