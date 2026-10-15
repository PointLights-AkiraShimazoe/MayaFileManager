# -*- coding: utf-8 -*-
"""r146: 実機でのコンパクトの «細かいが致命的» な点（ユーザー報告 2026-10-15）。

  「コンパクト状態にした直後にマウスオーバーをしても反応がない」
  「コンパクトで再起動すると、必ず画面の一番左上にでてくる。
    元々閉じた場所に出て欲しい。」
  「コンパクトの縁に黒い四角のあまりが見えます。また、青い線を太くするなりで
    もう少し目立つ様にしてください。」
  「パックを右クリック → 最小化 だけ通常ウィンドウが開くだけになってます。」
  「オプションウィンドウがアクティブになっていると、マウスがマネージャから
    外れてもコンパクトになりません。マネージャー関連のウィンドウのどれが
    アクティブでも、全てからマウスが離れているとコンパクトにしてください。」
"""
import ast
import os

from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest

from core.compat import Qt
import ui.compact_mode as cm
from ui.compact_mode import CompactController
from ui.main_window import MainWindow

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CM = ast.parse(open(os.path.join(ROOT, "ui", "compact_mode.py"),
                    encoding="utf-8").read())


def _func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


# ── 1) 畳んだ直後、カーソルの下に出ても構える ───────────────────
ob = None
for n in ast.walk(CM):
    if isinstance(n, ast.ClassDef) and n.name == "_OpenButton":
        ob = n
check(_func(ob, "arm_if_hovered") is not None,
      "カーソルの下に出てきた時も «乗せた» 扱いで構える口がある")
body = _func(ob, "_open_if_still_here")
names = {x.attr for x in ast.walk(body) if isinstance(x, ast.Attribute)}
check("underMouse" not in names,
      "underMouse に頼らない（出てきた時は False のままになる）")
puck_cls = None
for n in ast.walk(CM):
    if isinstance(n, ast.ClassDef) and n.name == "_Puck":
        puck_cls = n
check(_func(puck_cls, "showEvent") is not None,
      "パックは出た時に構え直す")

# ── 2) 退けてある間はパックの位置を触らない（起動時に左上へ飛ばない）──
col = _func(CM, "collapse")
check(any(isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
          and x.func.attr == "is_parked" for x in ast.walk(col)),
      "退けてある時はパックを «本体の角» から決め直さない")

# ── 3) 縁：透過＋太い線＋光る色 ─────────────────────────────────
init = _func(puck_cls, "__init__")
attrs = [x for x in ast.walk(init)
         if isinstance(x, ast.Attribute) and x.attr == "WA_TranslucentBackground"]
check(bool(attrs), "角の外に窓の地色が残らない（透過）")
check(hasattr(cm._Puck, "FLASH_KEY") and hasattr(cm._Puck, "flash"),
      "«ここに居ます» と光る口がある")

# ── 4) 右クリックの最小化はアプリとして最小化 ───────────────────
ctx = _func(puck_cls, "contextMenuEvent")
calls = {x.func.attr for x in ast.walk(ctx)
         if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)}
check("minimize_to_taskbar" in calls and "showMinimized" not in calls,
      "パック自身ではなくアプリを最小化する（%r）" % (sorted(calls),))

# ── 5) 畳む判断は «マウスがどこにあるか» 一本 ───────────────────
tick = _func(CM, "_tick")
calls = {x.func.attr for x in ast.walk(tick)
         if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)}
check("_typing" not in calls,
      "«入力中は畳まない» は外した（道具窓がアクティブでも畳める）")
check("_cursor_inside" in calls, "マウスの位置で決める")

# 実物でも: 道具窓がアクティブ・入力中でも、マウスが外なら畳む
sm.set(CompactController.ACTIVE_KEY, False, save=False)
w = MainWindow(settings_manager=sm)
w.resize(700, 500)
w.move(50, 50)
w.show()
QTest.qWait(150)
app.processEvents()
c = w._compact()
c.set_active(True)
QTest.qWait(120)
app.processEvents()
c._timer.stop()                 # 自動の監視は止めて、手で _tick を回す
c.expand()
QTest.qWait(120)
app.processEvents()
check(c.is_expanded(), "開いた")

from core.compat import QLineEdit, QDialog
dlg = QDialog(w)
dlg.setWindowTitle("tool")
le = QLineEdit(dlg)
dlg.resize(200, 80)
dlg.move(60, 60)
dlg.show()
le.setFocus()
app.processEvents()
QTest.qWait(80)
# マウスはどの窓からも離れている（オフスクリーンのカーソルは 0,0）
for _ in range(8):
    c._tick()
    QTest.qWait(40)
    app.processEvents()
check(not c.is_expanded(),
      "道具窓がアクティブ・入力中でも、マウスが外なら畳む")
dlg.close()
c.set_active(False)
app.processEvents()
w.close()
app.processEvents()

sm.set(CompactController.ACTIVE_KEY, False, save=False)
finish(not fails)
