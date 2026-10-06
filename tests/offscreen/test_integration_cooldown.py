# -*- coding: utf-8 -*-
"""r125: 連携が «途中で切れたまま戻らない» を防ぐ。

ユーザー報告 2026-10-06:「Perforce の連携状態が何故か途中で切れます」

原因は 2 つ重なっていた。
  1) note_failure が MAX_FAILURES に達すると available=False にし、
     **二度と True に戻らなかった**。戻るのはアプリを再起動して detect() が
     走る時だけ。
  2) p4 fstat の rc != 0 を «何でも失敗» に数えていた。クライアントビュー外・
     未マップ・権限なしのフォルダでも rc != 0 になるため、社内のフォルダを
     3 つ開いただけで上限に達し、Perforce が丸ごと止まっていた。

ここで固定すること:
  - 連続失敗は «一時的な休み» であり、時間が経てば自分で戻る
  - 成功が 1 回あれば即座に戻る
  - 手動更新（refresh）は休みを打ち切る
  - «繋がらない» と «そのフォルダに情報が無い» を取り違えない
"""
import time
from _common import *  # noqa: F401,F403
from _common import finish

from core.integrations.base import Provider
from core.integrations.p4_provider import P4Provider

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


# ── 1) 連続失敗は «休み» であって死亡ではない ────────────────────────
p = Provider()
p.available = True
check(p.available, "検出できたら使える")
for _ in range(Provider.MAX_FAILURES):
    p.note_failure("つながらない")
check(not p.available, "連続失敗したら一旦止まる")
check(p.info.get("disabled_reason"), "止まった理由が残る")

p._disabled_until = time.monotonic() - 1.0        # 休み明けを模擬
check(p.available, "休みが明けたら自分で戻る")
check(not p.info.get("disabled_reason"), "戻ったら理由も消える")
check(p._failures == 0, "失敗の数え直しも戻る")

# ── 2) 成功が 1 回あれば即復帰 ───────────────────────────────────────
p2 = Provider()
p2.available = True
for _ in range(Provider.MAX_FAILURES):
    p2.note_failure("x")
check(not p2.available, "前提: 休んでいる")
p2.note_success()
check(p2.available, "成功したら即座に戻る")

# ── 3) 手動更新は休みを打ち切る ──────────────────────────────────────
p3 = Provider()
p3.available = True
for _ in range(Provider.MAX_FAILURES):
    p3.note_failure("x")
check(not p3.available, "前提: 休んでいる")
p3.clear_cooldown()
check(p3.available, "手動更新（clear_cooldown）で即座に戻る")

# ── 4) そもそも入っていない物は戻らない ──────────────────────────────
p4n = Provider()
p4n.available = False        # p4.exe が無い等
p4n.clear_cooldown()
check(not p4n.available, "インストールされていない物は手動更新でも戻らない")

# ── 5) «繋がらない» と «情報が無い» の取り違え ───────────────────────
benign = [
    "/d/* - no such file(s).",
    "/d/* - file(s) not in client view.",
    "/d/* - no permission for operation on file(s).",
    "Path 'D:/x/' is not under client's root 'D:/p4'.",
    "/d/* - file(s) not on client.",
]
conn = [
    "Perforce client error:\n\tConnect to server failed; check $P4PORT.",
    "TCP connect to perforce:1666 failed.",
    "Your session has expired, please login again.",
    "Perforce password (P4PASSWD) invalid or unset.",
]
for t in benign:
    check(not P4Provider._is_connection_error(t),
          "対象外として扱う: %r" % t.splitlines()[0][:48])
for t in conn:
    check(P4Provider._is_connection_error(t),
          "接続エラーとして扱う: %r" % t.splitlines()[0][:48])

# ── 6) ビュー外のフォルダをいくつ開いても止まらない ───────────────────
p5 = P4Provider()
p5.available = True
for _ in range(10):
    text = "/d/* - file(s) not in client view."
    if p5._is_connection_error(text):
        p5.note_failure(text)
check(p5.available, "ビュー外のフォルダを 10 個開いても連携は止まらない")

finish(not fails)
