# -*- coding: utf-8 -*-
"""r146: パックはどの画面にも入っていなければ «一番近い画面» の中へ。

ユーザー指示 2026-10-16:
  「コンパクトが起動時にどのディスプレイにも入っていない場合は、一番近い
    ところに強制的にズレるという仕様が必要そうです。通常ウィンドウで
    コンパクトになる位置が画面外にでている状況でコンパクトになったり、
    閉じたりすると、次回触れなくなるので。」

固定すること:
  1) 保存されたパック位置が画面外 → 起動時に一番近い画面の中へ（左上へ飛ばない）
  2) 本体の角が画面外にある状態で畳む → パックは丸ごと見える場所に出る
  3) 半分はみ出した位置へ置いて離す → 中へ戻る
  4) 一番近い画面の判定は主画面ではなく距離で決まる
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest
from core.compat import QWidget, QRect, QPoint, QSize
from ui.compact_mode import CompactController

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


class _Framed(QWidget):
    B, T = 8, 31

    def frameGeometry(self):
        g = self.geometry()
        return QRect(g.left() - self.B, g.top() - self.T,
                     g.width() + self.B * 2, g.height() + self.T + self.B)

    def sync_compact_button(self):
        pass

    def _log_quiet(self, *_a):
        pass

    def is_always_on_top(self):
        return False

    def save_window_geometry(self):
        pass


scr = app.primaryScreen().availableGeometry()


def inside(puck):
    return scr.contains(puck.frameGeometry())


# ── 1) 保存位置が画面外 → 一番近い所へ ─────────────────────────
sm.set(CompactController.ACTIVE_KEY, False, save=False)
sm.set(CompactController.PUCK_POS_KEY, [scr.right() + 300, scr.top() + 200], save=False)
win = _Framed()
win.setGeometry(150, 150, 300, 200)
win.show()
QTest.qWait(60)
app.processEvents()
ctl = CompactController(win, sm)
puck = ctl._ensure_puck()
QTest.qWait(30)
app.processEvents()
check(inside(puck), "画面外に保存されていたパックは画面の中へ（%r）" % (puck.pos(),))
check(puck.pos().y() == scr.top() + 200,
      "一番近い場所（右端・同じ高さ）に寄せる。左上へ飛ばない（%r）" % (puck.pos(),))
check(puck.pos().x() == scr.right() - puck.width() + 1 or
      puck.frameGeometry().right() == scr.right(),
      "右端にぴったり付く（%r / 画面右端 %d）" % (puck.frameGeometry().right(), scr.right()))

# ── 2) 本体の角が画面外にある状態で畳む → 見える場所に ───────────
sm.set(CompactController.PUCK_POS_KEY, None, save=False)
win.setGeometry(scr.right() - 60, scr.bottom() - 40, 300, 200)   # 右下へはみ出す
QTest.qWait(30)
app.processEvents()
ctl.set_active(True)
QTest.qWait(80)
app.processEvents()
ctl._timer.stop()
puck = ctl._puck
check(puck is not None and puck.isVisible(), "前提: 畳めている")
check(inside(puck), "本体がはみ出していてもパックは丸ごと見える（%r）" % (puck.frameGeometry(),))
saved = sm.get(CompactController.PUCK_POS_KEY)
check(bool(saved) and scr.contains(QRect(QPoint(*saved), puck.size())),
      "保存されるパック位置も画面の中（%r）" % (saved,))

# 開くと本体は元の（はみ出した）位置に戻る
ctl._cursor_inside = lambda: True
ctl.expand()
QTest.qWait(50)
app.processEvents()
check(win.geometry() == QRect(scr.right() - 60, scr.bottom() - 40, 300, 200),
      "本体は（はみ出したまま）元の位置に戻る（%r）" % (win.geometry(),))

# ── 3) 半分はみ出した場所へ置いて離す → 中へ戻る ────────────────
ctl.collapse()
QTest.qWait(50)
app.processEvents()
puck = ctl._puck
puck.move(scr.left() - puck.width() // 2, scr.top() + 300)
puck.grip._from = QPoint(0, 0)
from core.compat import QtGui, QtCore
puck.grip.mouseReleaseEvent(QtGui.QMouseEvent(
    QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(0, 0),
    QtCore.QPointF(puck.mapToGlobal(QPoint(0, 0))),
    QtCore.Qt.LeftButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
app.processEvents()
check(inside(puck) and puck.pos().x() == scr.left(),
      "はみ出して離すと左端まで戻る（%r）" % (puck.pos(),))

# ── 4) 一番近い画面の判定（距離） ──────────────────────────────
near = CompactController._nearest_screen(QRect(scr.right() + 10, scr.top() + 10, 1, 1))
check(near is not None, "画面外の点にも一番近い画面が返る")
far_pt = QRect(scr.right() + 5000, scr.bottom() + 5000, 1, 1)
check(CompactController._nearest_screen(far_pt) is not None, "遠くても必ずどれかの画面")

finish(not fails)
