# -*- coding: utf-8 -*-
"""r127: サイズ調整バーは «乗せ続けた時» だけ出す。

ユーザー指示 2026-10-07:
  「サムネ表示切り替えボタンへのマウスオーバーでサイズ変更するバーが
    でる時間を伸ばしてください。少しでもマウスが上を通ると、バーが出て
    邪魔なので、明確に変えたいという意思のもとカーソルを合わせる時間で
    表示するようにしてください」

従来は Enter で即座に開いていた。通りすがりでも開いてしまう。

ここで固定すること:
  1) Enter しただけでは開かない（予約が入るだけ）
  2) 予約の時間はツールチップ（約 500ms）より長い＝意思が要る長さ
  3) 時間内に離れたら開かない（予約は取り消される）
  4) ▦ を押した時は予約を捨てる（モード切替の邪魔をしない）
  5) 予約が切れた時にカーソルが乗っていなければ開かない
"""
from _common import *  # noqa: F401,F403
from _common import finish, app

from core.compat import QtCore, QWidget
import ui.browser_column_view as bcv

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


class _Popup:
    def __init__(self):
        self.shown = 0
        self.hidden = 0

    def show_for(self, view, btn):
        self.shown += 1

    def request_hide(self):
        self.hidden += 1


class _Owner:
    def __init__(self):
        self.popup = _Popup()

    def size_popup(self):
        return self.popup


btn = QWidget()
btn.resize(24, 24)
_keep.append(btn)
owner = _Owner()
h = bcv._SizeButtonHover(owner, None, btn)

check(bcv._SizeButtonHover.HOVER_DELAY_MS > 500,
      "予約時間はツールチップ（約500ms）より長い（%dms）"
      % bcv._SizeButtonHover.HOVER_DELAY_MS)


def ev(kind):
    return QtCore.QEvent(kind)


# ── 1) Enter しただけでは開かない ────────────────────────────────────
h.eventFilter(btn, ev(QtCore.QEvent.Enter))
app.processEvents()
check(owner.popup.shown == 0, "Enter した直後は開かない（%d 回）" % owner.popup.shown)
check(h._timer.isActive(), "Enter で予約が入る")

# ── 2) 時間内に離れたら開かない ──────────────────────────────────────
QTest.qWait(int(bcv._SizeButtonHover.HOVER_DELAY_MS * 0.4))
h.eventFilter(btn, ev(QtCore.QEvent.Leave))
app.processEvents()
check(not h._timer.isActive(), "離れたら予約は取り消される")
QTest.qWait(bcv._SizeButtonHover.HOVER_DELAY_MS + 200)
app.processEvents()
check(owner.popup.shown == 0,
      "通りすがりでは最後まで開かない（%d 回）" % owner.popup.shown)
check(owner.popup.hidden >= 1, "離れた時は閉じる要求を出す")

# ── 3) 押した時は予約を捨てる ────────────────────────────────────────
h.eventFilter(btn, ev(QtCore.QEvent.Enter))
check(h._timer.isActive(), "前提: 予約が入っている")
h.eventFilter(btn, ev(QtCore.QEvent.MouseButtonPress))
check(not h._timer.isActive(), "▦ を押したら予約を捨てる（モード切替の邪魔をしない）")

# ── 4) 予約が切れてもカーソルが乗っていなければ開かない ───────────────
before = owner.popup.shown
h._open_now()            # カーソルはボタンの上にない（オフスクリーン）
app.processEvents()
check(owner.popup.shown == before,
      "予約が切れてもカーソルが乗っていなければ開かない（%d → %d）"
      % (before, owner.popup.shown))

finish(not fails)
