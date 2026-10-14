# -*- coding: utf-8 -*-
"""r145: 最前面表示トグルとコンパクトモード。

ユーザー指示 2026-10-09:
  「すべてのウィンドウの上に表示するモードのOn/Offの切り替えを
    メニューバーの「ファイル」の左隣に置いてください。
    また、その隣に、コンパクトモードのボタンを置いてください。
    …mayaの任意の場所に最小のウィンドウとして置いておき、マウスオーバーで
    マネージャー全体が開き通常と同じ様に操作が出来、ウィンドウ外にマウスを
    移動すると小さくなるという挙動です。
    コンパクト状態では、通常状態に戻すアイコンボタンだけが見えてる
    ものとしてください。」

ここで固定すること:
  1) 2 つのボタンがメニューバーの «左» に並ぶ
  2) 最前面表示トグルでウィンドウフラグが変わり、設定に残る
  3) 切り替えてもウィンドウの位置・大きさが飛ばない
  4) コンパクトに入ると本体が隠れ、パック（戻すボタンだけ）が出る
  5) パックにマウスを乗せると本体が開き、パックは隠れる
  6) 外に出ても «ポップアップ／モーダル／ドラッグ／入力中» の間は畳まない
  7) 戻すボタンでコンパクトを抜ける
  8) r145 追記（指示 2026-10-09）:
     - つまみ（ドラッグ用）と開くアイコンが «別の当たり» になっている
     - つまみに乗せても開かない（以前は全体がホバーで開き、戻すボタンを
       押す前にパックが消えて «戻せない» になっていた）
     - パックの左上 ＝ 本体の左上。開くと右下へ広がり、畳むと左上の
       角へ吸い込まれる。どちらを動かしても関係が崩れない
     - 離れたら «すぐ» 畳む
     - コンパクト中はヘッダのボタンが光る
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest

from core.compat import Qt
from ui.main_window import MainWindow

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _settle(ms=120):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    sm.set(MainWindow.ALWAYS_ON_TOP_KEY, False, save=False)
    sm.set("compact_mode", False, save=False)
    w = MainWindow(settings_manager=sm)
    _keep.append(w)
    w.resize(1100, 700)
    w.move(220, 160)
    w.show()
    _settle(250)

    # ── 1) 位置: メニューバーより左 ─────────────────────────────────
    lead = w._lead_blk
    hdr = w._header_widget
    check(lead.parent() is hdr, "左端ブロックがヘッダ行に入っている")
    check(lead.x() < w._menubar.x(),
          "メニューバーより左にある（lead=%d menubar=%d）"
          % (lead.x(), w._menubar.x()))
    check(lead.top_btn.x() < lead.compact_btn.x(),
          "最前面トグルの «隣» にコンパクトボタン")
    check(lead.top_btn.isCheckable() and lead.compact_btn.isCheckable(),
          "どちらも On/Off（今の状態が «光っているか» で分かる）")
    check(not lead.top_btn.icon().isNull()
          and not lead.compact_btn.icon().isNull(), "どちらもアイコンがある")

    # ── 2)(3) 最前面表示 ────────────────────────────────────────────
    before = w.geometry()
    lead.top_btn.click()
    _settle(200)
    # r146: 最前面表示は Qt のフラグではなく Win32 の SetWindowPos で行う
    # （フラグを触ると ✕ が押せなくなる／ウィンドウが作り直される／子ダイアログ
    #   が裏に回る、の 3 つが起きる）。状態は本体が持つ。
    check(w.is_always_on_top(), "最前面表示が入る")
    # Qt のフラグを触らないのは **Windows の実装**（SetWindowPos）の話。
    # Windows 以外は Qt のフラグへ落とすのが正しい動作なので、そこは見ない。
    import sys as _sys
    if _sys.platform.startswith("win"):
        check(not (w.windowFlags() & Qt.WindowStaysOnTopHint),
              "Qt のフラグは触らない（✕ が消えない・作り直しが起きない）")
    else:
        check(True, "Windows 以外は Qt のフラグへ落とす（確認は Windows で）")
    check(bool(sm.get(MainWindow.ALWAYS_ON_TOP_KEY, False)),
          "設定に残る")
    after = w.geometry()
    check(abs(after.width() - before.width()) <= 2
          and abs(after.height() - before.height()) <= 2,
          "大きさが飛ばない（%r → %r）" % (before.size(), after.size()))
    check(abs(after.x() - before.x()) <= 2 and abs(after.y() - before.y()) <= 2,
          "位置が飛ばない（%r → %r）" % (before.topLeft(), after.topLeft()))
    lead.top_btn.click()
    _settle(200)
    check(not w.is_always_on_top(), "もう一度押すと解除")
    check(not bool(sm.get(MainWindow.ALWAYS_ON_TOP_KEY, True)), "解除も残る")

    # ── 4) コンパクトへ ─────────────────────────────────────────────
    lead.compact_btn.click()
    _settle(250)
    ctl = w._compact()
    puck = ctl._puck
    check(ctl.is_active(), "コンパクトモードに入る")
    check(not w.isVisible(), "本体が隠れる")
    check(puck is not None and puck.isVisible(), "パックが出る")
    check(lead.compact_btn.isChecked(), "ヘッダのボタンが光る（状態が分かる）")
    # r146（ユーザー指示 2026-10-11）: コンパクトモードは最前面表示を
    # 勝手に触らない。«どこへ行ったか» はパック自身で担保する
    #   - パックは常に手前（本体の設定とは無関係）
    #   - パックはタスクバーに出る（Qt.Tool をやめた）
    check(not w.is_always_on_top(),
          "コンパクトに入っても最前面表示を勝手に入れない")
    check(bool(puck.windowFlags() & Qt.WindowStaysOnTopHint),
          "パック自身は常に手前")
    check((puck.windowFlags() & Qt.WindowType_Mask) != Qt.Tool,
          "パックはタスクバーに出る（Qt.Tool ではない）")
    check(bool(puck.windowTitle()),
          "タスクバーに出る名前を持っている")
    # 位置の検証中は «外に出たら畳む» 監視を黙らせる。オフスクリーンでは
    # カーソルが (0,0) 固定なので、開いた直後に畳まれてしまう。
    # （実機では開いた窓の角がパックの位置なので、カーソルは窓の中にいる）
    ctl._cursor_inside = lambda: True

    # ── 8a) つまみと開くアイコンが別の当たりになっている ─────────────
    check(puck.grip is not puck.btn, "つまみと開くアイコンが別")
    check(not puck.grip.geometry().intersects(puck.btn.geometry()),
          "当たりが重なっていない（grip=%r btn=%r）"
          % (puck.grip.geometry(), puck.btn.geometry()))
    check(puck.grip.x() < puck.btn.x(), "つまみが左、開くアイコンが右")
    check(puck.grip.__class__.__name__ == "_Grip", "つまみは専用ウィジェット")
    # つまみ側は «開く» を持たない。開くのはアイコン側だけ。
    import ui.compact_mode as _cm
    check("enterEvent" not in vars(_cm._Grip),
          "つまみに乗せても開かない（enterEvent を自分で持たない）")
    check("enterEvent" in vars(_cm._OpenButton),
          "開くのはアイコン側の仕事")
    check(not ctl.is_expanded() and not w.isVisible(),
          "この時点ではまだ畳んだまま")

    # つまみのドラッグで動く（位置を直接動かして記憶されるかを見る）
    puck.move(puck.x() + 40, puck.y() + 30)
    ctl.remember_puck_pos()
    _settle(80)
    check(list(sm.get(ctl.PUCK_POS_KEY, [])) == [puck.x(), puck.y()],
          "動かした位置を覚える（%r）" % (sm.get(ctl.PUCK_POS_KEY, None),))

    # ── 5)+8b) 開くと «パックの右下» にウィンドウが出る ──────────────
    ptl = puck.frameGeometry().topLeft()
    ctl.expand()
    _settle(200)
    check(w.isVisible(), "乗せると本体が開く")
    check(not puck.isVisible(), "開いている間はパックが隠れる")
    wtl = w.frameGeometry().topLeft()
    av = QApplication.primaryScreen().availableGeometry()
    if av.contains(w.frameGeometry()):
        check(abs(wtl.x() - ptl.x()) <= 2 and abs(wtl.y() - ptl.y()) <= 2,
              "パックのいた場所から右下へ開く（puck=%r win=%r）" % (ptl, wtl))
    else:
        check(True, "画面端のため位置は画面内に丸められた（puck=%r win=%r）"
              % (ptl, wtl))

    # ── 8d) 開いたまま «ウィンドウを動かしたら» 戻る先も動く ─────────
    #     画面端で丸められると位置が動くのは «正しい» 挙動なので、
    #     位置の往復はウィンドウが画面に収まる状態でだけ確かめる。
    av = QApplication.primaryScreen().availableGeometry()
    w.resize(max(w.minimumWidth(), min(560, av.width() - 80)),
             max(w.minimumHeight(), min(380, av.height() - 80)))
    _settle(120)
    room = (w.frameGeometry().width() + puck.width() + 40 <= av.width()
            and w.frameGeometry().height() + puck.height() + 40 <= av.height())

    def _place_puck_for(d):
        """その向きで窓が画面に収まるパックの置き場所。"""
        g = w.frameGeometry()
        left = d in ("dl", "ul")
        up = d in ("ul", "ur")
        x = (av.right() - 10 - puck.width()) if left else (av.left() + 10)
        y = (av.bottom() - 10 - puck.height()) if up else (av.top() + 10)
        _ = g
        puck.move(x, y)
        ctl.remember_puck_pos()
        _settle(60)

    if room:
        _place_puck_for("dr")
        ptl = puck.frameGeometry().topLeft()
        ctl.expand()
        _settle(120)
        wtl = w.frameGeometry().topLeft()
        check(abs(wtl.x() - ptl.x()) <= 2 and abs(wtl.y() - ptl.y()) <= 2,
              "パックの左上から右下へ開く（puck=%r win=%r）" % (ptl, wtl))
        w.move(w.x() + 60, w.y() + 40)
        _settle(120)
        moved_tl = w.frameGeometry().topLeft()
        ctl.collapse()
        _settle(120)
        new_ptl = puck.frameGeometry().topLeft()
        check(abs(new_ptl.x() - moved_tl.x()) <= 2
              and abs(new_ptl.y() - moved_tl.y()) <= 2,
              "動かした先の左上へ畳む（win=%r puck=%r）" % (moved_tl, new_ptl))
        ctl.expand()
        _settle(120)
        back_tl = w.frameGeometry().topLeft()
        check(abs(back_tl.x() - moved_tl.x()) <= 2
              and abs(back_tl.y() - moved_tl.y()) <= 2,
              "往復しても位置がずれない（%r → %r）" % (moved_tl, back_tl))

        # ── 8e) パックを動かしたら、次に開く場所も動く ───────────────
        ctl.collapse()
        _settle(120)
        puck.move(puck.x() + 40, puck.y() + 30)
        ctl.remember_puck_pos()
        moved_ptl = puck.frameGeometry().topLeft()
        ctl.expand()
        _settle(120)
        open_tl = w.frameGeometry().topLeft()
        check(abs(open_tl.x() - moved_ptl.x()) <= 2
              and abs(open_tl.y() - moved_ptl.y()) <= 2,
              "パックを動かすと開く場所も動く（puck=%r win=%r）"
              % (moved_ptl, open_tl))
    else:
        check(True, "画面が小さすぎるので位置の往復は確かめない（%r）" % (av,))

    # ── 6) 畳まない条件 ─────────────────────────────────────────────
    #     ここからは «カーソルは外にいる» ことにして、畳む側を確かめる
    ctl.expand()
    _settle(120)
    ctl._cursor_inside = lambda: False
    ctl._typing = lambda: False
    busy = {"v": True}
    ctl._busy = lambda: busy["v"]
    for _ in range(5):
        ctl._tick()
        QTest.qWait(60)
    check(w.isVisible(), "ポップアップ等が出ている間は畳まない")

    # ── 8c) 離れたら «すぐ» 畳む（1 tick で畳む）──────────────────────
    busy["v"] = False
    ctl._tick()
    _settle(120)
    check(not w.isVisible(), "外に出たら次の監視で即座に畳む")
    check(puck.isVisible(), "畳んだらパックが戻る")
    check(ctl.POLL_MS <= 100, "監視の間隔が短い（%d ms）" % ctl.POLL_MS)

    # ── 9) 開く向き（r145、指示 2026-10-09）────────────────────────
    #   「押す度に、右下→左下→左上→右上と回る様にしてください。
    #     右上の次は右下に戻ります。ウィンドウでの配置場所もそれに応じて
    #     変わります。アイコンの位置は右下・右上の場合は、アイコンの右
    #     左下・左上の場合はドラッグ用のツマミも含め左右反転した配置で」
    check(hasattr(lead, "dir_btn"), "向きボタンがある")
    check(lead.compact_btn.x() < lead.dir_btn.x(),
          "コンパクト切り替えの «横» に並ぶ")
    seen = [ctl.open_dir()]
    for _ in range(4):
        lead.dir_btn.click()
        _settle(80)
        seen.append(ctl.open_dir())
    check(seen == ["dr", "dl", "ul", "ur", "dr"],
          "右下→左下→左上→右上→右下 と回る（%r）" % (seen,))

    # 向きごとに «揃える角» が変わり、往復しても位置がずれない。
    for d in ("dr", "dl", "ul", "ur"):
        w.set_compact_open_dir(d)
        _settle(80)
        ctl.collapse()
        _settle(100)
        if not room:
            check(True, "%s: 画面が小さすぎるので確かめない" % d)
            continue
        _place_puck_for(d)
        ptl = puck.frameGeometry().topLeft()
        ctl.expand()
        _settle(100)
        ctl.collapse()
        _settle(100)
        back = puck.frameGeometry().topLeft()
        check(abs(back.x() - ptl.x()) <= 2 and abs(back.y() - ptl.y()) <= 2,
              "%s: 開いて畳んでも戻る先が同じ（%r → %r）" % (d, ptl, back))

    # 左向きのときは、つまみとアイコンが左右反転している
    w.set_compact_open_dir("dr")
    _settle(80)
    check(puck.grip.x() < puck.btn.x(), "右向きは [つまみ][アイコン]")
    w.set_compact_open_dir("dl")
    _settle(120)
    check(puck.btn.x() < puck.grip.x(), "左向きは [アイコン][つまみ]（左右反転）")
    w.set_compact_open_dir("ul")
    _settle(120)
    check(puck.btn.x() < puck.grip.x(), "左上も左右反転のまま")
    w.set_compact_open_dir("ur")
    _settle(120)
    check(puck.grip.x() < puck.btn.x(), "右上は [つまみ][アイコン] に戻る")

    # 設定に残る
    w.set_compact_open_dir("ul")
    _settle(80)
    check(sm.get(ctl.OPEN_DIR_KEY, "") == "ul",
          "向きが設定に残る（%r）" % sm.get(ctl.OPEN_DIR_KEY, None))
    w.set_compact_open_dir("dr")
    _settle(80)

    # ── 7) 戻すボタンでコンパクトを抜ける ───────────────────────────
    puck.btn.click()
    _settle(250)
    check(not ctl.is_active(), "開くアイコンのクリックでコンパクトを抜ける")
    check(w.isVisible(), "本体が戻る")
    check(not puck.isVisible(), "パックは消える")
    check(not lead.compact_btn.isChecked(), "ヘッダのボタンの光が消える")

    # ヘッダのトグルでも抜けられる（入って、もう一度押す）
    lead.compact_btn.click()
    _settle(200)
    check(ctl.is_active(), "ヘッダのトグルで入れる")
    lead.compact_btn.click()
    _settle(200)
    check(not ctl.is_active(), "ヘッダのトグルで戻せる")
    check(w.isVisible(), "本体が戻る（トグル経由）")

    finish(not fails)


run(s1, delay=600)
