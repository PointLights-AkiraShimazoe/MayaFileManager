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

最小化も駄目だった（ユーザー報告 2026-10-14「マウスオーバー時の通常
ウィンドウへの展開方法が間違っています」）。Windows の復帰アニメーションが
走り、しかも最小化から戻す操作は必ずフォアグラウンドを奪うので、
«パックのいた場所にすっと開く / フォーカスを奪わない» を両方壊す。

直し方は «畳む＝画面の外へ退ける»。見えたままなのでボタンは居座り、
開くのは move() 1 回で済む。

ここで固定すること:
  1) 畳んだら本体は «画面の外»（隠さない・最小化しない）、パックが出る
  2) 開く時は show() をやり直さない（＝アニメーションもフォーカス奪取も無い）
  3) パックはタスクバーに出さない（ボタンは本体のものだけ）
  4) タスクバーから起こされたら «開いた» 扱いにする（2 つ出たまま止まらない）
  5) 本体を閉じたらパックも閉じる（プロセスが残らない）
  6) 起動時にコンパクトを復元しても «両方出ている» にならない
"""
import ast
import os

from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest
from core.compat import Qt, QRect, QPoint

from ui.main_window import MainWindow
from ui.compact_mode import CompactController

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src_text(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def _src(*parts):
    return ast.parse(_src_text(*parts))


def _inside_if(func, call):
    """その呼び出しが if の中に入っているか（無条件に走らないか）。"""
    for n in ast.walk(func):
        if isinstance(n, ast.If):
            for x in ast.walk(n):
                if x is call:
                    return True
    return False


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
check(c.is_parked(), "本体は画面の外へ退けてある")
check(not w.isMinimized(), "最小化はしない（戻す時にアニメーションと"
      "フォーカス奪取が付いてくる）")
check(w.isVisible(), "隠さない（タスクバーのボタンを消さないため）")
check(c._puck is not None and c._puck.isVisible(), "パックが出ている")

# 2) 開く時は show() をやり直さない（位置を戻すだけ）
col_src = _src_text("ui", "compact_mode.py")
exp = _func(_src("ui", "compact_mode.py"), "expand")
shows = [x for x in ast.walk(exp)
         if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
         and x.func.attr in ("show", "showNormal")]
for x in shows:
    check(_inside_if(exp, x),
          "開く時の show は «隠れていた時だけ» に限る（%s）" % x.func.attr)

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

# ── 3) タスクバーから起こされても «開かない»。パックを光らせる ──
# ユーザー指示 2026-10-15:
#   「タスクバーをコンパクトでクリックすると通常ウィンドウは一瞬でるだけで
#     バグに見えるので、コンパクトのままオレンジ色なりの蛍光色を一度
#     ユーザーに視認させるために光らせるでお願いします。」
c._collapsed_at = 0.0           # 畳んだ直後の猶予を明けておく
w.activateWindow()
QTest.qWait(150)
app.processEvents()
w._compact_follow_window_state()
app.processEvents()
check(not c.is_expanded(), "起こされても開かない（一瞬出て消えない）")
check(c.is_parked(), "本体は画面の外のまま")
check(c._puck is not None and c._puck.isVisible(), "パックは出たまま")
check(getattr(c._puck, "_flash_timer", None) is not None,
      "パックが光って «ここに居ます» と知らせる")
from core.theme_engine import qss_vars as _qv
warn = _qv().get(c._puck.FLASH_KEY, "")
qss = c._puck.styleSheet()
check(bool(warn) and warn in qss,
      "光る色はテーマの注意色（%r / %r）" % (warn, qss[:70]))

# 開く道（アイコンに乗せる）はそのまま効く
c.expand()
app.processEvents()
check(c.is_expanded() and not c.is_parked(), "乗せれば今までどおり開く")

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
sm.set(CompactController.PUCK_POS_KEY, [120, 140], save=False)
# 保存されている本体の位置を «画面の中の普通の場所» にしておく
# （オフスクリーンは x=1 のような端の位置を枠ぶん押し戻すため）
from core.compat import QWidget as _QW
_probe = _QW()
_probe.setGeometry(120, 90, 560, 400)
_probe.show()
app.processEvents()
sm.set("window_geometry", _probe.saveGeometry().toHex().data().decode(), save=False)
_probe.close()
w2 = MainWindow(settings_manager=sm)
# A-12: 出す前に退けてあり、出しても活性化しない
check(w2._compact().is_parked(), "出す前から本体は画面の外（一瞬も見せない）")
check(w2.testAttribute(Qt.WA_ShowWithoutActivating), "出してもフォーカスを奪わない")
before_rect = QRect(w2._compact()._win_rect) if w2._compact()._win_rect else None
check(before_rect is not None and app.primaryScreen().availableGeometry().intersects(before_rect),
      "開く時に戻す矩形は «退ける前» の画面内の位置（%r）" % (before_rect,))
w2.show()
QTest.qWait(600)
app.processEvents()
c2 = w2._compact()
both = (not c2.is_parked()
        and c2._puck is not None and c2._puck.isVisible())
check(not both, "本体とパックが «両方出たまま» にならない")
check(c2.is_active(), "コンパクトは復元されている")
check(c2.is_parked(), "本体は表に出ていない（画面の外）")
check(c2._puck is not None and c2._puck.pos() == QPoint(120, 140),
      "パックは覚えていた場所に出る（%r）" % (c2._puck.pos() if c2._puck else None,))
check(getattr(c2._puck, "_flash_timer", None) is not None, "出た直後に光る")
c2._cursor_inside = lambda: True
c2.expand()
QTest.qWait(100)
app.processEvents()
check(w2.geometry() == before_rect,
      "開くと退ける前の位置に戻る（%r → %r）" % (before_rect, w2.geometry()))
# A-12 再（ユーザー報告 2026-10-16「展開するとコンパクトのボタンが勝手に
# OFF になる」）: 安全弁の時間内に開いても、コンパクトは解除されない
c2._pre_park_safety()
app.processEvents()
check(c2.is_active(), "安全弁の後もコンパクトは有効のまま（開いただけ）")
check(c2.is_expanded() and not c2.is_parked(), "本体は開いたまま")
# 本当に失敗した時（畳んでいるのにパックが無い）だけ介入する
c2.collapse()
QTest.qWait(50)
app.processEvents()
c2._puck.hide()
c2._pre_park_safety()
app.processEvents()
check(not c2.is_active() and not c2.is_parked(),
      "畳んだのにパックが無ければコンパクトを諦めて本体を画面へ")
w2.close()
app.processEvents()

# ── 7) 見失わないこと（r146、ユーザー報告 2026-10-15）──────────
#   「起動時に画面外に出て戻ってこなくなりました。」
# r146（ユーザー指示 2026-10-16、A-12「一瞬本体がでてからコンパクトになる」）:
# «出す前に退ける»（pre_park）は戻した。その代わり **安全弁** を必須にする:
#   pre_park が一定時間後にパックの有無を確かめ、無ければ本体を引き戻す。
mwt = _src("ui", "main_window.py")
init = _func(mwt, "__init__")
pre = [x for x in ast.walk(init or ast.Module(body=[], type_ignores=[]))
       if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
       and x.func.attr == "pre_park"]
check(bool(pre), "起動時に «出す前に退ける»（本体を一瞬見せない）")
cm_tree = _src("ui", "compact_mode.py")
pp = _func(cm_tree, "pre_park")
pp_calls = {x.func.attr for x in ast.walk(pp)
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)}
check("singleShot" in pp_calls and "_pre_park_safety" in
      {getattr(a, "attr", None) for x in ast.walk(pp) if isinstance(x, ast.Call)
       for a in x.args},
      "pre_park は安全弁（_pre_park_safety）を予約する")
saf = _func(cm_tree, "_pre_park_safety")
saf_calls = {x.func.attr for x in ast.walk(saf)
             if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)}
check("pull_window_onto_screen" in saf_calls,
      "安全弁はパックが無ければ本体を画面へ引き戻す")
rest = _func(mwt, "_restore_compact_mode")
calls = {x.func.attr for x in ast.walk(rest)
         if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)}
check("pull_window_onto_screen" in calls,
      "復元に失敗したら必ず画面の中へ引き戻す（%r）" % (sorted(calls),))

# 実際に «パックが出せなかった» 時、本体が画面の中に戻る
sm.set(CompactController.ACTIVE_KEY, True, save=False)
w3 = MainWindow(settings_manager=sm)
w3.resize(800, 500)
w3.show()
QTest.qWait(300)
app.processEvents()
c3 = w3._compact()
c3.set_active(True)
QTest.qWait(150)
app.processEvents()
c3._puck.hide()                 # パックが出せなかった状況を作る
c3.set_active(False)
c3.pull_window_onto_screen()
app.processEvents()
check(not c3.is_parked(), "引き戻せば本体は画面の中にいる")
w3.close()
app.processEvents()

# 透過はしない（全体が透けて見えづらくなる）
cm_init = None
for n in ast.walk(cm):
    if isinstance(n, ast.ClassDef) and n.name == "_Puck":
        cm_init = _func(n, "__init__")
trans = [x for x in ast.walk(cm_init)
         if isinstance(x, ast.Attribute) and x.attr == "WA_TranslucentBackground"]
check(not trans, "パックは透過しない（見えづらくなる）")

sm.set(CompactController.ACTIVE_KEY, False, save=False)
finish(not fails)
