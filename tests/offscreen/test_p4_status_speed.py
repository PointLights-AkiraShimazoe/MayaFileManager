# -*- coding: utf-8 -*-
"""r156（ユーザー報告 2026-11-24「Perforce のステータスアイコンの更新が相当遅い」）。

実機の Perforce とログで分かったこと（2026-11-27）:
  1) フォルダに日本語の名前のファイルが 1 つでもあると、そのフォルダの p4 fstat が
     丸ごと «No Translation» で失敗し、バッジが一切出なかった（p4.exe が «フォルダ\\*» を
     手元で展開し、Unicode のサーバーに日本語をコマンド行で渡していた）
  2) p4.exe が «フォルダ\\*» を手元で展開する＝フォルダを読むので、遅いドライブで
     12 秒の打ち切りまで待つことがあった（ログで 70 回）
  3) ジャンクションの実体側のフォルダは、ワークスペースのルートと表記が合わず、
     新しいフォルダを開くたびに p4 where を実行していた（ログで 660 回）
  4) 状態取得のワーカーが 1 本で、遅いフォルダが 1 つあると後ろの全フォルダが待たされた
  5) 右クリックのチェックアウト等も、日本語の名前のファイルだと «No Translation» で失敗する

確かめること:
  A) fstat のパターンは引数ファイル（-x）で渡す。Unicode のサーバーなら UTF-8（-C utf8）
  B) ジャンクションの実体側のフォルダも p4 where 無しでワークスペースが決まる（-c も正しい）
  C) 右クリックの操作もファイル群を引数ファイルで渡す（日本語の名前・件数が多くても 1 回）
  D) 状態取得は並んで走り、遅いフォルダがあっても他のフォルダのバッジは先に出る
  E) 検出が終わる前に積まれた状態取得は、検出の後で走る
"""
import io
import os
import subprocess
import threading
import time
from _common import *  # noqa: F401,F403
from _common import tmpdir, finish, app

import core.integrations.base as _base
import core.integrations.p4_provider as _p4m
from core.integrations.p4_provider import P4Provider
from core.integrations.base import norm

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


d = tmpdir("mfm_p4speed_")
real = os.path.join(d, "real")
os.makedirs(os.path.join(real, "Seq", "c001"))
ws = os.path.join(d, "ws")
if os.name == "nt":
    subprocess.run(["cmd", "/c", "mklink", "/J", ws, real], capture_output=True)
else:
    os.symlink(real, ws)
check(os.path.realpath(ws) != os.path.normpath(ws), "ワークスペースのジャンクションを作れた")
other = os.path.join(d, "other_ws")
os.makedirs(other)

INFO = ("... userName akira\n... clientHost this-pc\n... clientName WS_OTHER\n"
        "... clientRoot %s\n... unicode enabled\n" % other)
CLIENTS = ("... client WS_OTHER\n... Root %s\n... Host \n\n"
           "... client WS_MM\n... Root %s\n... Host \n\n") % (other, ws)
calls = []
xseen = []


def fake_run(cmd, cwd=None, timeout=None, input_text=None):
    calls.append(list(cmd))
    if "-x" in cmd:
        xf = cmd[cmd.index("-x") + 1]
        xseen.append(io.open(xf, "rb").read())
    if "info" in cmd:
        return 0, INFO, ""
    if "clients" in cmd:
        return 0, CLIENTS, ""
    if "where" in cmd:
        return 1, "", "not under client's root"
    if "fstat" in cmd:
        return 0, "... clientFile %s\n... headRev 2\n... haveRev 2\n" % os.path.join(cwd, u"背景_A.ma"), ""
    return 0, "", ""


_base.run = fake_run
_p4m.run = fake_run
p = P4Provider()
p.p4 = "p4"
p.available = True

# B) 実体側のフォルダ
seq_real = os.path.join(real, "Seq")
calls.clear()
r = p.find_root(seq_real)
check(r and norm(r) == norm(ws), "実体側のフォルダでもワークスペース（ルート %r）が決まる（%r）" % (ws, r))
check(not [c for c in calls if "where" in c], "p4 where を実行しない（%r）" % ([c for c in calls if "where" in c],))
check(p.client_for(seq_real) == "WS_MM" and p.conn_args_for(seq_real)[-2:] == ["-c", "WS_MM"],
      "実体側のフォルダの -c はそのワークスペース（WS_MM）（%r）" % (p.conn_args_for(seq_real),))

