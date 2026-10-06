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


def fake_run(cmd, cwd=None, timeout=None, input_text=None):
    calls.append(list(cmd))
    if "info" in cmd:
        return 0, INFO_OUT, ""
    if "clients" in cmd:
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

finish(True)
