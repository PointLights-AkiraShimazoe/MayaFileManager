# -*- coding: utf-8 -*-
"""r146: パックは «乗っていれば開く»（enterEvent に依らない）／
道具窓は畳む前の位置に戻る。

ユーザー報告 2026-10-16:
  A-5「畳んだとほぼ同時につまみにカーソルを乗せると、再度避けないと
      開かなくなります」
  A-7「畳まれた後に開いた場合、設定関連の UI だけ中央に戻ります。
      位置を維持してください」
  A-8「タスクバーから右クリックで閉じた場合、画面左上に出ます」
     「一つのディスプレイからはみ出すように通常画面が有る場合、コンパクト
      から開いた際に強制的にずらされます」

A-5: パックがカーソルの下に現れた時、Qt は enterEvent を出さない。
     つまみからアイコンへ動かしても enter が届かない場面があった。
     見張りの tick で座標を見て構える（enterEvent は補助に格下げ）。
A-7: QDialog は WA_Moved が無いと show のたびに親の中央へ置き直す。
     隠す前の矩形を覚え、戻す時に setGeometry で元の場所へ。
A-8: 畳んでいる間に終了すると本体の保存位置が «退け先（画面外）» になり、
     次の起動で Qt が左上へ引き戻す。畳む前に保存し、退け中は上書きしない。
     開く時は覚えた矩形をそのまま戻す（1 画面へ丸めない）。
"""
import os

from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest
from core.compat import QWidget, QRect, QDialog, QPoint
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

    saved = []

    def save_window_geometry(self):
        self.saved.append(QRect(self.geometry()))


sm.set(CompactController.ACTIVE_KEY, False, save=False)
sm.set(CompactController.PUCK_POS_KEY, None, save=False)
win = _Framed()
win.setGeometry(150, 150, 300, 200)
win.show()
QTest.qWait(80)
app.processEvents()

# 道具窓（設定ダイアログ相当）: 本体の子の QDialog を、中央ではない場所へ
dlg = QDialog(win)
dlg.resize(200, 120)
dlg.show()
QTest.qWait(50)
app.processEvents()
dlg.move(600, 420)            # ユーザーが動かした
QTest.qWait(50)
app.processEvents()
dlg_rect = QRect(dlg.geometry())
check(dlg.isVisible(), "前提: 道具窓が見えている")

ctl = CompactController(win, sm)
ctl.set_active(True)
QTest.qWait(80)
app.processEvents()
ctl._timer.stop()             # 見張りは手で回す
puck = ctl._puck
check(puck is not None and puck.isVisible(), "前提: パックが出ている")
check(not dlg.isVisible(), "畳むと道具窓も消える")

# ── A-5: enterEvent 無しでも «乗っていれば開く» ────────────────
opened = []
ctl.expand = lambda *a, **k: opened.append(True)
btn = puck.btn
# カーソルをアイコンの上に «置く»（enter は出さない）
_cm.QCursor.pos = staticmethod(lambda: btn.mapToGlobal(btn.rect().center()))
check(btn._cursor_on_me(), "前提: 座標上はアイコンに乗っている")
ctl._tick()                   # 見張り 1 回
check(btn._timer.isActive(), "tick で待ちが始まる（enterEvent 不要）")
ctl._tick(); ctl._tick()      # 何度 tick しても待ちは仕切り直さない
QTest.qWait(btn.HOVER_OPEN_MS + 120)
app.processEvents()
check(opened, "留まっていれば開く")

# 乗っていなければ待ちは止まる
opened[:] = []
_cm.QCursor.pos = staticmethod(lambda: QPoint(-5000, -5000))
ctl._tick()
btn._timer.start()
ctl._tick()
check(not btn._timer.isActive(), "乗っていなければ待ちを止める")

# ── A-7: 開いたら道具窓は元の場所 ──────────────────────────────
del ctl.expand                # 本物の expand に戻す
ctl._cursor_inside = lambda: True
ctl.expand()
QTest.qWait(80)
app.processEvents()
check(dlg.isVisible(), "開くと道具窓も戻る")
check(dlg.geometry() == dlg_rect,
      "道具窓は畳む前の位置のまま（%r → %r）"
      % ((dlg_rect.x(), dlg_rect.y()), (dlg.geometry().x(), dlg.geometry().y())))

# もう 1 往復
ctl.collapse()
QTest.qWait(50)
app.processEvents()
ctl.expand()
QTest.qWait(50)
app.processEvents()
check(dlg.geometry() == dlg_rect, "2 往復目も位置が変わらない")

# ── A-8a: 畳む時に «退ける前の» 位置を保存している ─────────────
on_screen = QRect(win.geometry())
ctl.collapse()
QTest.qWait(50)
app.processEvents()
check(ctl.is_parked(), "前提: 退けてある")
check(win.saved and win.saved[-1] == on_screen,
      "畳む時に退ける前の位置を保存する（%r）"
      % (((win.saved[-1].x(), win.saved[-1].y()) if win.saved else None),))
n_saved = len(win.saved)
ctl.notice()                   # 退け直し（保存し直さない）
QTest.qWait(30)
app.processEvents()
check(len(win.saved) == n_saved, "退けている最中は保存し直さない")
ctl.expand()
QTest.qWait(50)
app.processEvents()

# ── A-8b: 画面からはみ出す位置でも、開く時に丸めない ────────────
scr = app.primaryScreen().availableGeometry()
span = QRect(scr.right() - 120, scr.top() + 100, 300, 200)   # 右端を跨ぐ
win.setGeometry(span)
QTest.qWait(50)
app.processEvents()
check(win.geometry() == span, "前提: はみ出した位置に置けている（%r）" % ((win.geometry().x(), win.geometry().y()),))
ctl.collapse()
QTest.qWait(50)
app.processEvents()
ctl.expand()
QTest.qWait(50)
app.processEvents()
check(win.geometry() == span,
      "はみ出していても開いた位置はそのまま（%r → %r）"
      % ((span.x(), span.y()), (win.geometry().x(), win.geometry().y())))

finish(not fails)
