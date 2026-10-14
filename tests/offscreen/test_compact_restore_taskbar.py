# -*- coding: utf-8 -*-
"""r146: コンパクトのまま終了した次の起動で «アプリを見失う» 事故。

ユーザー報告 2026-10-13:
  「前回をコンパクトのまま終了したためか、コンパクトの表示も
    タスクバーへの表示もでないので、見失いました。」

原因は 2 つ重なっていた。

  1. **コンパクト状態を読み戻していなかった。**
     ACTIVE_KEY（compact_mode）は保存していたのに、起動時に誰も読まない。
  2. **パックがタスクバーに出ていなかった。**
     Qt.Tool をやめて Qt.Window にしただけでは足りない。Windows は
     FramelessWindowHint の窓を WS_POPUP として作るので、拡張スタイルに
     WS_EX_APPWINDOW を立てないとタスクバーのボタンが付かない。

さらに «覚えている置き場所が画面外»（実機の保存値は -2108,-510）だと、
畳んだ先が見えないので本体もパックも無い状態になる。

ここで固定すること:
  1) 起動時に compact_mode を読み戻す
  2) パックの置き場所が画面外なら引き戻す
  3) パックをタスクバーに出す細工が入っている
  4) 畳んだのにパックが出せなければ通常表示へ戻す（見失わない）
"""
import io
import os

from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest

from core.compat import QApplication
from ui.main_window import MainWindow

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _settle(ms=150):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    # ── 3) タスクバーに出す細工 ─────────────────────────────────
    from core import win_topmost
    check(hasattr(win_topmost, "set_taskbar_button"),
          "タスクバー表示を切り替える口がある")
    cm_src = io.open(os.path.join(ROOT, "ui", "compact_mode.py"),
                     encoding="utf-8").read()
    check("set_taskbar_button" in cm_src, "パックがそれを使っている")
    check("WS_EX_APPWINDOW" in io.open(
        os.path.join(ROOT, "core", "win_topmost.py"), encoding="utf-8").read(),
        "WS_EX_APPWINDOW を立てている（枠無し窓はこれが無いと出ない）")

    # ── 1) 起動時の復元 ─────────────────────────────────────────
    sm.set("compact_mode", True, save=False)
    # 2) 覚えている置き場所を «どの画面にも無い» 値にしておく（実機の再現）
    sm.set("compact_puck_pos", [-2108, -510], save=False)
    sm.set(MainWindow.ALWAYS_ON_TOP_KEY, False, save=False)

    w = MainWindow(settings_manager=sm)
    _keep.append(w)
    w.resize(900, 600)
    w.show()
    _settle(400)          # __init__ が singleShot で復元を仕掛ける

    ctl = w._compact()
    check(ctl.is_active(), "前回コンパクトだったら、次の起動でも畳んだ状態になる")
    puck = ctl._puck
    check(puck is not None, "パックが作られる")

    if puck is not None:
        g = puck.frameGeometry()
        on_screen = any(sc.availableGeometry().intersected(g).isValid()
                        for sc in QApplication.screens())
        check(on_screen,
              "画面外に覚えていた置き場所から引き戻す（%r）" % (g.topLeft(),))
        check(puck.isVisible(), "パックが実際に見えている")
        check(bool(puck.windowTitle()), "タスクバーに出る名前を持っている")
        check(w._lead_blk.compact_btn.isChecked(),
              "ヘッダのボタンも «コンパクト» を指している")

    # ── 4) 見失わない安全網 ─────────────────────────────────────
    import ui.compact_mode as _cm
    src = io.open(_cm.__file__.replace(".pyc", ".py"), encoding="utf-8").read() \
        if os.path.exists(_cm.__file__.replace(".pyc", ".py")) else cm_src
    check("if not puck.isVisible():" in src,
          "畳んだのにパックが見えなければ通常表示へ戻す")

    # 通常表示へ戻せる
    ctl.set_active(False)
    _settle(200)
    check(w.isVisible(), "コンパクトを抜ければ本体が出る")
    check(not bool(sm.get("compact_mode", True)), "抜けた状態が設定に残る")

    w.close()
    finish(not fails)


run(s1, delay=900)
