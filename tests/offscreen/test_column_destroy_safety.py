# -*- coding: utf-8 -*-
"""r144: カラム破棄中に «死にかけのカラム» を触らないこと。

test_preset_dirs が断続的に exit code 3221225477（ACCESS_VIOLATION）で落ちて
いた件。QColumnView の列は非同期に作られ捨てられるが、destroyed シグナルは
C++ のデストラクタの中から飛ぶ。そこで台帳の全カラムを走査（rootIndex() を
呼ぶ）していたため、同時に捨てられている列を叩いてプロセスごと落ちていた。

確認すること:
  1) destroyed ハンドラは **何も触らない**（走査も台帳の組み替えもしない）
  2) 走査・再描画は次のイベントループで行われる
  3) 台帳から確実に消える（破棄後に古いカラムが残らない）
  4) r146: destroyed を lambda でつながない。lambda には持ち主が無いので、
     この QColumnView が先に捨てられた後でも呼ばれ、解放済みメモリを触る
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, finish, tmpdir, make_panel

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
cv._on_column_destroyed()
check(not scanned, "destroyed の中では走査しない（%r）" % (scanned,))
# r146: 台帳の組み替えも «デストラクタの中ではやらない» に変えた。
# 死んだものを外すのは次のループの _live_columns（RuntimeError で落とす）。
# デストラクタの中でリストを作り直すと、同時に捨てられている最中の
# ラッパーに触れてしまう。
check(cv._mfm_columns == [d1, d2],
      "destroyed の中では台帳も触らない（%r）" % (cv._mfm_columns,))

# r146: lambda ではなく «束縛メソッド» でつなぐこと。
# lambda には持ち主が無いので、この QColumnView が先に捨てられた後でも
# 呼ばれてしまい、解放済みメモリを触って 3221225477 で落ちる。
import ast, io as _io, os as _os
_src = _io.open(_os.path.join(ROOT, "ui", "browser_column_view.py"),
                encoding="utf-8").read()
_fn = [n for n in ast.walk(ast.parse(_src))
       if isinstance(n, ast.FunctionDef) and n.name == "_track_column"][0]
_lambda_connects = []
for n in ast.walk(_fn):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
            and n.func.attr == "connect":
        if any(isinstance(a, ast.Lambda) for a in n.args):
            _lambda_connects.append(n.lineno)
check(not _lambda_connects,
      "destroyed を lambda でつながない（%r）" % (_lambda_connects,))

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
