# -*- coding: utf-8 -*-
"""r153（2026-11-21）: 圧縮ファイルの解凍を «見つけられて・使える» ようにする。

ユーザー報告:
- 右クリックから解凍できない／書庫カラムの「展開」は文字だけでアイコンも無く、解凍できると分からない
- 書庫カラムを閉じるボタンにアイコンが無い
- zip を見ると右から出たカラムに隠れて通常のカラムが見えない
- 解凍先の窓が Manager をロックし、Manager からパスを拾えない
- 解凍先の窓を開いた瞬間、書庫のあるフォルダが選ばれた見た目でないと迷う
- 解凍してできたフォルダ（名前に «.» を含む）がファイルのアイコンになる
"""
import os, zipfile
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, Qt, QTest, QTimer

b = make_panel(1100, 650)
root = tmpdir()
sub = os.path.join(root, "pack")
os.makedirs(sub)
ZIP = os.path.join(sub, "Asset_v3.0.zip")
with zipfile.ZipFile(ZIP, "w") as z:
    z.writestr("Asset_v3.0/readme.txt", "hello")
    z.writestr("Asset_v3.0/model.ma", "maya-ascii")
b.navigate_to(sub)


def _settle(ms=350):
    QTest.qWait(ms)
    app.processEvents()


def _same(a, c):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(c))


def s1():
    from ui import archive_column as acm
    from core.compat import QFileDialog, QToolButton

    # 1) 閉じる・解凍ボタンにアイコンがあり、文言は «解凍»
    ac = b._archive_col
    close = ac.findChild(QToolButton, "arcClose")
    ext = ac.findChild(QToolButton, "arcExtract")
    assert close is not None and not close.icon().isNull(), "閉じるボタンにアイコンが無い"
    assert ext is not None and not ext.icon().isNull(), "解凍ボタンにアイコンが無い"
    assert "解凍" in ext.text() or "Extract" in ext.text(), ext.text()
    src = open(acm.__file__, encoding="utf-8").read()
    assert "展開" not in "".join(l for l in src.splitlines()
                                 if "tr(" in l), "ユーザー向け文言に «展開» が残っている"
    print("close/extract buttons have icons, wording is 解凍: OK")

    # 2) 右クリックメニューに «ここに解凍» «解凍先を選んで解凍…»
    import ui.browser_panel as bp
    got = []
    Orig = bp.QMenu

    class RecMenu(Orig):
        def exec(self, *a, **k):
            got.extend(x.text() for x in self.actions())
        exec_ = exec
    bp.QMenu = RecMenu
    try:
        b._popup_context_menu([ZIP.replace("\\", "/")], b.mapToGlobal(b.rect().center()))
        ma = os.path.join(sub, "plain.ma"); open(ma, "w").close()
        n_before = len(got)
        b._popup_context_menu([ma.replace("\\", "/")], b.mapToGlobal(b.rect().center()))
        other = got[n_before:]
    finally:
        bp.QMenu = Orig
    assert any("ここに解凍" in t or "Extract here" in t for t in got), got
    assert any("解凍先を選んで" in t or "Extract to" in t for t in got), got
    assert not any("解凍" in t or "Extract" in t for t in other), ("書庫以外にも出ている", other)
    print("context menu offers extraction for archives only: OK")

    # 3) «ここに解凍» → 書庫と同名のフォルダに中身が出る
    assert acm.extract_archive(b, ZIP, True)
    out = os.path.join(sub, "Asset_v3.0")
    assert os.path.isfile(os.path.join(out, "Asset_v3.0", "readme.txt")), os.listdir(out)
    print("extract here -> sibling folder named after the archive: OK")

    # 4) 解凍先の窓（r154）: モーダルでない・書庫のフォルダが入って全選択・フォルダ一覧を出さない
    #    （Qt のフォルダ選択の窓は、大きい・遅いフォルダで数十秒止まった。実機 2026-11-24）
    picked = []
    dlg = acm.pick_extract_dest(b, sub, picked.append)
    _settle(200)
    from core.compat import QLineEdit, QToolButton, QPushButton
    edit = dlg.findChild(QLineEdit, "extractDest")
    check_ok = (dlg.isVisible() and dlg.windowModality() == Qt.NonModal and not dlg.isModal()
                and QApplication_active_modal() is None)
    assert check_ok, "モーダルで Manager がロックされる"
    assert edit is not None and _same(edit.text(), sub) and edit.selectedText() == edit.text(), (
        "書庫のフォルダが入って全選択されていない", edit.text() if edit else None)
    assert not dlg.findChildren(QFileDialog), "フォルダ選択の窓（QFileDialog）を使っている"
    from core.compat import QAbstractItemView as _AIV
    assert not dlg.findChildren(_AIV), "フォルダの一覧を出している（遅いフォルダで止まる元）"
    # «表示中»: Manager で表示中のフォルダが入る
    b.navigate_to(root)
    _settle(300)
    dlg.findChild(QToolButton, "extractUseCurrent").click()
    assert _same(edit.text(), root), ("«表示中» で Manager のフォルダが入らない", edit.text(), root)
    # «解凍»: 決まったフォルダで on_pick
    dlg.findChild(QPushButton, "extractOk").click()
    assert picked and _same(picked[0], root), picked
    print("extract-to dialog: non-modal, archive folder preselected, no folder list, picks from Manager: OK")
    b.navigate_to(sub)
    QTimer.singleShot(50, s2)


