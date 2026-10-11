# -*- coding: utf-8 -*-
"""①r145: 連続した同じ操作は «毎回» 送る（時間窓で握りつぶさない）＋
  1 操作 = 1 個の操作 ID
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
    _dcc_guarded = MainWindow._dcc_guarded      # r123: 共通の関所
    _dcc_begin_op = MainWindow._dcc_begin_op    # r145
    _dcc_end_op = MainWindow._dcc_end_op        # r145
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


def test_repeats_are_not_swallowed():
    """r145: ユーザーが続けて 2 回やったら 2 回実行される。

    旧実装は «1.2 秒以内の同じ操作» を捨てていた。これはユーザーの正当な
    連続実行まで握りつぶす（指摘 2026-10-08）。1 操作が 2 回 DCC に届いて
    しまう問題は «操作 ID による冪等化» で実行側が保証するので、
    送信側は時間で捨ててはいけない。"""
    d = tmpdir()
    f = os.path.join(d, "chr_A.ma")
    open(f, "w").close()
    w = _FakeWin()
    w._dcc_reference(f, "maya")
    w._dcc_reference(f, "maya")
    assert len(w.sent) == 2, ("連続した同じ操作が握りつぶされている", w.sent)
    g = os.path.join(d, "chr_B.ma")
    open(g, "w").close()
    w._dcc_reference(g, "maya")
    w._dcc_import(g, "maya")
    assert len(w.sent) == 4, w.sent
    print("a repeated action is sent every time (no time window): OK")


def test_each_operation_gets_one_id():
    """r145: 1 操作 = 1 ID。操作ごとに別の ID が振られる。"""
    d = tmpdir()
    f = os.path.join(d, "chr_C.ma")
    open(f, "w").close()
    w = _FakeWin()
    ids = []
    orig = w._maya_reference

    def _spy(path, ask_ns=True):
        ids.append(getattr(w, "_dcc_op_id", ""))
        return orig(path)
    w._maya_reference = _spy
    w._dcc_reference(f, "maya")
    w._dcc_reference(f, "maya")
    assert len(ids) == 2 and all(ids), ids
    assert ids[0] != ids[1], ("別の操作なのに同じ ID", ids)
    assert not getattr(w, "_dcc_op_id", ""), "操作が終わったら ID は空に戻す"
    print("every operation gets its own op id: OK")


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
    test_repeats_are_not_swallowed()
    test_each_operation_gets_one_id()
    test_save_dialog_defaults_to_optional()
    test_combo_has_arrow_qss()
    finish(True)


run(step, delay=100)
