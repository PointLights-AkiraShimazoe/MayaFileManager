# -*- coding: utf-8 -*-
"""r146（ユーザー報告 2026-11-07）: 「Maya の File メニューの Save Scene で
エラーが出た（文面は残っていない）。右クリックの『シーンを保存』で事なきを
得たが、頻発するなら解消法にたどり着けるユーザーがいない」
「Manager から開くとワークスペースの場所がおかしくなるのでは」

確かめること:
  1) 接続できた Maya に «保存の見張り» を 1 回だけ入れる（同じ Maya に二重に入れない）
  2) Windows: 本物の Maya（mayapy）で
     * 保存できた時: シーン・プロジェクト・書き込めるかが記録される
     * 開いた時: シーンとプロジェクトが記録される
     * 保存できなかった時: «保存できなかった» と、Maya が出したエラーの文面が記録される
     * 見張りを 2 回入れても 1 回分しか動かない
  3) Windows: プロジェクトのセット確認は «実体のパス» で比べる
     （ジャンクション経由の同じプロジェクトでは確認を出さない。違うプロジェクトでは出す）
  4) 操作の結果（成功・エラーの文面）が mfm_maya.log に残る
"""
import io
import json
import os
import subprocess
import sys
from _common import *  # noqa: F401,F403
from _common import finish, tmpdir, sm

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 1) 1 回だけ入れる
from ui import main_window_dcc as mwd
sent = []


class _FakeBridge:
    def __init__(self, port):
        self.port = port

    def send_python(self, code, timeout=1.0, tag=""):
        sent.append((self.port, code))
        return True, "save-watch: installed"


_orig_bridge = mwd.MayaBridge
mwd.MayaBridge = _FakeBridge
try:
    class _Host:
        _sm = sm
    h = _Host()
    arm = mwd.MainWindowDccMixin._arm_save_watch if hasattr(mwd.MainWindowDccMixin, "_arm_save_watch") else None
    if arm:
        arm(h, 20261, 1234)
        arm(h, 20261, 1234)
        arm(h, 20262, 5678)
    check(len(sent) == 2 and "_mfm_save_watch" in sent[0][1],
          "接続できた Maya ごとに 1 回だけ見張りを入れる（%d 回送信）" % len(sent))
finally:
    mwd.MayaBridge = _orig_bridge
src = io.open(os.path.join(ROOT, "ui", "main_window_dcc.py"), encoding="utf-8").read()
check("self._arm_save_watch(port, pid)" in src, "接続の確認（スキャン）から見張りを入れる")
check('bridge_log("result tag=' in src, "操作の結果が mfm_maya.log に残る")

if os.name == "nt":
    d = tmpdir("mfm_save_watch_")
    real = os.path.join(d, "real_proj")
    os.makedirs(os.path.join(real, "scenes"))
    open(os.path.join(real, "workspace.mel"), "w").close()
    other = os.path.join(d, "other_proj")
    os.makedirs(other)
    open(os.path.join(other, "workspace.mel"), "w").close()
    junc = os.path.join(d, "junc_proj")
    subprocess.run(["cmd", "/c", "mklink", "/J", junc, real], capture_output=True)
    log = os.path.join(d, "save.log").replace("\\", "/")
    from core.maya_save_watch import install_code
    from core.maya_project import setproject_code
    code = install_code(log, "ja")
    sp_same = setproject_code(os.path.join(junc, "scenes", "a.ma"))
    sp_other = setproject_code(os.path.join(other, "b.ma"))
    script = os.path.join(d, "run.py")
    io.open(script, "w", encoding="utf-8").write(r'''
import os, stat, sys, json
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
real, junc, code, sp_same, sp_other, out = sys.argv[1:7]
code = open(code, encoding="utf-8").read()
r1 = eval(code); r2 = eval(code)
cmds.workspace(real, openWorkspace=True)
p = os.path.join(real, "scenes", "a.ma").replace("\\", "/")
cmds.file(new=True, force=True); cmds.polyCube()
cmds.file(rename=p); cmds.file(save=True, type="mayaAscii", force=True)
cmds.file(p, open=True, force=True)
os.chmod(p, stat.S_IREAD)
err = ""
import maya.mel as mel
try:
    cmds.polySphere()
    mel.eval("file -save")        # File > Save Scene と同じく MEL で保存する
except Exception as e:
    err = str(e)
import __main__
st = __main__._mfm_save_watch
st["check"]()                         # GUI では保存の後に自動で走る
os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
asked = []
cmds.confirmDialog = lambda *a, **k: (asked.append(k.get("message", "")), "x")[1]
exec(open(sp_same, encoding="utf-8").read(), {})
same_asked = len(asked)
exec(open(sp_other, encoding="utf-8").read(), {})
other_asked = len(asked) - same_asked
json.dump({"r1": r1, "r2": r2, "err": err, "same_asked": same_asked,
           "other_asked": other_asked, "ids": len(st["ids"])}, open(out, "w"))
maya.standalone.uninitialize()
''')
    files = {}
    for name, txt in (("code.txt", code), ("same.txt", sp_same), ("other.txt", sp_other)):
        files[name] = os.path.join(d, name)
        io.open(files[name], "w", encoding="utf-8").write(txt)
    out = os.path.join(d, "out.json")
    r = subprocess.run([sys.executable, script, real, junc, files["code.txt"], files["same.txt"],
                        files["other.txt"], out], capture_output=True, text=True, timeout=300)
    res = json.load(open(out)) if os.path.isfile(out) else {}
    if not res:
        print(r.stdout[-1500:], r.stderr[-3000:], flush=True)
    recs = []
    try:
        for ln in io.open(log, encoding="utf-8"):
            recs.append(json.loads(ln))
    except OSError:
        pass
    evs = [x.get("ev") for x in recs]
    print("events:", evs, "err:", res.get("err"), flush=True)
    check(res.get("r1") == "save-watch: installed" and res.get("r2") == "save-watch: already"
          and res.get("ids") == 4, "見張りを 2 回入れても 1 回分（%r）" % ({k: res.get(k) for k in ("r1", "r2", "ids")},))
    saved = [x for x in recs if x.get("ev") == "saved"]
    check(saved and saved[0].get("scene", "").endswith("a.ma") and saved[0].get("workspace")
          and saved[0].get("writable") is True and saved[0].get("scene_in_workspace") is True,
          "保存できた時にシーン・プロジェクト・書き込めるかが記録される（%r）" % (saved[:1],))
    opened = [x for x in recs if x.get("ev") == "opened"]
    check(opened and opened[0].get("workspace"), "開いた時にシーンとプロジェクトが記録される")
    failed = [x for x in recs if x.get("ev") == "save_failed"]
    check(res.get("err") and failed and failed[0].get("writable") is False,
          "保存できなかった時に «保存できなかった» と書き込めない状態が記録される（%r）" % (failed[:1],))
    check(failed and failed[0].get("msgs"),
          "Maya が出したエラーの文面も記録される（%r）" % ((failed[0].get("msgs") if failed else None),))
    check(res.get("same_asked") == 0,
          "ジャンクション経由の同じプロジェクトでは «セットしなおしますか» を出さない（%r）" % res.get("same_asked"))
    check(res.get("other_asked") == 1, "違うプロジェクトでは確認を出す（%r）" % res.get("other_asked"))
    subprocess.run(["cmd", "/c", "rmdir", junc], capture_output=True)
else:
    print("SKIP 本物の Maya での確認（Windows のみ）", flush=True)

finish(not fails)
