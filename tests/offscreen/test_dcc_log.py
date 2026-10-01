# -*- coding: utf-8 -*-
"""r88: マネージャーからの操作を DCC 側にログとして残す。
Maya → スクリプトエディタ（MGlobal.display*）/ Blender → mfm_log（Info エディタ）。
偽の maya モジュールと偽のブリッジ名前空間で、送る式をそのまま評価して確かめる。"""
import sys
import types
from _common import *  # noqa: F401,F403
from _common import app, finish, run
from core import dcc_log

LOG = []


def _install_fake_maya(file_raises=None):
    MGlobal = types.SimpleNamespace(
        displayInfo=lambda m: LOG.append(("info", m)),
        displayWarning=lambda m: LOG.append(("warning", m)),
        displayError=lambda m: LOG.append(("error", m)))
    om = types.ModuleType("maya.api.OpenMaya"); om.MGlobal = MGlobal
    api = types.ModuleType("maya.api"); api.OpenMaya = om
    cmds = types.ModuleType("maya.cmds")

    def _file(*a, **k):
        if file_raises:
            raise RuntimeError(file_raises)
        if k.get("q") or k.get("query"):
            return False
        return None
    cmds.file = _file
    cmds.confirmDialog = lambda **k: LOG.append(("dialog", k.get("message")))
    maya = types.ModuleType("maya"); maya.api = api; maya.cmds = cmds
    sys.modules.update({"maya": maya, "maya.api": api,
                        "maya.api.OpenMaya": om, "maya.cmds": cmds})


def _levels():
    return [lv for lv, _ in LOG]


def _forget_recent():
    """r119e の «DCC 側二重実行ガード» の記録を捨てる。

    このテストは同じ (操作, パス) を «ログの形» を見るために連続で評価する。
    ガードは «前回の完了から 8 秒以内の同一操作» を止めるので、
    ケースごとに忘れさせないと 2 件目以降が実行されない。
    （ガード自体の検証は test_dcc_duplicate_guard.py）"""
    import __main__
    __main__.__dict__.pop("_mfm_recent_ops", None)


def test_maya_simple_results():
    _install_fake_maya()
    for code, want in (("None", ["info", "info"]),
                       ("'Failed: boom'", ["info", "error"]),
                       ("'Error: disk full'", ["info", "error"]),
                       ("'Cancelled'", ["info", "warning"])):
        LOG.clear()
        _forget_recent()
        eval(dcc_log.wrap_maya(code, "リファレンス", "D:/proj/chr_A.ma"), {})
        assert _levels() == want, (code, LOG)
    assert LOG[0][1].startswith("[MayaFileManager] リファレンス: D:/proj/chr_A.ma"), LOG[0]
    # 戻り値はそのまま返る（マネージャーの挙動を変えない）
    assert eval(dcc_log.wrap_maya("'saved:x.ma'", "保存", "x.ma"), {}) == "saved:x.ma"
    # 例外はログを出した上で再送出
    LOG.clear()
    try:
        eval(dcc_log.wrap_maya("1/0", "開く", "x.ma"), {})
        raise AssertionError("例外が握りつぶされた")
    except ZeroDivisionError:
        pass
    assert _levels() == ["info", "error"], LOG
    print("maya: start/done/fail/cancel lines go to the Script Editor: OK")


def test_maya_real_reference_code():
    """実際に送っているリファレンスの式（失敗時は Maya 側ダイアログ＋ Failed）。"""
    _install_fake_maya(file_raises="namespace clash")
    expr = "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result'))[1])({})"
    inner = ("import maya.cmds as cmds\n"
             "try:\n"
             "    cmds.file('D:/p/a.ma', reference=True, namespace='a')\n"
             "except Exception as _e:\n"
             "    cmds.confirmDialog(title='x', message=u'fail\\n' + str(_e), button=['OK'])\n"
             "    _mfm_result = 'Failed: ' + str(_e)\n")
    LOG.clear()
    _forget_recent()
    res = eval(dcc_log.wrap_maya(expr % inner, "リファレンス", "D:/p/a.ma"), {})
    assert str(res).startswith("Failed:"), res
    lv = _levels()
    assert lv[0] == "info" and "dialog" in lv and lv[-1] == "error", LOG
    assert "namespace clash" in LOG[-1][1], LOG[-1]
    print("maya: real reference code logs the failure reason: OK")


def test_blender_logs():
    got = []
    ns = {"mfm_log": lambda m, lv="info": got.append((lv, m)),
          "mfm_import": lambda p: "error: bad file"}
    _forget_recent()
    res = eval(dcc_log.wrap_blender("mfm_import('D:/x.fbx')", "インポート", "D:/x.fbx"), ns)
    assert res == "error: bad file", res
    assert [g[0] for g in got] == ["info", "error"], got
    # 古いブリッジ（mfm_log 無し）は print にフォールバックして落ちない
    ns2 = {"mfm_open": lambda p: "confirm"}
    _forget_recent()
    assert eval(dcc_log.wrap_blender("mfm_open('D:/a.blend')", "開く", "D:/a.blend"),
                ns2) == "confirm"
    print("blender: logs via mfm_log, falls back to print on old bridges: OK")


def step():
    test_maya_simple_results()
    test_maya_real_reference_code()
    test_blender_logs()
    for k in ("maya", "maya.api", "maya.api.OpenMaya", "maya.cmds"):
        sys.modules.pop(k, None)
    finish(True)


run(step, delay=100)
