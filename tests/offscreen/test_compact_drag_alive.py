# -*- coding: utf-8 -*-
"""r146: コンパクト中に Manager から外へドラッグしても、ドラッグは死なない。

ユーザー報告 2026-10-16:
  「コンパクトモードのとき、manager 外への D&D が死んでいます。
    コンパクトに戻しつつも、生きたままにしてください。ドロップした後は
    逆に manager のアクティブな状況は残さずコンパクト化したままで OK です。」

原因: カーソルが外へ出た tick で collapse() が走り、その中の
_return_foreground()（元のアプリへ SetForegroundWindow）で OLE の
ドラッグがマウスの捕捉を失って終わっていた。

仕様:
  * 自分のドラッグ（mfm_drag_active）の最中にカーソルが外へ出たら
    畳む（本体を画面の外へ・パックを出す）が、フォアグラウンドは触らない
  * 落とし終えたら、フォアグラウンドがこちらに残っていれば落とし先
    （カーソルの下の他プロセスの窓）へ返す。本体は開かない
  * カーソルが中にあるうちは畳まない
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest
from core.compat import QWidget, QRect, QPoint
from ui.compact_mode import CompactController
import ui.compact_mode as _cm
from ui.browser_util import mfm_drag_begin, mfm_drag_end, mfm_drag_active

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


class _Framed(QWidget):
    def sync_compact_button(self):
        pass

    def _log_quiet(self, *_a):
        pass

    def is_always_on_top(self):
        return False

    def save_window_geometry(self):
        pass


sm.set(CompactController.ACTIVE_KEY, False, save=False)
sm.set(CompactController.PUCK_POS_KEY, None, save=False)
win = _Framed()
win.setGeometry(150, 150, 300, 200)
win.show()
QTest.qWait(80)
app.processEvents()

ctl = CompactController(win, sm)
ctl.set_active(True)
QTest.qWait(80)
app.processEvents()
ctl._timer.stop()             # 見張りは手で回す
ctl._cursor_inside = lambda: True
ctl.expand()
QTest.qWait(80)
app.processEvents()
check(ctl.is_expanded() and not ctl.is_parked(), "前提: 開いている")

returned = []
ctl._return_foreground = lambda: returned.append("return")
handed = []
import core.win_topmost as _wt
_orig = (_wt.app_is_foreground, _wt.set_foreground, _wt.foreign_window_at_cursor)
_wt.app_is_foreground = lambda: True
_wt.set_foreground = lambda h: (handed.append(int(h)), True)[1]
_wt.foreign_window_at_cursor = lambda: 0

# ── ドラッグ開始。カーソルは中 → 畳まない ────────────────────────
mfm_drag_begin()
check(mfm_drag_active(), "前提: 自分のドラッグ中")
ctl._tick()
check(ctl.is_expanded(), "ドラッグ中でもカーソルが中なら畳まない")

# ── カーソルが外へ → 畳むが、フォアグラウンドは触らない ───────────
ctl._cursor_inside = lambda: False
ctl._tick()
QTest.qWait(50)
app.processEvents()
check(not ctl.is_expanded() and ctl.is_parked(), "外へ出たら畳む（本体は画面の外）")
check(ctl._puck is not None and ctl._puck.isVisible(), "パックが出る")
check(not returned, "ドラッグ中はフォアグラウンドを返さない（OLE が切れる）")
check(ctl._collapsed_in_drag, "«ドラッグ中に畳んだ» を覚えている")
# ドラッグが続いている間、何度 tick しても触らない
ctl._tick(); ctl._tick()
check(not returned and not handed, "続く tick でも触らない")

# ── 落とし終えた → 落とし先へフォアグラウンドを返す。本体は開かない ──
_wt.foreign_window_at_cursor = lambda: 4242
mfm_drag_end()
ctl._tick()
QTest.qWait(50)
app.processEvents()
check(not ctl.is_expanded() and ctl.is_parked(), "落とした後も畳んだまま（開かない）")
check(handed == [4242], "落とし先（カーソルの下の窓）へフォアグラウンドを返す（%r）" % handed)
check(not ctl._collapsed_in_drag, "後始末は 1 回だけ")
ctl._tick()
check(handed == [4242], "次の tick では何もしない")

# ── こちらが前にいなければ触らない ───────────────────────────────
ctl._cursor_inside = lambda: True
ctl.expand()
QTest.qWait(50); app.processEvents()
mfm_drag_begin()
ctl._cursor_inside = lambda: False
ctl._tick()
_wt.app_is_foreground = lambda: False
handed[:] = []
mfm_drag_end()
ctl._tick()
check(not handed, "こちらが前にいなければフォアグラウンドに触らない")
check(not ctl.is_expanded(), "やはり畳んだまま")

# ── 落とし先が自分の窓（取れない）なら、開く前にいたアプリへ ──────
ctl._cursor_inside = lambda: True
ctl.expand()
QTest.qWait(50); app.processEvents()
ctl._prev_foreground = 777
mfm_drag_begin()
ctl._cursor_inside = lambda: False
ctl._tick()
_wt.app_is_foreground = lambda: True
_wt.foreign_window_at_cursor = lambda: 0
handed[:] = []
mfm_drag_end()
ctl._tick()
check(handed == [777], "落とし先が取れなければ元のアプリへ返す（%r）" % handed)

# ── 原文の検査: ドラッグの exec を旗で挟んでいる ────────────────────
import os, re
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
for rel in ("ui/browser_column_view.py", "ui/flat_column.py"):
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    check("mfm_drag_begin" in src and "mfm_drag_end" in src,
          "%s: ドラッグの exec を mfm_drag_begin/end で挟む" % rel)

_wt.app_is_foreground, _wt.set_foreground, _wt.foreign_window_at_cursor = _orig
finish(not fails)
