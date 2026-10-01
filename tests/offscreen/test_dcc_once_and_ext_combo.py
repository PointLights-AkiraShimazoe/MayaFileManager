# -*- coding: utf-8 -*-
"""r86: ①同一操作の二重発火を抑止（Maya へのリファレンスが2回実行される件）
②保存ダイアログの拡張子プルダウンは既定 Optional（任意）
③コンボに ▼ が出る QSS が入っていること"""
import os
import time
from _common import *  # noqa: F401,F403
from _common import app, tmpdir, finish, run, sm
from core import dcc_save, theme_engine
from ui.save_dialog import SaveDialog


class _FakeStatus:
    def showMessage(self, *a, **k):
        pass


class _FakeWin:
    """MainWindow を丸ごと作らずに _dcc_once / _dcc_reference だけ検証する。"""
    from ui.main_window import MainWindow
    _dcc_once = MainWindow._dcc_once
    _dcc_accepts = MainWindow._dcc_accepts
    _dcc_reference = MainWindow._dcc_reference
    _dcc_import = MainWindow._dcc_import

    def __init__(self):
        self._dcc = "maya"
        self.sent = []

    def statusBar(self):
        return _FakeStatus()

    def _maya_reference(self, path, ask_ns=True):
        self.sent.append(("reference", path))

    def _blender_link(self, path):
        self.sent.append(("link", path))

    def _maya_import(self, path):
        self.sent.append(("import", path))

    def _blender_import(self, path):
        self.sent.append(("bimport", path))


def test_reference_is_sent_once():
    d = tmpdir()
    f = os.path.join(d, "chr_A.ma")
    open(f, "w").close()
    w = _FakeWin()
    # 同じクリックから2回呼ばれても1回だけ送る
    w._dcc_reference(f, "maya")
    w._dcc_reference(f, "maya")
    assert w.sent == [("reference", f)], w.sent
    # 別ファイルは通る
    g = os.path.join(d, "chr_B.ma")
    open(g, "w").close()
    w._dcc_reference(g, "maya")
    assert len(w.sent) == 2, w.sent
    # 種別が違えば通る（リファレンスの直後にインポート）
    w._dcc_import(g, "maya")
    assert len(w.sent) == 3, w.sent
    # 時間を置けば «意図した2回目» は通る
    w._last_dcc_send = (w._last_dcc_send[0], time.monotonic() - 2.0)
    w._dcc_import(g, "maya")
    assert len(w.sent) == 4, ("意図的な再実行まで潰している", w.sent)
    print("same action on the same file is sent once (1.2s window): OK")


def test_save_dialog_defaults_to_optional():
    d = tmpdir()
    dlg = SaveDialog("maya", "save", d, "scene.mb", sm)
    assert dlg._ext_combo.currentIndex() == 0
    assert dlg.selected_ext() == dcc_save.AUTO_EXT, dlg.selected_ext()
    assert "Optional" in dlg._ext_combo.currentText(), dlg._ext_combo.currentText()
    # 既定が Optional でも、保存される形式はファイル名から決まる
    assert dlg.effective_ext() == ".mb", dlg.effective_ext()
    assert dlg.path().endswith("scene.mb"), dlg.path()
    dlg.close()
    print("ext combo defaults to Optional and still resolves the format: OK")


def test_combo_has_arrow_qss():
    """▼ は «生成した PNG» を image: url() で渡す。
    border で三角を作る CSS の小技は Qt では小さな四角にしか描かれない（実機確認）。"""
    import os as _os
    for mode in ("dark", "light"):
        qss = theme_engine.build_qss(mode)
        assert "QComboBox::down-arrow" in qss, mode
        assert 'image: url("' in qss, mode
        png = qss.split('image: url("', 1)[1].split('"', 1)[0]
        assert png.endswith(".png") and _os.path.exists(png), (mode, png)
    print("combo shows a ▼ arrow (generated png) in both themes: OK")


def step():
    test_reference_is_sent_once()
    test_save_dialog_defaults_to_optional()
    test_combo_has_arrow_qss()
    finish(True)


run(step, delay=100)
