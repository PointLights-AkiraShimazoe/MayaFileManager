# -*- coding: utf-8 -*-
"""r89: Maya へ 開く／インポート／リファレンス する時、上の階層に workspace.mel が
あれば «プロジェクトをセットするか» を Maya 側で確認する。
実際に MainWindow が組み立てて送るコードを、偽の maya モジュールで評価して確かめる。"""
import os
import sys
import types
from _common import *  # noqa: F401,F403
from _common import app, tmpdir, finish, run
from core.maya_project import find_project_root

EV = []          # Maya 側で起きたこと（順番つき）
STATE = {"cur": "", "answer": None}


def _install_fake_maya():
    import __main__
    __main__.__dict__.pop("_mfm_setproj_declined", None)
    MGlobal = types.SimpleNamespace(
        displayInfo=lambda m: EV.append(("info", m)),
        displayWarning=lambda m: EV.append(("warn", m)),
        displayError=lambda m: EV.append(("error", m)))
    om = types.ModuleType("maya.api.OpenMaya"); om.MGlobal = MGlobal
    api = types.ModuleType("maya.api"); api.OpenMaya = om
    cmds = types.ModuleType("maya.cmds")

    def _file(*a, **k):
        if k.get("q") or k.get("query"):
            return False                      # 未保存の変更なし
        EV.append(("file", "open" if k.get("open") else "import" if k.get("i")
                   else "reference" if k.get("reference") else "?"))

    def _workspace(*a, **k):
        if k.get("q"):
            return STATE["cur"]
        if k.get("openWorkspace"):
            STATE["cur"] = a[0]; EV.append(("setproject", a[0]))

    def _confirm(**k):
        EV.append(("dialog", k.get("title")))
        b = k.get("button") or []
        if STATE["answer"] == "yes":
            return b[0]
        return b[-1] if b else ""
    cmds.file = _file
    cmds.workspace = _workspace
    cmds.confirmDialog = _confirm
    cmds.loadPlugin = lambda *a, **k: None
    mel = types.ModuleType("maya.mel")

    def _mel_eval(s):
        if s.startswith("setProject"):
            pj = s.split('"')[1]
            STATE["cur"] = pj
            EV.append(("setproject", pj))
    mel.eval = _mel_eval
    maya = types.ModuleType("maya"); maya.api = api; maya.cmds = cmds; maya.mel = mel
    sys.modules.update({"maya": maya, "maya.api": api, "maya.api.OpenMaya": om,
                        "maya.cmds": cmds, "maya.mel": mel})


class _W:
    from ui.main_window import MainWindow as _M
    _maya_open = _M._maya_open
    _maya_import = _M._maya_import
    _maya_reference = _M._maya_reference

    def __init__(self):
        self._inside_maya = False
        self.sent = []

    def _maya_send_or_prompt(self, code, label, log=None):
        from core.dcc_log import wrap_maya
        self.sent.append(wrap_maya(code, log[0], log[1]) if log else code)
        return True


def _run_last(w):
    return eval(w.sent[-1], {})


def kinds():
    return [k for k, _ in EV]


def step():
    root = tmpdir()
    proj = os.path.join(root, "projA")
    os.makedirs(os.path.join(proj, "scenes", "chr"))
    open(os.path.join(proj, "workspace.mel"), "w").close()
    scene = os.path.join(proj, "scenes", "chr", "chr_A.ma")
    open(scene, "w").close()
    loose = os.path.join(root, "loose", "x.ma")
    os.makedirs(os.path.dirname(loose)); open(loose, "w").close()

    assert os.path.normcase(find_project_root(scene)) == os.path.normcase(proj)
    assert find_project_root(loose) is None
    print("find_project_root walks up to workspace.mel: OK")

    _install_fake_maya()
    w = _W()
    pj = proj.replace("\\", "/")

    # 1) 別プロジェクトが現在 → 確認 → «セットする» → 開く前にセット
    EV.clear(); STATE.update(cur="D:/other", answer="yes")
    w._maya_open(scene); _run_last(w)
    k = kinds()
    assert "dialog" in k and ("setproject", pj) in EV, EV
    assert k.index("setproject") < k.index("file"), ("開く前にセットされていない", EV)
    print("open: asks, sets project before opening: OK")

    # 2) 既にそのプロジェクト → 何も聞かない
    EV.clear()
    w._maya_import(scene); _run_last(w)
    assert "dialog" not in kinds() and ("file", "import") in EV, EV
    print("import: already the current project -> no dialog: OK")

    # 3) workspace.mel が無い場所 → 何も聞かない
    EV.clear(); STATE.update(cur="D:/other")
    w._maya_reference(loose, ask_ns=False); _run_last(w)
    assert "dialog" not in kinds() and ("file", "reference") in EV, EV
    print("reference: no workspace.mel -> no dialog: OK")

    # 4) «セットしない» はそのセッション中は再確認しない（連続リファレンス対策）
    EV.clear(); STATE.update(cur="D:/other", answer="no")
    w._maya_reference(scene, ask_ns=False); _run_last(w)
    assert kinds().count("dialog") == 1 and "setproject" not in kinds(), EV
    EV.clear()
    w._maya_reference(scene, ask_ns=False); _run_last(w)
    assert "dialog" not in kinds(), ("断ったのにまた聞いた", EV)
    assert ("file", "reference") in EV
    print("declined project is not asked again in the session: OK")

    for m in ("maya", "maya.api", "maya.api.OpenMaya", "maya.cmds", "maya.mel"):
        sys.modules.pop(m, None)
    finish(True)


run(step, delay=100)