def QApplication_active_modal():
    from core.compat import QApplication
    return QApplication.activeModalWidget()


def s2():
    # 5) 名前に «.» を含むフォルダはフォルダのアイコンで描かれる
    b.navigate_to(sub)
    _settle(600)
    cvw, rect, idx = find_item(b, "Asset_v3.0")
    assert cvw, "解凍したフォルダが見えない"
    try:
        from PySide6.QtWidgets import QStyleOptionViewItem
    except ImportError:
        from PySide2.QtWidgets import QStyleOptionViewItem
    dg = cvw.itemDelegate()
    opt = QStyleOptionViewItem()
    ix = cvw.indexAt(rect.center())
    dg.initStyleOption(opt, ix)
    from ui.browser_delegates import _folder_icon
    want = _folder_icon().pixmap(16, 16).toImage()
    have = opt.icon.pixmap(16, 16).toImage()
    assert have == want, "«.» を含むフォルダがフォルダのアイコンになっていない"
    print("dotted folder name draws a folder icon: OK")

    # 6) zip を選ぶと書庫カラムが出るが、zip を選んだカラムは隠れない
    cvw, rect, _ = find_item(b, "Asset_v3.0.zip")
    assert cvw
    QTest.mouseClick(cvw.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    _settle(900)
    assert b._archive_col.isVisible(), "書庫カラムが開かない"
    vp = b._column_view.viewport()
    host = None
    for v in b._column_view.findChildren(type(cvw)):
        if v.isVisible() and v.model() is not None:
            p = b._column_view._path_for_index(v.rootIndex())
            if p and _same(p, sub):
                host = v
    assert host is not None
    left = host.mapTo(vp, host.rect().topLeft()).x()
    right = left + host.width()
    assert left >= -1 and right <= vp.width() + 1, (
        "zip のカラムが隠れている", left, right, vp.width())
    print("archive column does not hide the column that holds the zip: OK")

    # 7) 書庫カラムの幅を変えても、中のカラムは右端まで（スクロールバーは右端・横スクロール無し）
    #    （ユーザー指摘 2026-11-22: 広げるとバーが右端から離れ、狭めるとボタンが見えない）
    from core.compat import QToolButton, QListView
    ac = b._archive_col
    for want in (520, 300, 220):
        b._set_inline_panel_sizes(b._view_stack.indexOf(ac), want)
        _settle(250)
        av = ac._view
        cols = [v for v in av.findChildren(QListView) if v.isVisible()]
        assert cols, "書庫の中のカラムが無い"
        last = max(cols, key=lambda v: v.mapTo(av.viewport(), v.rect().topLeft()).x())
        right = last.mapTo(av.viewport(), last.rect().topRight()).x() + 1
        vpw = av.viewport().width()
        assert abs(right - vpw) <= 2, ("中のカラムが右端に届いていない／はみ出している", want, right, vpw)
        hb = av.horizontalScrollBar()
        assert not hb.isVisible() and hb.value() == 0, ("1 階層なのに横スクロールが出る", want)
        for name in ("arcExtract", "arcClose"):
            btn = ac.findChild(QToolButton, name)
            bx = btn.mapTo(ac, btn.rect().topRight()).x()
            assert btn.isVisible() and bx <= ac.width(), ("ボタンが見えない", want, name, bx, ac.width())
    # 中のフォルダへ入っても（2 カラム）、最後のカラムが右端まで
    b._set_inline_panel_sizes(b._view_stack.indexOf(ac), 520)
    _settle(200)
    av = ac._view
    av.setCurrentIndex(av.model().index(0, 0))
    _settle(300)
    cols = [v for v in av.findChildren(QListView) if v.isVisible() and v.width() > 1]
    assert len(cols) >= 2, ("フォルダに入っても 2 カラムにならない", len(cols))
    last = max(cols, key=lambda v: v.mapTo(av.viewport(), v.rect().topLeft()).x())
    right = last.mapTo(av.viewport(), last.rect().topRight()).x() + 1
    assert abs(right - av.viewport().width()) <= 2 or av.horizontalScrollBar().maximum() > 0, (
        "2 カラムで右端に届いていない", right, av.viewport().width())
    av.setCurrentIndex(av.model().index(-1, -1))
    print("archive inner columns fill to the right edge at any width: OK")

    # 7b) 狭いカラムでも «解凍» と閉じるボタンがカラムの中に見えている（見出しは省略）
    b._set_inline_panel_sizes(b._view_stack.indexOf(ac), 220)
    ac._title.setText("very_long_archive_name_" * 6 + ".zip（123 ファイル）")
    _settle(300)
    for name in ("arcExtract", "arcClose"):
        btn = ac.findChild(QToolButton, name)
        r = btn.rect()
        right = btn.mapTo(ac, r.topRight()).x()
        assert btn.isVisible() and btn.width() >= 16 and right <= ac.width(), (
            "ボタンがカラムの外へ押し出されている", name, right, ac.width(), btn.width())
    assert ac._title.text() != ac._title.full_text(), "見出しが省略されていない"
    print("narrow archive column keeps extract/close buttons visible: OK")
    finish()


run(s1, delay=1500)
