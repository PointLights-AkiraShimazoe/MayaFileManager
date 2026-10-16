# -*- coding: utf-8 -*-
"""r146: エリアバー（左端の縦ストリップ）のボタン配置。

ユーザー指示 2026-10-16（図付き、配置のみの変更）:
  ✕ が一番上、＋ が一番下、＋ の上に向かって ▼ ▲ の順。
  真ん中にエリア名（タブ列）と表示切替ボタン。
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest
from ui.main_window import MainWindow

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


sm.set("browser_areas_state", None, save=False)
sm.set(MainWindow.AREA_SIZES_KEY, None, save=False)
sm.set(MainWindow.AREA_VIEW_MODE_KEY, "split", save=False)
w = MainWindow(settings_manager=sm)
w.resize(1100, 700)
w.show()
QTest.qWait(300)
app.processEvents()
a = w._areas[0]
ys = {name: getattr(a, name).y() for name in
      ("_close_btn", "_title", "_mode_btn", "_up_btn", "_down_btn", "_add_btn")}
print("   y:", ys)
check(ys["_close_btn"] < ys["_title"] < ys["_mode_btn"] < ys["_up_btn"]
      < ys["_down_btn"] < ys["_add_btn"],
      "上から ✕ → エリア名 → 表示切替 → ▲ → ▼ → ＋ の順")
strip_h = a._strip.height()
check(ys["_close_btn"] < 12, "✕ は一番上（y=%d）" % ys["_close_btn"])
check(a._add_btn.y() + a._add_btn.height() > strip_h - 12,
      "＋ は一番下（下端 %d / 高さ %d）" % (a._add_btn.y() + a._add_btn.height(), strip_h))
check(ys["_add_btn"] - ys["_down_btn"] <= 30 and ys["_down_btn"] - ys["_up_btn"] <= 30,
      "＋ ▼ ▲ は詰めて並ぶ")
# タブ表示でも順番は同じ
w._on_area_add_below(w._areas[0])        # 2 エリアにしてタブ表示へ
w._toggle_area_view_mode()
QTest.qWait(200)
app.processEvents()
ys2 = {name: getattr(a, name).y() for name in ("_close_btn", "_mode_btn", "_up_btn", "_down_btn", "_add_btn")}
check(w._is_tab_mode(), "前提: タブ表示に切り替わった")
check(ys2["_close_btn"] < ys2["_mode_btn"] < ys2["_up_btn"] < ys2["_down_btn"] < ys2["_add_btn"],
      "タブ表示でも順番は同じ")
w.close()
finish(not fails)
