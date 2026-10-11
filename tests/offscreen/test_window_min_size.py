# -*- coding: utf-8 -*-
"""r144: ウィンドウが «ある大きさから縮まらない» ことの回帰防止。

ユーザー指摘 2026-10-08:
  「manager全体が有る一定から高さが縮められません。」
  「もう半分くらいは細くなってもUI的には大丈夫では無いでしょうか？」

原因:
  - 高さ: setMinimumSize(1024, 640) に加え、縦に積んだエリアの子ウィジェット
    （ブックマーク／履歴／ブラウザ）の最小高が積み上がっていた。
  - 幅: ヘッダ行の minimumSizeHint が sizeHint をそのまま返しており、
    «メニュー＋DCC ブロック＋動作ブロックの自然幅» がウィンドウ幅の下限に
    なっていた。

ここで固定すること:
  1) ウィンドウ自身の最小サイズが小さい
  2) エリアを 3 つに増やしても、レイアウトが要求する最小高が跳ね上がらない
  3) 実際にその大きさまでリサイズできる
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest

from ui.main_window import MainWindow

fails = []

MAX_MIN_H = 480      # これ以上の «縮められない高さ» は不合格
MAX_MIN_W = 860      # 幅も（従来は 1400 超だった）


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _settle(ms=150):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    sm.set("browser_areas_state", None, save=False)
    sm.set(MainWindow.AREA_SIZES_KEY, None, save=False)
    sm.set(MainWindow.AREA_VIEW_MODE_KEY, "split", save=False)

    w = MainWindow(settings_manager=sm)
    w.resize(1200, 900)
    w.show()
    _settle(250)

    check(w.minimumHeight() <= 400,
          "ウィンドウ自身の最小高が小さい（%d）" % w.minimumHeight())
    check(w.minimumWidth() <= 800,
          "ウィンドウ自身の最小幅が小さい（%d）" % w.minimumWidth())

    h1 = w.minimumSizeHint().height()
    w1 = w.minimumSizeHint().width()
    check(h1 <= MAX_MIN_H, "エリア1つの要求最小高が %d 以下（%d）" % (MAX_MIN_H, h1))
    check(w1 <= MAX_MIN_W, "要求最小幅が %d 以下（%d）" % (MAX_MIN_W, w1))

    w._on_area_add_below(w._areas[0])
    w._on_area_add_below(w._areas[1])
    _settle()
    check(len(w._areas) == 3, "前提: エリアが 3 つ（%d）" % len(w._areas))

    h3 = w.minimumSizeHint().height()
    check(h3 <= MAX_MIN_H,
          "エリア3つでも要求最小高が %d 以下（%d）" % (MAX_MIN_H, h3))

    # 実際に縮められる
    w.resize(800, 440)
    _settle(200)
    check(w.height() <= 480, "実際に 440px 付近まで縮められる（%d）" % w.height())
    check(w.width() <= 880, "実際に 800px 付近まで細くできる（%d）" % w.width())

    finish(not fails)


run(s1, delay=900)
