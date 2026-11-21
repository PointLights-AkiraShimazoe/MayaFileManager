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

    # 4) 解凍先の窓はモーダルでない（Manager からパスを拾える）・書庫のフォルダが選ばれている
    picked = []
    dlg = acm.pick_directory_nonmodal(b, "t", sub, picked.append)
    _settle(200)
    assert isinstance(dlg, QFileDialog) and dlg.isVisible()
    assert dlg.windowModality() == Qt.NonModal and not dlg.isModal(), "モーダルで Manager がロックされる"
    assert QApplication_active_modal() is None, "モーダル窓が出ている"
    assert _same(dlg.directory().absolutePath(), root), dlg.directory().absolutePath()
    sel = dlg.selectedFiles()
    assert sel and _same(sel[0], sub), ("書庫のフォルダが選ばれていない", sel)
    dlg.fileSelected.emit(sub)
    assert picked and _same(picked[0], sub)
    dlg.close()
    print("extract-to dialog is non-modal and starts with the archive folder selected: OK")
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
    finish()


run(s1, delay=1500)
