# -*- coding: utf-8 -*-
"""r154（実機障害 2026-11-24）: v0.9.29 は配布版（PyInstaller の exe）でだけ、Maya へ送る
«保存の見張り» のコードが作れなかった（inspect.getsource は exe に .py が無いので失敗する）。
開発版のテストは全部通っていたのに、配布版では Maya 側の確認が全部消えていた。

確かめること:
  1) 実行時のコードで inspect.getsource を使っていない
  2) «配布版と同じ条件»（sys.frozen・ソースが取れない）で、Maya へ送るコードを全部作れて、
     構文も正しい（main._selfcheck_dcc_codes。リリースのビルドでは exe 自身もこれを走らせる）
"""
import inspect
import io
import os
import re
import sys
from _common import *  # noqa: F401,F403
from _common import finish

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
hits = []
for base in ("core", "ui"):
    for dp, _dn, fn in os.walk(os.path.join(ROOT, base)):
        for f in fn:
            if f.endswith(".py"):
                src = io.open(os.path.join(dp, f), encoding="utf-8").read()
                for i, ln in enumerate(src.splitlines(), 1):
                    s = ln.split("#", 1)[0]
                    if re.search(r"\binspect\.getsource|\bgetsource\(", s):
                        hits.append("%s:%d" % (os.path.relpath(os.path.join(dp, f), ROOT), i))
check(not hits, "実行時のコードで inspect.getsource を使わない（%r）" % (hits,))

sys.path.insert(0, ROOT)
import main

_orig = inspect.getsource


def _no_source(*a, **k):
    raise OSError("could not get source code (frozen)")


inspect.getsource = _no_source
sys.frozen = True
try:
    bad = main._selfcheck_dcc_codes()
finally:
    inspect.getsource = _orig
    del sys.frozen
check(not bad, "配布版と同じ条件で、Maya へ送るコードを全部作れる（%r）" % ([b[:2] for b in bad],))
finish(not fails)
