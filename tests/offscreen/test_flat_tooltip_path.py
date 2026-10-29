# -*- coding: utf-8 -*-
"""r146（ユーザー指示 2026-10-26）: 平坦ビューの項目にマウスオーバーすると、
平坦化したルートからのパスが出る。同じ名前のファイルがどのフォルダの
ものかを見分けるため。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, tmpdir, finish, QTest

from core.compat import Qt
from ui.flat_column import FlatColumn, _PATH_ROLE

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


root = tmpdir()
for sub in (os.path.join("shotA", "anim"), os.path.join("shotB", "anim")):
    os.makedirs(os.path.join(root, sub))
    open(os.path.join(root, sub, "same.ma"), "w").close()

fc = FlatColumn()
fc.set_sources([root])
QTest.qWait(50)
app.processEvents()


def tips():
    out = {}
    for r in range(fc._src.rowCount()):
        it = fc._src.item(r)
        out[it.data(_PATH_ROLE)] = it.data(Qt.ToolTipRole) or ""
    return out


t = tips()
a = os.path.join(root, "shotA", "anim", "same.ma")
b = os.path.join(root, "shotB", "anim", "same.ma")
check(len(t) == 2, "前提: 同名の 2 件が並ぶ")
check(t.get(a, "").splitlines()[0] == os.path.join("shotA", "anim", "same.ma"),
      "1 件目: ルートからのパス %r" % t.get(a))
check(t.get(b, "").splitlines()[0] == os.path.join("shotB", "anim", "same.ma"),
      "2 件目: ルートからのパス %r" % t.get(b))
check(t.get(a) != t.get(b), "同名でもツールチップで見分けられる")
check(os.path.normpath(a) in t.get(a, ""), "2 行目にフルパス")

# 複数のルートを平坦化した時は、どのルートかが分かる（ルート名を先頭に）
fc.set_sources([os.path.join(root, "shotA"), os.path.join(root, "shotB")])
app.processEvents()
t = tips()
check(t.get(a, "").splitlines()[0] == os.path.join("shotA", "anim", "same.ma"),
      "複数ルート: ルート名から %r" % t.get(a))
check(t.get(b, "").splitlines()[0] == os.path.join("shotB", "anim", "same.ma"),
      "複数ルート: ルート名から %r" % t.get(b))

finish(not fails)
