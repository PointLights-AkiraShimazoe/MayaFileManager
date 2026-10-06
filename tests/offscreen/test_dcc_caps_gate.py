# -*- coding: utf-8 -*-
"""r91: Maya・Blender 問わず «そのコマンドの対象形式» にだけ反応する。
.py を D&D したら «開く» を送ろうとした、への対策。送信の関所と右クリック
メニューが同じ表（core/dcc_caps.py）を使っていること。"""
import os
import types
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, run, sm
from core import dcc_caps
import ui.browser_panel as bp

d = tmpdir()
F = {n: os.path.join(d, n) for n in
     ("install.py", "tool.mel", "a.ma", "b.mb", "c.fbx", "d.obj", "e.abc",
      "f.usd", "g.blend", "h.png", "i.txt", "j.gltf")}
for p in F.values():
    open(p, "w").close()


class _W:
    from ui.main_window import MainWindow as _M
    _dcc_accepts = _M._dcc_accepts
    _dcc_open = _M._dcc_open
    _dcc_import = _M._dcc_import
    _dcc_reference = _M._dcc_reference
    _dcc_once = _M._dcc_once
    _dcc_guarded = _M._dcc_guarded          # r123: 共通の関所
    _on_maya_drop = _M._on_maya_drop

    def __init__(self):
        self._dcc = "maya"
        self.sent = []
        self.msgs = []

    def statusBar(self):
        return types.SimpleNamespace(showMessage=lambda m, *a: self.msgs.append(m))

    def _maya_open(self, p): self.sent.append(("maya", "open", os.path.basename(p)))
    def _maya_import(self, p): self.sent.append(("maya", "import", os.path.basename(p)))
    def _maya_reference(self, p, ask_ns=True): self.sent.append(("maya", "reference", os.path.basename(p)))
    def _blender_open(self, p): self.sent.append(("blender", "open", os.path.basename(p)))
    def _blender_import(self, p): self.sent.append(("blender", "import", os.path.basename(p)))
    def _blender_link(self, p): self.sent.append(("blender", "reference", os.path.basename(p)))
    def _maya_run_script(self, p): self.sent.append(("maya", "run_script", os.path.basename(p)))
    def _dcc_save_dialog(self, *a): pass


def test_table():
    assert dcc_caps.supports("maya", "open", F["a.ma"])
    assert not dcc_caps.supports("maya", "open", F["install.py"])
    assert not dcc_caps.supports("maya", "open", F["c.fbx"]), "開くはシーン形式のみ"
    assert dcc_caps.supports("maya", "import", F["e.abc"])
    assert not dcc_caps.supports("maya", "reference", F["f.usd"])
    assert dcc_caps.supports("maya", "run_script", F["tool.mel"])
    assert dcc_caps.supports("blender", "reference", F["g.blend"])
    assert not dcc_caps.supports("blender", "reference", F["c.fbx"]), "リンクは .blend のみ"
    assert not dcc_caps.supports("maya", "import", d), "フォルダは対象外"
    print("capability table: OK")


def test_gate_never_sends_unsupported():
    w = _W()
    for name in F:
        for app_ in ("maya", "blender"):
            w._dcc_open(F[name], app_)
            w._dcc_import(F[name], app_)
            w._dcc_reference(F[name], app_, ask_ns=False)
            w._last_dcc_send = (None, 0.0)
    for app_, cmd, name in w.sent:
        assert dcc_caps.supports(app_, cmd, F[name]), ("対象外を送った", app_, cmd, name)
    assert ("maya", "open", "install.py") not in w.sent
    assert any("install.py" in m for m in w.msgs), "スキップ理由が出ていない"
    # D&D（動作=開く）で .py / .png を落としても «開く» は送らない
    w.sent.clear()
    w._on_maya_drop("open", [F["install.py"], F["h.png"]], "maya")
    assert not any(c == "open" for _, c, _n in w.sent), w.sent
    # 複数のうち対象の最初の1件を開く（先頭が対象外でも）
    w.sent.clear(); w._last_dcc_send = (None, 0.0)
    w._on_maya_drop("open", [F["h.png"], F["b.mb"]], "maya")
    assert w.sent == [("maya", "open", "b.mb")], w.sent
    print("gate never sends a command to an unsupported file: OK")


def test_menu_follows_table():
    b = make_panel()
    b._dcc_callback = lambda *a: None
    cur = {"dcc": "maya"}
    b.set_dcc_target_provider(lambda: cur["dcc"])
    captured = []

    class _Menu(bp.QMenu):
        def exec_(self, *a, **k):
            # r125: 項目名から «Maya» «Blender» を外したので data() で見る
            captured.append([x.data() for x in self.actions() if x.text()])
        exec = exec_
    orig = bp.QMenu
    bp.QMenu = _Menu
    try:
        def cmds(name):
            captured.clear()
            b._popup_context_menu([F[name]], b.mapToGlobal(b.rect().center()))
            return [d for d in captured[-1] if isinstance(d, tuple)]
        t = cmds("install.py")
        assert not [d for d in t if d[1] in ("open", "import", "reference")], t
        t = cmds("c.fbx")
        assert ("maya", "open") not in t and ("maya", "import") in t \
            and ("maya", "reference") in t, t
        t = cmds("f.usd")
        assert ("maya", "import") in t and ("maya", "reference") not in t, t
        cur["dcc"] = "blender"
        t = cmds("c.fbx")
        assert ("blender", "import") in t and ("blender", "reference") not in t, t
    finally:
        bp.QMenu = orig
    print("context menu shows only commands whose target the file is: OK")


def step():
    test_table()
    test_gate_never_sends_unsupported()
    test_menu_follows_table()
    finish(True)


run(step, delay=300)
