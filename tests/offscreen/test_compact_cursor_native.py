# -*- coding: utf-8 -*-
"""r146: «カーソルが本体の上か» は Windows では物理ピクセルで判定する。

ユーザー報告 2026-10-16:
  「コンパクト状態でディスプレイを跨いでいる場合、ウィンドウの上に
    マウスがあっても、別ディスプレイにマウスが移動するとコンパクトに
    戻ってしまいます。」

画面ごとに DPI が違うと、Qt の QCursor.pos() と frameGeometry() は画面ごとに
別の論理座標になり、跨いだ窓の向こう側が «外» に見える。
core.win_topmost.cursor_inside_any（GetCursorPos / GetWindowRect）で物理
ピクセル同士を比べる。Windows 以外（None）では従来の Qt 計算に落ちる。
MFM_DIAG=no_native_cursor で従来計算だけに戻せる（切り分け用）。
"""
import os

from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest
from core.compat import QWidget, QRect, QPoint
from ui.compact_mode import CompactController
import ui.compact_mode as _cm
import core.win_topmost as _wt

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
win = _Framed()
win.setGeometry(150, 150, 300, 200)
win.show()
QTest.qWait(60)
app.processEvents()
ctl = CompactController(win, sm)

# Qt の計算では «外» になる場所にカーソルを置く
_cm.QCursor.pos = staticmethod(lambda: QPoint(5000, 5000))
check(not ctl._cursor_inside(), "前提: Qt の矩形計算では外")

# 1) 物理ピクセル判定が «中» と言えば中（跨いだ窓の向こう側）
seen = []
_wt.cursor_inside_any = lambda wins, m=0: (seen.append((len(wins), m)), True)[1]
check(ctl._cursor_inside() is True, "物理ピクセル判定が中なら中（Qt の計算より優先）")
check(seen and seen[-1][1] == ctl.MARGIN, "余白もそのまま渡す（%r）" % (seen,))

# 2) 物理ピクセル判定が «外» なら、Qt が中と言っても外
_cm.QCursor.pos = staticmethod(lambda: QPoint(200, 200))
_wt.cursor_inside_any = lambda wins, m=0: False
check(ctl._cursor_inside() is False, "物理ピクセル判定が外なら外")

# 3) 判定できない（None）なら Qt の計算に落ちる
_wt.cursor_inside_any = lambda wins, m=0: None
check(ctl._cursor_inside() is True, "判定不能なら従来の Qt 計算（中）")
_cm.QCursor.pos = staticmethod(lambda: QPoint(5000, 5000))
check(ctl._cursor_inside() is False, "判定不能なら従来の Qt 計算（外）")

# 4) 切り分けスイッチで従来計算だけに戻る
_wt.cursor_inside_any = lambda wins, m=0: True
os.environ["MFM_DIAG"] = "no_native_cursor"
try:
    from core import diag as _diag
    for name in ("_cache", "_CACHE", "_off", "_OFF"):
        if hasattr(_diag, name):
            try:
                getattr(_diag, name).clear()
            except Exception:
                setattr(_diag, name, None)
    check(ctl._cursor_inside() is False,
          "MFM_DIAG=no_native_cursor で従来計算だけになる")
finally:
    os.environ.pop("MFM_DIAG", None)

# 5) 本物の cursor_inside_any は Windows 以外で None（Linux の回帰スイート）
import importlib
importlib.reload(_wt)
r = _wt.cursor_inside_any([win], 4)
from core.win_topmost import is_windows
check((r is None) if not is_windows() else (r in (True, False)),
      "本物: Windows 以外は None、Windows は真偽（%r）" % (r,))

finish(not fails)
