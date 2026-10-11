# -*- coding: utf-8 -*-
"""r134/r131: 表示言語・テーマをヘッダのプルダウンで切り替える／
バージョン情報に PointLights のロゴを載せる（ユーザー指示 2026-10-08）。

  「表示言語・テーマについては、メニューバーの直下の空いている部分に
    プルダウンとして置いてください。言語：システム/日本語/English
    テーマ：システム/ライト/ダーク」
  「バージョン情報にPLsのアイコンを載せてください。」
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest

from ui.main_window import MainWindow
from core import theme_engine as te

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _settle(ms=150):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    sm.set("ui_language", "auto", save=False)
    sm.set("theme", "dark", save=False)

    w = MainWindow(settings_manager=sm)
    w.resize(1200, 800)
    w.show()
    _settle(250)

    blk = getattr(w, "_prefs_blk", None)
    check(blk is not None, "ヘッダに言語／テーマのブロックがある")

    # ── 1) 選択肢がご指定どおり 3 つずつ ─────────────────────────────
    check(blk.lang.count() == 3 and blk.theme.count() == 3,
          "言語・テーマとも 3 択（%d / %d）" % (blk.lang.count(), blk.theme.count()))
    check(blk.LANGS == ("auto", "ja", "en"), "言語の値 %r" % (blk.LANGS,))
    check(blk.THEMES == ("system", "light", "dark"),
          "テーマの値 %r" % (blk.THEMES,))

    # ── 2) メニューバーの «真下» に居る ──────────────────────────────
    hdr = w._header_widget
    _settle(150)
    mb_bottom = w._menubar.geometry().bottom()
    g = blk.geometry()
    check(g.top() >= mb_bottom - 2,
          "メニューバーの下に置かれている（menubar bottom=%d, prefs top=%d）"
          % (mb_bottom, g.top()))
    check(g.left() <= 4, "左端に寄っている（left=%d）" % g.left())
    check(hdr.height() >= mb_bottom + g.height() - 2,
          "ヘッダの高さがプルダウンの分まで確保されている（%d）" % hdr.height())

    # ── 3) 現在の設定が出ている ─────────────────────────────────────
    check(blk.lang.currentIndex() == 0 and blk.theme.currentIndex() == 2,
          "起動時に設定どおりの選択（lang=%d, theme=%d）"
          % (blk.lang.currentIndex(), blk.theme.currentIndex()))

    # ── 4) 変えると設定に書かれ、UI を作り直す ──────────────
    # r135: テーマ・言語は «プロセスを落とさずに窓ごと作り直す»。
    # ここで本当に作り直すと残りの検証が古い窓を見ることになるので、
    # ここでは «呼ばれたか» だけ見る。作り直し本体は 8) 。
    calls = []
    w._relaunch_ui = lambda note="": calls.append(note)
    blk.theme.setCurrentIndex(0)            # システム
    _settle()
    check(sm.get("theme", "") == "system",
          "テーマの選択が設定に入る（%r）" % sm.get("theme", ""))
    check(len(calls) == 1, "テーマ変更で UI の作り直しが走る（%d）" % len(calls))
    blk.lang.setCurrentIndex(1)             # 日本語
    _settle()
    check(sm.get("ui_language", "") == "ja",
          "言語の選択が設定に入る（%r）" % sm.get("ui_language", ""))
    check(len(calls) == 2, "言語変更でも作り直しが走る（%d）" % len(calls))
    w._on_theme_changed("system")
    check(len(calls) == 2, "同じ値なら作り直さない（%d）" % len(calls))

    # ── 5) «システム» は実際のモードへ解決される ────────────────────
    check(te.resolve_mode("system") in ("dark", "light"),
          "システムが dark/light に解決される（%r）" % te.resolve_mode("system"))
    check(te.resolve_mode("light") == "light" and te.resolve_mode("dark") == "dark",
          "明示指定はそのまま")
    check(te.get_colors("system") == te.get_colors(te.system_mode()),
          "system を渡しても色が引ける（dark へ落ちない）")

    # ── 6) 設定ダイアログ側とずれない ───────────────────────────────
    from ui.settings_dialog import SettingsDialog
    d = SettingsDialog(sm, parent=w)
    check(d._theme_combo.count() == 3, "設定ダイアログのテーマも 3 択")
    check(d._theme_combo.currentIndex() == 0,
          "設定ダイアログにも «システム» が反映される（%d）"
          % d._theme_combo.currentIndex())
    d.deleteLater()

    # ── 7) バージョン情報のロゴ ─────────────────────────────────────
    # r131: バージョン情報は «原則 Manager を止めない» に従って非モーダル
    about = w._build_about_dialog()
    check(about is not None, "バージョン情報の窓を作れる")
    if about is not None:
        check(not about.isModal(), "バージョン情報が Manager を止めない")
        from core.compat import QLabel as _QL
        has_logo = any(lb.pixmap() is not None and not lb.pixmap().isNull()
                       for lb in about.findChildren(_QL)
                       if lb.pixmap() is not None)
        check(has_logo, "バージョン情報にロゴが載っている")
        about.deleteLater()
    pm = MainWindow._logo_pixmap(240)
    check(pm is not None and not pm.isNull(), "PLs ロゴの画像が読める")
    if pm is not None and not pm.isNull():
        check(pm.width() <= 240 and pm.height() > 0,
              "ロゴが収まる大きさに縮んでいる（%dx%d）" % (pm.width(), pm.height()))

    # ── 8) 作り直し本体が通ること ───────────────────────
    del w._relaunch_ui                      # 本物に戻す
    sm.set("theme", "dark", save=False)
    w2 = w._relaunch_ui("applying")
    _settle(500)
    check(w2 is not None and w2 is not w, "新しい窓が生まれる")
    if w2 is not None:
        check(w2.isVisible(), "新しい窓が表示されている")
        check(len(w2._areas) >= 1, "新しい窓にエリアがある")
        b2 = getattr(w2, "_prefs_blk", None)
        check(b2 is not None and b2.theme.currentIndex() == 2,
              "新しい窓のプルダウンが設定を反映している")
    check(getattr(w, "_rebuilding", False), "古い窓に作り直し中の印が立つ")

    sm.set("theme", "dark", save=False)
    sm.set("ui_language", "auto", save=False)
    finish(not fails)


run(s1, delay=900)
