# -*- coding: utf-8 -*-
"""r122: Perforce のワークスペースが複数あっても全部で状態が出ること。

ユーザー報告 2026-10-06:
  「あるワークスペースのステータスは反映されるが、他のワークスペースが
    反応していない」

原因: p4 info が返す clientRoot（＝今つないでいる **1 つ**）だけで
「Perforce 管理下か」を判定していた。他のワークスペースのフォルダは
`p4 where` が "not under client's root" を返すので管理外扱いになり、
バッジも右クリックメニューも出なかった。
対策: `p4 clients -u <user>` でこのユーザーの全ワークスペースを引き、
パスから «どのワークスペースか» を決めて、以後 -c で指定する。
"""
import os
from _common import *  # noqa: F401,F403
from _common import tmpdir, finish

import core.integrations.base as _base
from core.integrations.p4_provider import P4Provider
from core.integrations.base import norm

base_dir = tmpdir()
ws_a = os.path.join(base_dir, "ws_a")          # 今つないでいる方
ws_b = os.path.join(base_dir, "ws_b")          # もう一方
ws_b_deep = os.path.join(ws_b, "Assets", "CH")
other = os.path.join(base_dir, "not_p4")
for d in (ws_a, ws_b_deep, other):
    os.makedirs(d, exist_ok=True)

CLIENTS_OUT = (
    "... client WS_A\n... Root %s\n... Host \n\n"
    "... client WS_B\n... Root %s\n... Host \n\n"
    "... client WS_OTHERPC\n... Root C:\\\\elsewhere\n... Host someone-else\n\n"
) % (ws_a, ws_b)

INFO_OUT = ("... userName akira\n... clientHost this-pc\n"
            "... clientName WS_A\n... clientRoot %s\n" % ws_a)

calls = []


# -u 無しで引くとサーバーの全クライアント（他ユーザー・Host 無し）が返る
ALL_CLIENTS_OUT = "".join(
    "... client jenkins_%03d\n... Root D:\\build\\%03d\n... Host \n... Owner jenkins\n\n" % (i, i)
    for i in range(400))


def fake_run(cmd, cwd=None, timeout=None, input_text=None):
    calls.append(list(cmd))
    if "info" in cmd:
        return 0, INFO_OUT, ""
    if "clients" in cmd:
        if "-u" not in cmd:
            return 0, ALL_CLIENTS_OUT, ""
        return 0, CLIENTS_OUT, ""
    if "where" in cmd:
        return 1, "", "not under client's root"
    if "fstat" in cmd:
        return 0, "", ""
    return 0, "", ""


_base.run = fake_run
import core.integrations.p4_provider as _p4m
_p4m.run = fake_run

p = P4Provider()
p.p4 = "p4"
p._conn_args = []

# ── 1) 両方のワークスペースが «管理下» と判定されること ────────────────
assert p.find_root(ws_a), "つないでいるワークスペースが管理下にならない"
assert p.find_root(ws_b), "**他のワークスペースが管理下にならない（報告された不具合）**"
assert p.find_root(ws_b_deep), "他のワークスペースの深い階層が管理下にならない"
assert p.find_root(other) is None, "無関係なフォルダまで管理下にしている"
print("every workspace of this user counts as managed: OK")

# ── 1b) detect() をもう一度走らせても（エリアを足すと走る）壊れない ────
# ユーザー報告 2026-10-21「P4V ですが、ワークスペースが MM のものしか反応して
# いません」: 2 つ目のエリアを作ると detect() が info を空に戻し、以後
# `p4 clients` が -u 無し（全 413 件、このユーザーの分は Host 付きで除外）に
# なって、既定のワークスペース以外が管理外に戻っていた。
p.detect()
p.p4 = "p4"
names = [n for _k, n, _r in p._all_clients()]
assert "WS_A" in names and "WS_B" in names, ("detect() のやり直し後にこのユーザーのワークスペースが消えた", names)
assert not any(n.startswith("jenkins_") for n in names), ("-u 無しで全クライアントを引いている", names[:5])
assert p.find_root(ws_b), "detect() のやり直し後に他のワークスペースが管理外になる（報告された不具合）"
assert all("-u" in c for c in calls if "clients" in c), ("clients を -u 無しで引いた", [c for c in calls if "clients" in c])
print("re-detect keeps every workspace of this user: OK")

