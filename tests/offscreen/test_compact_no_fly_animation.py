# -*- coding: utf-8 -*-
"""r146: 畳んでいる間は本体の最小化／復元アニメーションを止める。

ユーザー報告 2026-10-16:
  「コンパクト状態でタスクバーを押すとコンパクトは光るのは正しいが、
    何か他の UI が画面外へ飛んでいくさまが見える。もう一度押すと戻ってくる」

畳んでいる間、本体は画面の外に置いてある。タスクバーのボタンは Windows が
最小化／復元を切り替えるので、DWM が «タスクバー ⇔ 画面外の矩形» を結ぶ
飛行アニメーションを描いていた。それが «飛んでいく UI» の正体。
退けている間だけ、この窓の遷移を切る（DWMWA_TRANSITIONS_FORCEDISABLED）。
開く時・コンパクトを抜ける時・画面へ引き戻す時には戻す。
"""
import os

from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest
from core.compat import QWidget, QRect
from ui.compact_mode import CompactController
import ui.compact_mode as _cm

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


# Win32 呼び出しを記録する（Windows 以外では本物は何もしない）
calls = []
import core.win_topmost as _wt
_orig = _wt.set_window_transitions
_wt.set_window_transitions = lambda w, on: (calls.append(bool(on)), _orig(w, on))[1]

sm.set(CompactController.ACTIVE_KEY, False, save=False)
sm.set(CompactController.PUCK_POS_KEY, None, save=False)
win = _Framed()
win.setGeometry(150, 150, 300, 200)
win.show()
QTest.qWait(80)
app.processEvents()

ctl = CompactController(win, sm)
ctl.set_active(True)                  # 畳む
QTest.qWait(80)
app.processEvents()
ctl._timer.stop()
check(ctl.is_parked(), "前提: 畳むと本体は画面の外")
check(calls and calls[-1] is False, "畳んだら遷移アニメーションを切る（%r）" % calls)

# タスクバーから起こされた相当（notice → 退け直し）: 切ったまま
n0 = len(calls)
ctl.notice()
QTest.qWait(50)
app.processEvents()
check(ctl.is_parked(), "notice しても本体は画面の外のまま")
check(True not in calls[n0:], "notice で遷移を戻さない（%r）" % calls[n0:])

# 開くと戻す
ctl._cursor_inside = lambda: True
ctl.expand()
QTest.qWait(50)
app.processEvents()
check(calls[-1] is True, "開いたら遷移アニメーションを戻す（%r）" % calls)

# もう一度畳んで、コンパクトを抜ける → 戻っている
ctl.collapse()
QTest.qWait(50)
app.processEvents()
check(calls[-1] is False, "畳み直すとまた切る")
ctl.set_active(False)
QTest.qWait(50)
app.processEvents()
check(calls[-1] is True, "コンパクトを抜けると戻す（%r）" % calls)
check(not ctl.is_parked(), "抜けた本体は画面の中")

# 同じ値の掛け直しはしない（冪等・安い）
n1 = len(calls)
ctl.pull_window_onto_screen()
check(len(calls) == n1, "既に戻っていれば掛け直さない")

finish(not fails)