# A) fstat は引数ファイルで
calls.clear(); xseen.clear()
io.open(os.path.join(seq_real, u"背景_A.ma"), "w").write("x")
st = p.fetch_status(r, seq_real)
fst = [c for c in calls if "fstat" in c]
pattern = os.path.join(seq_real, "*")
check(fst and "-x" in fst[0] and pattern not in fst[0], "fstat のパターンをコマンド行に置かず -x で渡す（%r）" % (fst[:1],))
check(fst and fst[0][fst[0].index("-x") - 2:fst[0].index("-x")] == ["-C", "utf8"],
      "Unicode のサーバーなので -C utf8")
check(xseen and xseen[0].decode("utf-8").strip() == pattern, "引数ファイルの中身は UTF-8 のパターン（%r）" % (xseen[:1],))
check(fst and fst[0][fst[0].index("-x") + 1] and not os.path.exists(fst[0][fst[0].index("-x") + 1]),
      "引数ファイルは後で消す")
check(st.get(norm(os.path.join(seq_real, u"背景_A.ma"))) == _p4m.ST_CLEAN, "日本語の名前のファイルにも状態が付く（%r）" % (st,))

# C) 右クリックの操作
ran = []
done = threading.Event()


def fake_run2(cmd, cwd=None, timeout=None, input_text=None):
    xf = cmd[cmd.index("-x") + 1] if "-x" in cmd else None
    ran.append((list(cmd), io.open(xf, "rb").read().decode("utf-8") if xf else None, xf))
    done.set()
    return 0, "//depot/x#1 - opened for edit", ""


_base.run = fake_run2
p.report = lambda ok, msg: None
files = [os.path.join(seq_real, u"背景_A.ma")] + [os.path.join(seq_real, "f%04d.ma" % i) for i in range(3000)]
acts = p.actions(r, files)
co = [a for a in acts if a[0] and "p4 edit" in a[0]][0]
co[1]()
done.wait(5)
time.sleep(0.3)
check(len(ran) == 1, "3000 件でも 1 回で実行（分割しない）（%d 回）" % len(ran))
if ran:
    cmd, content, xf = ran[0]
    check("-x" in cmd and cmd[-1] == "edit" and not any(f in cmd for f in files[:2]),
          "ファイル群はコマンド行に置かず -x で渡す（%r）" % (cmd,))
    check(content and content.splitlines()[0] == files[0] and len(content.splitlines()) == len(files),
          "引数ファイルに日本語の名前を含む全件が入る")
    check(cmd[cmd.index("-c") + 1] == "WS_MM", "操作もそのワークスペースで")
    check(xf and not os.path.exists(xf), "操作の引数ファイルも後で消す")
_base.run = fake_run

# D・E) 並んで走る・検出の後で走る
from core.integrations.manager import IntegrationManager


class SlowProv:
    key, label, available = "p4", "Perforce", True    # info は持たない（検出の失敗に強いかも見る）

    def __init__(self):
        self.order = []

    def detect(self):
        time.sleep(0.5)
        self.order.append("detect")

    def root_for(self, d):
        return d

    def fetch_status(self, root, d):
        self.order.append("start " + os.path.basename(d))
        time.sleep(3.0 if os.path.basename(d) == "slow" else 0.05)
        self.order.append("end " + os.path.basename(d))
        return {}

    def note_failure(self, *a):
        pass


m = IntegrationManager()
sp = SlowProv()
m.providers = [sp]
got = {}
m.status_updated.connect(lambda dd: got.setdefault(os.path.basename(dd), time.monotonic()))
slow, fast = os.path.join(d, "slow"), os.path.join(d, "fast")
t0 = time.monotonic()
m.start()
m.request_status(slow)
m.request_status(fast)
end = time.monotonic() + 8
while time.monotonic() < end and len(got) < 2:
    app.processEvents()
    time.sleep(0.02)
check(sp.order and sp.order[0] == "detect", "検出が終わってから状態取得が始まる（%r）" % (sp.order,))
check("fast" in got and "slow" in got and got["fast"] < got["slow"],
      "遅いフォルダが先に積まれていても、速いフォルダのバッジが先に出る（%r）" % ({k: round(v - t0, 2) for k, v in got.items()},))
check("fast" in got and got["fast"] - t0 < 2.0, "速いフォルダは遅いフォルダを待たない（%.2f 秒）" % (got.get("fast", 99) - t0))
check(m.WORKERS >= 2 and len(m._workers) == m.WORKERS, "状態取得のワーカーは複数（%d）" % len(m._workers))

if os.name == "nt":
    subprocess.run(["cmd", "/c", "rmdir", ws], capture_output=True)
finish(not fails)
if fails:
    print("FAILED:", fails)