# ── 2) ホストが違うワークスペースは使わない ───────────────────────────
roots = [r for _k, _n, r in p._all_clients()]
assert "C:\\elsewhere" not in roots, ("他 PC 用のワークスペースを拾っている", roots)
print("a workspace bound to another host is ignored: OK")

# ── 3) フォルダごとに正しいワークスペース名が引けること ────────────────
assert p.client_for(ws_a) == "WS_A", p.client_for(ws_a)
assert p.client_for(ws_b) == "WS_B", p.client_for(ws_b)
assert p.client_for(ws_b_deep) == "WS_B", p.client_for(ws_b_deep)
assert p.client_for(other) == "", p.client_for(other)
print("each folder resolves to its own workspace: OK")

# ── 4) p4 の呼び出しに «そのフォルダの» -c が付くこと ──────────────────
assert p.conn_args_for(ws_b)[-2:] == ["-c", "WS_B"], p.conn_args_for(ws_b)
# 既定接続に別の -c があっても置き換わる
p._conn_args = ["-p", "srv:1666", "-u", "akira", "-c", "WS_A"]
got = p.conn_args_for(ws_b_deep)
assert got.count("-c") == 1 and got[-1] == "WS_B", got
assert "-p" in got and "srv:1666" in got, ("ポート指定が落ちている", got)
print("the right -c is used per folder, replacing the default: OK")

# fstat が «そのワークスペース» で実行されること
calls.clear()
p.fetch_status(ws_b, ws_b_deep)
fstat = [c for c in calls if "fstat" in c]
assert fstat, calls
i = fstat[0].index("-c")
assert fstat[0][i + 1] == "WS_B", ("fstat が別のワークスペースで走っている", fstat[0])
print("fstat runs against the folder's own workspace: OK")

# 右クリックの操作も同じワークスペースで実行されること
sent = []
p.run_cli_async = lambda cmd, cwd, what, **k: sent.append(list(cmd))
acts = p.actions(ws_b, [ws_b_deep])
hit = [a for a in acts if a[0] and "追加" in a[0]]
assert hit
hit[0][1]()
assert sent and "-c" in sent[0] and sent[0][sent[0].index("-c") + 1] == "WS_B", sent
print("context-menu actions use the folder's workspace too: OK")

# ── 5) ⟳（手動更新）で一覧を取り直すこと ─────────────────────────────
assert p._clients is not None
p.invalidate_status(ws_b)
assert p._clients is None and p._client_for_dir == {}, \
    "手動更新でワークスペース一覧が捨てられていない（新規作成が拾えない）"
print("a manual refresh re-reads the workspace list: OK")

# ── 5) 検出はプロセスで 1 回。エリアを足しても（start を何度呼んでも）
#       やり直さない（r146、ユーザー報告 2026-10-21 の直接の引き金）──────
import time as _time
from core.integrations.manager import IntegrationManager
m = IntegrationManager()
n = {"detect": 0}
m._do_detect = lambda: n.__setitem__("detect", n["detect"] + 1)
m.start(True)
m.start(True)                 # 2 つ目のエリア
m.start(True)                 # 3 つ目
for _ in range(50):
    if n["detect"] >= 1:
        break
    _time.sleep(0.02)
_time.sleep(0.2)
assert n["detect"] == 1, ("エリアを足すたびに検出をやり直している", n)
m.start(True, redetect=True)  # 明示のやり直しだけ通る
for _ in range(50):
    if n["detect"] >= 2:
        break
    _time.sleep(0.02)
assert n["detect"] == 2, ("明示の再検出が走らない", n)
print("detection runs once per process (areas do not re-trigger it): OK")

finish(True)
