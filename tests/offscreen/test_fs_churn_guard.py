# -*- coding: utf-8 -*-
"""r146（ユーザー報告 2026-10-30「P4V で取得中にフリーズする」）

原因: 見張っているフォルダに新しいファイルが現れるたびに QFileSystemModel が
読み込み済みの全フォルダを並べ替え直し（layoutChanged）、その後段の作り直しが
Qt の C++ 側で重い。P4V の取得（一時ファイル → 名前変更 → 読み取り専用）を
何千回も受けると途切れなく続いて UI が止まる。

対策 ui/fs_churn_guard.py: 大量更新の間だけ監視を止め、落ち着いたら戻して
表示中のフォルダを読み直す。確かめること:
  1. P4V と同じ書き方の連続書き込みで監視が止まる（UI の塞がりが減る）
  2. 書き込みが終わると監視が戻り、一覧が最新（全件・一時ファイル無し）
  3. 戻った後の 1 件の変化は今までどおり自動で反映される（監視が生きている）
  4. 普通のフォルダ移動（大きなフォルダを次々開く）では止まらない
  5. 止めている間に変わった «読み込み済みだが表示していない» フォルダは、
     後で開いた時に最新になる"""
import os
import stat
import subprocess
import sys
import tempfile
import time
from _common import *  # noqa: F401,F403
from _common import app, make_panel, finish, QTest
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


root = tempfile.mkdtemp(prefix="mfm_churn_")
bigs = []
for i in range(4):
    d = os.path.join(root, "big%d" % i)
    os.makedirs(d)
    for j in range(3000):
        open(os.path.join(d, "asset_%05d.uasset" % j), "w").close()
    bigs.append(d)
work = os.path.join(bigs[0], "work")
other = os.path.join(bigs[1], "other")          # 読み込むが、書き込み中は表示しない
os.makedirs(work)
os.makedirs(other)

b = make_panel(1400, 700)
g = b._churn_guard
pauses = []
_orig_pause = g.pause
g.pause = lambda why="": (pauses.append(why), _orig_pause(why))[1]

# 4) 普通のフォルダ移動では止まらない
for d in bigs + [other]:
    b.navigate_to(d)
    pump(1.2)
b.navigate_to(work)
pump(3.0)
check(not pauses and not g.is_paused(), "普通のフォルダ移動では監視を止めない: %r" % pauses)

m = b._fs_model


def names(d):
    idx = m.index(d)
    return [m.index(r, 0, idx).data() for r in range(m.rowCount(idx))]


WRITER = r'''
import os, sys, stat, time
n = int(sys.argv[1]); ds = sys.argv[2:]
for k in range(n):
    d = ds[k % len(ds)]
    t = os.path.join(d, "tmp.%d.%d" % (os.getpid(), k))
    with open(t, "w") as f: f.write("y")
    dst = os.path.join(d, "g%04d.ma" % k)
    os.rename(t, dst); os.chmod(dst, stat.S_IREAD)
    time.sleep(0.01)
'''
wf = os.path.join(root, "_writer.py")
with open(wf, "w") as f:
    f.write(WRITER)

# 1) P4V と同じ書き方で連続書き込み（表示中の work と、裏の other の両方）
proc = subprocess.Popen([sys.executable, wf, "700", work, other])
paused_during = False
while proc.poll() is None:
    app.processEvents()
    time.sleep(0.003)
    paused_during = paused_during or g.is_paused()
check(paused_during, "連続書き込みの間は監視を止める: %r" % pauses)
check(len(pauses) == 1, "止めるのは 1 回だけ（自分の読み直しで止め直さない）: %r" % pauses)

# 2) 書き込みが終わると戻り、一覧が最新
waited = 0.0
while g.is_paused() and waited < 12:
    pump(0.5)
    waited += 0.5
check(not g.is_paused(), "書き込みが終わると監視を戻す（%.1f 秒）" % waited)
pump(2.5)
nw = names(work)
want_w = 350
check(sum(1 for n in nw if n.startswith("g")) == want_w and not any(n.startswith("tmp.") for n in nw),
      "表示中のフォルダが最新: g=%d（%d 件のはず）tmp=%d"
      % (sum(1 for n in nw if n.startswith("g")), want_w, sum(1 for n in nw if n.startswith("tmp."))))

# 3) 戻った後の 1 件の変化は自動で反映される
one = os.path.join(work, "after_resume.ma")
open(one, "w").close()
ok = False
for _ in range(40):
    pump(0.25)
    if "after_resume.ma" in names(work):
        ok = True
        break
check(ok, "戻った後の変化は自動で反映される（監視が生きている）")

# 5) 止めている間に変わった裏のフォルダは、開いた時に最新になる
#    （カラムのクリックと同じ経路。アドレス欄からの移動はそれ自体が読み直すので
#     確かめにならない）
b._select_in_columns(other)
ok = False
for _ in range(40):
    pump(0.25)
    no = names(other)
    if sum(1 for n in no if n.startswith("g")) == 350 and not any(n.startswith("tmp.") for n in no):
        ok = True
        break
no = names(other)
check(ok, "止めていた間に変わったフォルダも開くと最新: g=%d tmp=%d"
      % (sum(1 for n in no if n.startswith("g")), sum(1 for n in no if n.startswith("tmp."))))

for d in (work, other):
    for n in os.listdir(d):
        try:
            os.chmod(os.path.join(d, n), stat.S_IWRITE)
        except OSError:
            pass
finish(not fails)
