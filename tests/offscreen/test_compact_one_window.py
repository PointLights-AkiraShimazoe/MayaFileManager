# -*- coding: utf-8 -*-
"""r146 後半: コンパクトは «窓 1 つ・タスクバーのボタン 1 つ» で通す。

ユーザー報告 2026-10-14（スクリーンショット付き）:
  「コンパクトモードでコンパクトのまま閉じ、再度起動すると、画像の様に
    2つ存在し、通常ウィンドウはマウスオーバー関係無しにでたままです。」
  「タスクバーも、コンパクトと通常が切り替わる度に消えて出てを
    繰り返していてみっともないです。」

原因は «畳む＝本体を hide()» にしていたこと。
  * タスクバーのボタンはウィンドウ 1 つにつき 1 個なので、本体を隠して
    パックを出すと、ボタンも消えて出る（点滅）。
  * 隠れた本体は «閉じる口» が無い。パックを閉じてもプロセスが残り、
    次の起動が «既にいる方を前に出す» 経路に入って両方出た。

直し方は «畳む＝最小化»。ボタンは常に本体のものが 1 つだけ居座る。

ここで固定すること:
  1) 畳んだら本体は «最小化»（隠すのではない）、パックが出る
  2) パックはタスクバーに出さない（ボタンは本体のものだけ）
  3) 最小化から戻されたら «開いた» 扱いにする（2 つ出たまま止まらない）
  4) 本体を閉じたらパックも閉じる（プロセスが残らない）
  5) 起動時にコンパクトを復元しても «両方出ている» にならない
"""
import ast
import os

from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest

from ui.main_window import MainWindow
from ui.compact_mode import CompactController

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src(*parts):
    return ast.parse(open(os.path.join(ROOT, *parts), encoding="utf-8").read())


def _func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


# ── 1) 畳んだら最小化 ───────────────────────────────────────────
sm.set(CompactController.ACTIVE_KEY, False, save=False)
w = MainWindow(settings_manager=sm)
w.resize(900, 600)
w.show()
QTest.qWait(200)
app.processEvents()

c = w._compact()
c.set_active(True)
QTest.qWait(200)
app.processEvents()
check(c.is_active() and not c.is_expanded(), "畳んだ状態になっている")
check(w.isMinimized(), "本体は «最小化»（hide ではない）")
check(c._puck is not None and c._puck.isVisible(), "パックが出ている")

# 2) パックはタスクバーに出さない（呼び方を固定する）
cm = _src("ui", "compact_mode.py")
puck_init = None
for n in ast.walk(cm):
    if isinstance(n, ast.ClassDef) and n.name == "_Puck":
        puck_init = _func(n, "__init__")
calls = [x for x in ast.walk(puck_init or ast.Module(body=[], type_ignores=[]))
         if isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
         and x.func.id == "set_taskbar_button"]
check(len(calls) == 1, "パックはタスクバーの扱いを 1 か所で決める（%d）" % len(calls))
if calls:
    arg = calls[0].args[1] if len(calls[0].args) > 1 else None
    check(isinstance(arg, ast.Constant) and arg.value is False,
          "パックにタスクバーのボタンを付けない")

col = _func(cm, "collapse")
hides = [x for x in ast.walk(col or ast.Module(body=[], type_ignores=[]))
         if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
         and x.func.attr == "hide"
         and isinstance(x.func.value, ast.Attribute)
         and x.func.value.attr == "_win"]
check(not hides, "畳む時に本体を hide() しない（タスクバーから消える）")

# ── 3) 最小化から戻されたら «開いた» 扱い ───────────────────────
w.showNormal()
QTest.qWait(150)
app.processEvents()
w._compact_follow_window_state()
app.processEvents()
check(c.is_expanded(), "最小化から戻したら開いた扱いになる")
check(c._puck is None or not c._puck.isVisible(), "その時パックは引っ込む")

# ── 4) 閉じたらパックも閉じる ───────────────────────────────────
# （コンパクトは入ったまま。開いている状態から畳み直す）
c.collapse()
QTest.qWait(150)
app.processEvents()
check(c._puck is not None and c._puck.isVisible(), "もう一度畳める")
w.close()
app.processEvents()
check(c._puck is None, "本体を閉じたらパックも手放す")

mw = _src("ui", "main_window.py")
ce = _func(mw, "closeEvent")
closes = [x for x in ast.walk(ce or ast.Module(body=[], type_ignores=[]))
          if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
          and x.func.attr == "close_puck"]
check(bool(closes), "closeEvent がパックを閉じる")

# ── 5) 起動時の復元で «両方出ている» にならない ─────────────────
sm.set(CompactController.ACTIVE_KEY, True, save=False)
w2 = MainWindow(settings_manager=sm)
w2.resize(900, 600)
w2.show()
QTest.qWait(600)
app.processEvents()
c2 = w2._compact()
both = (w2.isVisible() and not w2.isMinimized()
        and c2._puck is not None and c2._puck.isVisible())
check(not both, "本体とパックが «両方出たまま» にならない")
check(c2.is_active(), "コンパクトは復元されている")
check(w2.isMinimized() or not w2.isVisible(), "本体は表に出ていない")
w2.close()
app.processEvents()

sm.set(CompactController.ACTIVE_KEY, False, save=False)
finish(not fails)
