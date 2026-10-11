# -*- coding: utf-8 -*-
"""r142: 右クリックメニューのアイコンを «一列» に揃える。

ユーザー指摘 2026-10-08:
  「この maya のアイコンの場所のルールだとすると、Perforce のロゴも
    Maya のロゴと同じ列にあるのが正しいと思います。」

原因: DCC の項目だけが setIcon() で «本物のアイコン列» を使い、残りは
絵文字を «テキストの頭» に置いていた。QMenu はアイコン列を常に確保するので、
絵文字はその分だけ右へずれて並んでいた。

固定すること:
  1) 右クリックメニューの項目は «テキストに絵文字を持たない»
     （＝アイコンは必ずアイコン列に出る）
  2) 連携（Perforce 等）のサブメニューにもアイコンが付く
  3) 連携のアイコンは Maya と同じ仕組み（インストール済み exe の正式
     アイコン → 同梱画像 → 文字）で、**必ず空でないもの**を返す
"""
import os
import re
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, \
    finish, run, QTest

from core.compat import QMenu
from ui.dcc_header import glyph_icon, integration_icon, set_integration_exe
import ui.browser_panel as bp

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


# 絵文字・記号の類（テキストの頭に残っていたら «アイコン列を使っていない»）
GLYPH_RE = re.compile(
    "[\U0001F300-\U0001FAFF←-⇿①-➿⬀-⯿⎇]")


def s1():
    # ── 1) アイコンは «必ず» 返る ────────────────────────────────────
    for ch in ("📁", "📋", "🗑", "✏", "🔄", "🖥", "⭐", "🏷", "📄"):
        ic = glyph_icon(ch)
        check(not ic.isNull() and not ic.pixmap(18, 18).isNull(),
              "%s のアイコンが作れる" % ch)

    for key in ("p4", "git", "svn", "cloud", ""):
        ic = integration_icon(key)
        check(not ic.isNull(),
              "連携アイコンが空でない（key=%r）" % key)

    # Maya と同じ «exe の正式アイコン» 経路があること（無い環境では文字へ落ちる）
    set_integration_exe("p4", None)
    check(not integration_icon("p4").isNull(),
          "exe が無くても連携アイコンは空にならない")

    # ── 2) 実際のメニューを組み立てて、テキストに絵文字が残っていないこと ──
    d = tmpdir()
    open(os.path.join(d, "alpha.ma"), "w").close()
    b = make_panel(1100, 650)
    b.navigate_to(d)
    QTest.qWait(400)
    app.processEvents()
    cvw, rect, _i = find_item(b, "alpha.ma")
    check(bool(cvw), "前提: 項目が出ている")

    menus = []
    orig_exec = QMenu.exec_ if hasattr(QMenu, "exec_") else QMenu.exec

    def fake_exec(self, *a, **k):
        menus.append(self)
        return None

    if hasattr(QMenu, "exec_"):
        QMenu.exec_ = fake_exec
    QMenu.exec = fake_exec
    try:
        b._popup_context_menu([os.path.join(d, "alpha.ma")],
                              cvw.mapToGlobal(rect.center()))
        app.processEvents()
    finally:
        if hasattr(QMenu, "exec_"):
            QMenu.exec_ = orig_exec
        QMenu.exec = orig_exec

    check(bool(menus), "右クリックメニューが組み立てられた")
    bad = []
    for m in menus:
        for a in m.actions():
            if a.isSeparator():
                continue
            t = a.text() or ""
            if GLYPH_RE.search(t):
                bad.append(t)
    check(not bad, "テキストに絵文字が残っている: %s" % (bad,))

    # フォルダ側（何も選ばずの右クリック）も同じ
    menus[:] = []
    if hasattr(QMenu, "exec_"):
        QMenu.exec_ = fake_exec
    QMenu.exec = fake_exec
    try:
        b._popup_folder_context_menu(d, cvw.mapToGlobal(rect.center()))
        app.processEvents()
    finally:
        if hasattr(QMenu, "exec_"):
            QMenu.exec_ = orig_exec
        QMenu.exec = orig_exec
    bad2 = []
    for m in menus:
        for a in m.actions():
            if not a.isSeparator() and GLYPH_RE.search(a.text() or ""):
                bad2.append(a.text())
    check(not bad2, "フォルダメニューにも絵文字が残っている: %s" % (bad2,))

    finish(not fails)


run(s1, delay=600)
