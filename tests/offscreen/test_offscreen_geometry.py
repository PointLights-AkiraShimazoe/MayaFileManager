# -*- coding: utf-8 -*-
"""r145: 画面外に保存された位置から «起動しない» ようにしない。

実機 2026-10-09:
  「今は何も起動しなくなっています」
  settings.json の window_geometry をほどくと
    frameGeometry = (-2143, -606, -737, 601) / screenNumber = 3
  で、今つながっている画面の外だった。Qt の restoreGeometry は
  モニタ構成が変わると «そのままの座標» に置くことがあり、
  ウィンドウは開いているのに見えない＝起動していないように見える。

ここで固定すること:
  1) どの画面にも載っていない位置なら、主画面の中央へ引き戻す
  2) ちゃんと載っている位置は動かさない
  3) 引き戻した結果が «最小サイズより小さい» にならない
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest

from core.compat import QApplication
from ui.main_window import MainWindow

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def s1():
    w = MainWindow(settings_manager=sm)
    _keep.append(w)
    w.resize(1000, 700)
    w.show()
    QTest.qWait(200)
    app.processEvents()

    av = QApplication.primaryScreen().availableGeometry()

    # ── 2) 画面内はそのまま ─────────────────────────────────────────
    inside = av.center()
    w.move(inside.x() - 400, inside.y() - 300)
    QTest.qWait(80)
    before = w.pos()
    w._pull_onto_screen()
    QTest.qWait(80)
    check(w.pos() == before, "画面内なら動かさない（%r → %r）" % (before, w.pos()))

    # ── 1) 画面外からは引き戻す ─────────────────────────────────────
    w.move(-2143, -606)          # 実機の保存値
    QTest.qWait(80)
    w._pull_onto_screen()
    QTest.qWait(120)
    app.processEvents()
    g = w.frameGeometry()
    hit = 0
    for sc in QApplication.screens():
        a = sc.availableGeometry().intersected(g)
        if a.isValid():
            hit = max(hit, min(a.width(), a.height()))
    check(hit >= 120, "画面外からは引き戻す（重なり %d px、位置 %r）" % (hit, w.pos()))

    # ── 3) 最小サイズを割らない ─────────────────────────────────────
    check(w.width() >= w.minimumWidth() and w.height() >= w.minimumHeight(),
          "最小サイズを割らない（%dx%d / 最小 %dx%d）"
          % (w.width(), w.height(), w.minimumWidth(), w.minimumHeight()))

    finish(not fails)


run(s1, delay=400)
