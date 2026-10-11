# -*- coding: utf-8 -*-
"""r144: カラム破棄中に «死にかけのカラム» を触らないこと。

test_preset_dirs が断続的に exit code 3221225477（ACCESS_VIOLATION）で落ちて
いた件。QColumnView の列は非同期に作られ捨てられるが、destroyed シグナルは
C++ のデストラクタの中から飛ぶ。そこで台帳の全カラムを走査（rootIndex() を
呼ぶ）していたため、同時に捨てられている列を叩いてプロセスごと落ちていた。

確認すること:
  1) destroyed ハンドラは «本人を台帳から外す» だけで、走査しない
  2) 走査・再描画は次のイベントループで行われる
  3) 台帳から確実に消える（破棄後に古いカラムが残らない）
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, finish, tmpdir, make_panel

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


b = make_panel()
_keep.append(b)
cv = b._column_view

# 1) destroyed ハンドラが走査しないこと（_live_columns を呼ばない）
scanned = []
orig_live = cv._live_columns
cv._live_columns = lambda: (scanned.append(1), orig_live())[1]

class _Dummy:
    pass

d1, d2 = _Dummy(), _Dummy()
cv._mfm_columns = [d1, d2]
cv._on_column_destroyed(id(d1))
check(not scanned, "destroyed の中では走査しない（%r）" % (scanned,))
check(cv._mfm_columns == [d2], "死んだ本人だけ台帳から外れる（%r）" % (cv._mfm_columns,))

# 2) 走査は次のイベントループで行われる
cv._mfm_columns = []
app.processEvents()
check(scanned, "次のイベントループで走査される（%r）" % (scanned,))
cv._live_columns = orig_live

# 3) 実際にカラムを作って捨てても台帳が増え続けない
root = tmpdir()
for n in ("x", "y", "z"):
    os.makedirs(os.path.join(root, n, "inner"))
b.navigate_to(root)
for _ in range(30):
    app.processEvents()
b.navigate_to(os.path.join(root, "x"))
for _ in range(30):
    app.processEvents()
b.navigate_to(os.path.join(root, "y", "inner"))
for _ in range(60):
    app.processEvents()
alive = cv._live_columns()
check(len(cv._mfm_columns) == len(alive),
      "台帳に死んだカラムが残らない（台帳 %d / 生存 %d）"
      % (len(cv._mfm_columns), len(alive)))

# QColumnView は先読みで列を確保するので «本数» 自体は環境依存。
# 見るべきは «行き来しても増え続けないこと»（＝死骸が溜まらないこと）。
n0 = len(cv._live_columns())
for _ in range(3):
    b.navigate_to(os.path.join(root, "z"))
    for _ in range(20):
        app.processEvents()
    b.navigate_to(os.path.join(root, "y", "inner"))
    for _ in range(20):
        app.processEvents()
n1 = len(cv._live_columns())
check(n1 <= n0 + 2, "行き来してもカラムが増え続けない（%d → %d）" % (n0, n1))
check(len(cv._mfm_columns) == n1,
      "行き来したあとも台帳に死骸が残らない（台帳 %d / 生存 %d）"
      % (len(cv._mfm_columns), n1))

finish(not fails)
