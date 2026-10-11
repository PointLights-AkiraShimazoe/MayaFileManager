# -*- coding: utf-8 -*-
"""r130: エリアの表示方法を «並列（縦積み）» と «タブ切替» で切り替えられる。

ユーザー指示 2026-10-08:
  「エリアの表示方法ですが、今の表示方法と、タブ切り替え方式を切り替え
    られる様にしてください。…切り替えボタンは、画像の緑四角の下にアイコン
    ボタンをデザインして置いてください。タブ表示中は、並列がわかる見た目の
    デザイン。並列表示中はタブがわかる見た目のデザインにしてください。」
  「現在あるボタンはタブ表示中は下記の挙動になります。
    ＋：タブ追加 / ▲：表示しているタブの位置を上に移動 /
    ▼：表示しているタブの位置を下に移動 / ✕：表示しているタブを削除」

ここで固定すること:
  1) 既定は従来どおり並列。切替ボタンは縦ストリップに居る
  2) タブ表示ではアクティブなエリアだけが見える（他は隠れる）
  3) タブをクリックすると、そのエリアへ切り替わる
  4) ＋▲▼✕ は «表示しているタブ» に効く（追加／上へ／下へ／削除）
  5) アイコンは «次に切り替わる側» を示す（並列中＝タブの絵／タブ中＝並列の絵）
  6) 設定に残り、並列へ戻すと全エリアがまた見える
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest

from ui.main_window import MainWindow
import ui.browser_area as ba

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _settle(ms=150):
    QTest.qWait(ms)
    app.processEvents()


def _visible(w):
    return [i for i, a in enumerate(w._areas) if a.isVisible()]


def s1():
    sm.set("browser_areas_state", None, save=False)
    sm.set(MainWindow.AREA_SIZES_KEY, None, save=False)
    sm.set(MainWindow.AREA_VIEW_MODE_KEY, "split", save=False)

    w = MainWindow(settings_manager=sm)
    w.resize(1200, 900)
    w.show()
    _settle(250)
    w._on_area_add_below(w._areas[0])
    w._on_area_add_below(w._areas[1])
    _settle()
    check(len(w._areas) == 3, "前提: エリアが 3 つ（%d）" % len(w._areas))

    # ── 1) 既定は並列。切替ボタンがある ───────────────────────────────
    check(not w._is_tab_mode(), "既定は並列表示")
    btn = w._areas[0]._mode_btn
    check(btn is not None and not btn.icon().isNull(),
          "切替ボタンにアイコンが入っている")
    check(len(_visible(w)) == 3, "並列では全エリアが見える（%s）" % (_visible(w),))

    # ── 5) アイコンは «次に切り替わる側» ──────────────────────────────
    split_icon = btn.icon().pixmap(16, 16).toImage()

    # ── 2) 切替 → タブ表示。アクティブだけ見える ─────────────────────
    btn.click()
    _settle()
    check(w._is_tab_mode(), "ボタンでタブ表示に切り替わる")
    check(_visible(w) == [0], "タブ表示ではアクティブだけ見える（%s）" % (_visible(w),))
    tab_icon = w._areas[0]._mode_btn.icon().pixmap(16, 16).toImage()
    check(tab_icon != split_icon,
          "並列中とタブ中でアイコンの絵が変わる（«次に切り替わる側» を示す）")

    # タブ列が出て、縦ラベルは引っ込んでいる
    a0 = w._areas[0]
    check(a0._tab_box.isVisible(), "タブ列が出ている")
    check(not a0._title.isVisible(), "並列用の «エリア N» ラベルは引っ込む")
    check(len(a0._tabs) == 3, "タブがエリアの数だけ並ぶ（%d）" % len(a0._tabs))
    # r144: 非選択タブにも名前が入っていること（無地だと «どのエリアか» が
    # 読めない。ユーザー指摘 2026-10-08）
    labels = [t._label for t in a0._tabs]
    check(all(labels), "どのタブにもラベルが入っている（%r）" % (labels,))
    check(len(set(labels)) == len(labels), "タブごとに違う名前（%r）" % (labels,))

    # ── 3) タブのクリックで切り替わる ────────────────────────────────
    a0._tabs[2].clicked.emit(2)
    _settle()
    check(_visible(w) == [2], "3 番目のタブを押すとそこが見える（%s）" % (_visible(w),))
    check(w._browser is w._areas[2].browser,
          "ツールバー等が見る «代表エリア» も表示中のタブへ移る")

    # ── 4) ＋▲▼✕ は «表示しているタブ» に効く ───────────────────────
    cur = w._areas[2]
    cur._add_btn.click()
    _settle()
    check(len(w._areas) == 4, "＋ でタブが増える（%d）" % len(w._areas))
    check(w._active_area == 3 and _visible(w) == [3],
          "追加したタブがそのまま開く（active=%s, visible=%s）"
          % (w._active_area, _visible(w)))

    w._areas[3]._up_btn.click()
    _settle()
    check(w._active_area == 2 and _visible(w) == [2],
          "▲ で表示中のタブが上へ動き、表示も付いていく（active=%s）"
          % w._active_area)

    w._areas[2]._down_btn.click()
    _settle()
    check(w._active_area == 3 and _visible(w) == [3],
          "▼ で下へ動く（active=%s）" % w._active_area)

    w._areas[3]._close_btn.click()
    _settle()
    check(len(w._areas) == 3, "✕ で表示中のタブが消える（%d）" % len(w._areas))
    check(len(_visible(w)) == 1, "消した後も 1 つだけ見える（%s）" % (_visible(w),))

    # ── 6) 設定に残り、並列へ戻せる ──────────────────────────────────
    check(sm.get(MainWindow.AREA_VIEW_MODE_KEY, "split") == "tabs",
          "表示方法が設定に残る")
    w._areas[w._active_area]._mode_btn.click()
    _settle()
    check(not w._is_tab_mode(), "もう一度押すと並列へ戻る")
    check(len(_visible(w)) == 3, "並列へ戻すと全部見える（%s）" % (_visible(w),))
    check(w._areas[0]._title.isVisible(), "«エリア N» ラベルが戻る")
    check(not w._areas[0]._tab_box.isVisible(), "タブ列は引っ込む")

    # 片付け（次のテストへ持ち越さない）
    sm.set(MainWindow.AREA_VIEW_MODE_KEY, "split", save=False)
    finish(not fails)


run(s1, delay=900)
