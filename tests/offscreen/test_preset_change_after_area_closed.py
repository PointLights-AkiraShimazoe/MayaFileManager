# -*- coding: utf-8 -*-
"""ユーザー報告 2026-10-31（v0.9.13）: プリセットを変えると
«予期しないエラー: Internal C++ object (QuickNavBar) already deleted»。

経路: 🔗リンク ON で、エリアを閉じた後にプリセットを変える。
閉じたエリアのプリセット行は C++ 側が消えても Python の入れ物が残ることが
あり、リンクの伝播がその入れ物を触って落ちていた（2026-10-08 にも同じ経路で
QHBoxLayout 版が出ていた）。

確かめること:
  1) エリアを閉じた後にプリセットを変えてもエラーにならない（combo の操作で）
  2) 生きている他のエリアにはリンクどおり伝わる
  3) 閉じたエリアの入れ物は一覧から外れる
  4) 🔗 の ON/OFF を切り替えてもエラーにならない
"""
import sys
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest
try:
    from PySide6.QtCore import QEvent
except ImportError:
    from PySide2.QtCore import QEvent
from ui.main_window import MainWindow
from ui.nav_panels import QuickNavBar

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


errors = []
_orig_hook = sys.excepthook
sys.excepthook = lambda t, v, tb: errors.append("%s: %s" % (t.__name__, v))

sm.save_quick_nav_presets({"default": [{"label": "A", "path": "/tmp"}],
                           "MM": [{"label": "B", "path": "/tmp"}],
                           "XX": [{"label": "C", "path": "/tmp"}]})
sm.set("quick_nav_link_areas", True, save=False)

w = MainWindow(sm)
w.resize(1400, 900)
w.show()
while len(w._areas) < 3:
    w._add_area()
QTest.qWait(300)

# 閉じるエリアのプリセット行を «どこかが握っている» 状態にする
# （実機ではシグナルの接続先などが入れ物を残す）
gone = w._areas[2]
ghost = gone.quick_nav
w._on_area_remove(gone)
for _i in range(5):
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    QTest.qWait(50)

from core.compat import is_alive
check(not is_alive(ghost), "閉じたエリアのプリセット行は C++ 側が消えている（前提）")

a0, a1 = w._areas[0].quick_nav, w._areas[1].quick_nav
a0._preset_combo.setCurrentText("MM")
QTest.qWait(200)
check(not errors, "エリアを閉じた後にプリセットを変えてもエラーにならない（%r）" % (errors,))
check(a0.active_preset() == "MM" and a1.active_preset() == "MM",
      "生きている他のエリアにはリンクどおり伝わる（%s / %s）"
      % (a0.active_preset(), a1.active_preset()))
alive = QuickNavBar.live_instances() if hasattr(QuickNavBar, "live_instances") else []
check(ghost not in alive and len(alive) == 2,
      "閉じたエリアの入れ物は一覧から外れる（%d）" % len(alive))

errors.clear()
a1._preset_combo.setCurrentText("XX")
a0._link_btn.setChecked(False)
a0._link_btn.setChecked(True)
QTest.qWait(200)
check(not errors, "続けて変えても・🔗 を切り替えてもエラーにならない（%r）" % (errors,))
check(a0.active_preset() == "XX", "リンク ON の伝播が続けて効く（%s）" % a0.active_preset())

sys.excepthook = _orig_hook
w.close()
finish(not fails)
