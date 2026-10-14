# -*- coding: utf-8 -*-
"""r146 後半: コンパクト⇔通常を繰り返しても位置がずれない／つまみは掴める。

ユーザー報告 2026-10-14:
  「つまみをクリックしたらドラッグ出来ずに通常ウィンドウが開きます。」
  「コンパクト⇔通常を繰り返すだけで、どんどん UI が左上に上がっていきます。
    閉じる起点と開く起点が合っていない証拠ではないでしょうか。」

原因は 2 つ。
  1) パックの «ウィンドウがアクティブになったら開く» が残っていた。
     つまみを押した瞬間にアクティブ化が走り、ドラッグが始まる前に開く。
     （パックにタスクバーのボタンを出していた頃の名残。もう要らない）
  2) 畳む時は frameGeometry（枠込み）の左上を基準にパックを置くのに、
     開く時は move() で動かしていた。この 2 つが «枠» を指すか «中身» を
     指すかは Qt の版と «今見えているか» で変わるので、往復のたびに
     タイトルバーの高さぶんずれていった。
     → **変換に頼らない。** 畳む時に中身の矩形をそのまま覚え、開く時は
       それを戻す（パックを動かされていたらその差分だけずらす）。

ここで固定すること:
  1) パックはアクティブ化では開かない（つまみを掴める）
  2) 畳む→開く を何度繰り返しても本体の位置が 1px も動かない
  3) パックを動かしたら、その分だけ本体もずれて開く
"""
import ast
import os

from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest

from core.compat import QWidget, QRect, QPoint
from ui.compact_mode import CompactController

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


# ── 1) パックはアクティブ化では開かない ─────────────────────────
puck_cls = None
for n in ast.walk(CM):
    if isinstance(n, ast.ClassDef) and n.name == "_Puck":
        puck_cls = n
check(_func(puck_cls, "event") is None,
      "パックは «アクティブになったら開く» を持たない（つまみを掴める）")
names = {x.attr for x in ast.walk(puck_cls) if isinstance(x, ast.Attribute)}
check("WindowActivate" not in names,
      "アクティブ化を見ていない（%r）" % sorted(n for n in names if "Activ" in n))
import ui.compact_mode as _cm
check(not hasattr(_cm._Puck, "event") or
      "event" not in vars(_cm._Puck),
      "実物の _Puck にも event が無い")
check("mousePressEvent" in vars(_cm._Grip), "つまみは押下を自分で受ける")

# ── 2) 位置合わせは «枠の左上» ───────────────────────────────────
f = _func(CM, "collapse")
remembers = {x.attr for x in ast.walk(f) if isinstance(x, ast.Attribute)}
check("_win_rect" in remembers and "_puck_anchor" in remembers,
      "畳む時に «本体の矩形» と «パックの位置» を覚える")
f = _func(CM, "expand")
calls = {x.func.attr for x in ast.walk(f)
         if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)}
check("_restore_window_at_puck" in calls, "開く時は覚えた矩形へ戻す")
check("move" not in calls, "開く時に座標を計算し直さない（ずれの元）")


class _Framed(QWidget):
    """タイトルバーと枠を持つ窓のふり（オフスクリーンには装飾が無いため）。"""
    B, T = 8, 31                      # 左右の枠 / タイトルバーの高さ

    def frameGeometry(self):
        g = self.geometry()
        return QRect(g.left() - self.B, g.top() - self.T,
                     g.width() + self.B * 2, g.height() + self.T + self.B)

    def sync_compact_button(self):
        pass

    def _log_quiet(self, *_a):
        pass


# ── 3) 畳む→開く を繰り返しても動かない ─────────────────────────
sm.set(CompactController.ACTIVE_KEY, False, save=False)
sm.set(CompactController.PUCK_POS_KEY, None, save=False)
win = _Framed()
win.setGeometry(150, 150, 300, 200)
win.show()
QTest.qWait(80)
app.processEvents()

ctl = CompactController(win, sm)
ctl.set_active(True)                  # 1 回目の畳み
QTest.qWait(80)
app.processEvents()
# 見張り（60ms ごとに «カーソルが外なら畳む»）は手動の往復と喧嘩するので止める
ctl._timer.stop()
ctl.expand()
QTest.qWait(40)
app.processEvents()
first = win.geometry()

for i in range(6):
    ctl.collapse()
    QTest.qWait(30)
    app.processEvents()
    ctl.expand()
    QTest.qWait(30)
    app.processEvents()
    now = win.geometry()
    check(now == first,
          "%d 往復しても本体の位置と大きさが変わらない（%r → %r）"
          % (i + 1, (first.x(), first.y()), (now.x(), now.y())))
    if now != first:
        break

# パックを動かしたら、その差分だけ本体もずれて開く
ctl.collapse()
QTest.qWait(30)
app.processEvents()
before_puck = ctl._puck.pos()
ctl._puck.move(before_puck.x() + 37, before_puck.y() + 23)
app.processEvents()
ctl.expand()
QTest.qWait(30)
app.processEvents()
check(win.geometry().topLeft() == QPoint(first.left() + 37, first.top() + 23),
      "パックを動かした分だけ本体もずれる（%r）" % (win.geometry().topLeft(),))

ctl._timer.stop()
ctl.set_active(False)
app.processEvents()
sm.set(CompactController.ACTIVE_KEY, False, save=False)
finish(not fails)
