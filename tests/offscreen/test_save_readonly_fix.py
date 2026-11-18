# -*- coding: utf-8 -*-
"""r149（ユーザー報告 2026-11-18、記録は 2026-11-13 の 2 件）:
  読み取り専用のシーン（Perforce でチェックアウトしていないファイル）は、Maya の
  Save Scene が «Cannot save changes to read only file» で止まる。Manager の
  «シーンを保存» は強制の保存なので通り、**チェックアウト無しで書き換えていた**
  （Perforce から見ると知らない変更になる）。

確かめること:
  1) Perforce の接続（どのフォルダがどのワークスペースか）を Maya へ渡す
  2) Manager の «シーンを保存» は、上書き先が読み取り専用なら黙って書き換えない
  3) Windows: 本物の Maya で
     * 保存に失敗した理由が読み取り専用なら «チェックアウトして保存／読み取り専用を
       外して保存／保存しない» を聞き、選んだとおりに直して保存し直す
     * チェックアウトは、そのファイルが入っているワークスペースの -c で実行する
     * チェックアウトできなかった時は理由を出し、残りの選択肢だけで聞き直す
     * 断った時はファイルに触らない
     * 書き込めるファイルの保存では何も聞かない
"""
import io
import json
import os
import stat
import subprocess
import sys
import types
from _common import *  # noqa: F401,F403
from _common import finish, tmpdir, sm

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


from core import dcc_save
from core import maya_save_watch as sw
from ui import main_window_dcc as mwd

# 1) 接続の表
class _Prov:
    key = "p4"
    available = True
    p4 = "C:/tools/p4.exe"
    _conn_args = ["-p", "srv:1666", "-u", "me", "-c", "main_ws"]
    _clients = [("d:\\work\\long", "ws_long", "D:\\work\\long"), ("d:\\work", "ws_short", "D:\\work")]


t = sw.p4_table(_Prov())
check(t.get("exe") == "C:/tools/p4.exe" and t.get("base") == _Prov._conn_args
      and t.get("clients") == [["D:\\work\\long", "ws_long"], ["D:\\work", "ws_short"]],
      "Perforce の接続の表を作る（%r）" % (t,))
_Prov.available = False
check(sw.p4_table(_Prov()) == {} and sw.p4_table(None) == {}, "Perforce が使えない時は空の表")
_Prov.available = True
import __main__ as _main
check(eval(sw.p4_table_code(t)) == "p4-table" and _main.__dict__.get("_mfm_p4") == t,
      "表を渡す式は __main__ へ置く")
for lang in ("ja", "en"):
    compile(sw._maya_source("C:/x/log.txt", lang), "<maya>", "exec")

sent = []


class _FakeBridge:
    def __init__(self, port):
        self.port = port

    def send_python(self, code, timeout=1.0, tag=""):
        sent.append(code)
        return True, ("p4-table" if "_mfm_p4" in code and "_mfm_save_watch" not in code
                      else "save-watch: installed")


_orig_bridge = mwd.MayaBridge
mwd.MayaBridge = _FakeBridge
try:
    class _Host:
        _sm = sm
        _push_p4_table = mwd.MainWindowDccMixin._push_p4_table
    h = _Host()
    mwd.MainWindowDccMixin._arm_save_watch(h, 20261, 1234)
    n1 = len(sent)
    mwd.MainWindowDccMixin._arm_save_watch(h, 20261, 1234)
    check(n1 == 2 and "_mfm_save_watch" in sent[0] and "_mfm_p4" in sent[1] and len(sent) == 2,
          "見張りを入れた Maya へ接続の表も渡す。同じ中身は渡し直さない（%d → %d 回）" % (n1, len(sent)))
finally:
    mwd.MayaBridge = _orig_bridge


# 2) Manager の «シーンを保存»
def _inner(code):
    i = code.index("exec(") + 5
    j = code.rindex(", _ns)")
    return eval(code[i:j])


d = tmpdir("mfm_ro_")
target = os.path.join(d, "a.ma")
io.open(target, "w").write("x")
os.chmod(target, stat.S_IREAD)
calls = []
fake = types.ModuleType("maya")
fake.cmds = types.ModuleType("maya.cmds")
fake.cmds.file = lambda *a, **k: calls.append(k)
fake.cmds.confirmDialog = lambda *a, **k: "OK"
_saved_mods = {k: sys.modules.get(k) for k in ("maya", "maya.cmds")}
sys.modules["maya"], sys.modules["maya.cmds"] = fake, fake.cmds
try:
    inner = _inner(dcc_save.maya_code(target, "save", {}))
    if os.access(target, os.W_OK):
        print("SKIP この環境では読み取り専用にしても書き込める（管理者）", flush=True)
    else:
        asked = []
        _main.__dict__["_mfm_save_watch"] = {"ro_fix": lambda p: (asked.append(p), False)[1]}
        ns = {}
        exec(inner, ns)
        check(ns.get("_mfm_result") == "Cancelled" and not calls and len(asked) == 1,
              "読み取り専用の上書き先: 断ったら保存しない（%r / file %d 回）" % (ns.get("_mfm_result"), len(calls)))
        _main.__dict__["_mfm_save_watch"] = {"ro_fix": lambda p: True}
        ns = {}
        exec(inner, ns)
        check(str(ns.get("_mfm_result", "")).startswith("saved:") and len(calls) == 2,
              "読み取り専用の上書き先: 直せたら保存する（%r）" % (ns.get("_mfm_result"),))
    os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
    del calls[:]
    _main.__dict__["_mfm_save_watch"] = {"ro_fix": lambda p: (_ for _ in ()).throw(AssertionError("asked"))}
    ns = {}
    exec(inner, ns)
    check(str(ns.get("_mfm_result", "")).startswith("saved:") and len(calls) == 2,
          "書き込める上書き先では何も聞かない")
    _main.__dict__.pop("_mfm_save_watch", None)
    del calls[:]
    ns = {}
    exec(inner, ns)
    check(len(calls) == 2, "見張りが入っていない Maya では従来どおり保存する")
finally:
    _main.__dict__.pop("_mfm_save_watch", None)
    _main.__dict__.pop("_mfm_p4", None)
    for k, v in _saved_mods.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v

# 3) 本物の Maya
if os.name == "nt":
    log = os.path.join(d, "save.log").replace("\\", "/")
    io.open(os.path.join(d, "code.txt"), "w", encoding="utf-8").write(sw.install_code(log, "en"))
    io.open(os.path.join(d, "mfmsave.txt"), "w", encoding="utf-8").write(
        _inner(dcc_save.maya_code(os.path.join(d, "ws", "m.ma"), "save", {})))
    io.open(os.path.join(d, "fakep4.py"), "w", encoding="utf-8").write(r'''
import json, os, stat, sys
here = os.path.dirname(os.path.abspath(__file__))
json.dump(sys.argv[1:], open(os.path.join(here, "p4_args.json"), "w"))
if os.path.exists(os.path.join(here, "p4_fail")):
    print(sys.argv[-1] + " - file(s) not on client.")
else:
    os.chmod(sys.argv[-1], stat.S_IREAD | stat.S_IWRITE)
    print("//depot/x#1 - opened for edit")
''')
    bat = os.path.join(d, "fakep4.bat")
    io.open(bat, "w").write('@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, os.path.join(d, "fakep4.py")))
    script = os.path.join(d, "run.py")
    io.open(script, "w", encoding="utf-8").write(r'''
import os, stat, sys, json
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds, maya.mel as mel, maya.utils
import __main__
d, out, bat = sys.argv[1:4]
eval(open(os.path.join(d, "code.txt"), encoding="utf-8").read())
st = __main__._mfm_save_watch
ws = os.path.join(d, "ws"); os.makedirs(ws)
__main__._mfm_p4 = {"exe": bat, "base": ["-p", "srv:1666", "-c", "other_ws"],
                    "clients": [[d, "ws_outer"], [ws, "ws_test"]]}
res = {}
_about = cmds.about
cmds.about = lambda *a, **k: False if k.get("batch") else _about(*a, **k)
maya.utils.executeDeferred = lambda *a, **k: None     # GUI では保存の後に走る。ここでは手で呼ぶ
asked = []
answers = []
def _dlg(*a, **k):
    asked.append({"button": k.get("button"), "message": k.get("message", "")})
    return answers.pop(0) if answers else "OK"
cmds.confirmDialog = _dlg
CO, RW, NO = "Check out and save", "Make writable and save", "Don't save"
def mk(name):
    p = os.path.join(ws, name).replace("\\", "/")
    cmds.file(new=True, force=True); cmds.polyCube()
    cmds.file(rename=p); cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(p, open=True, force=True)
    os.chmod(p, stat.S_IREAD)
    return p
def save_via_menu(p, ans):
    """File > Save Scene と同じ MEL。失敗 → 見張りの後始末（GUI では自動）。"""
    del asked[:]; answers[:] = list(ans)
    m0 = os.path.getmtime(p); sz0 = os.path.getsize(p)
    cmds.polySphere()
    first = ""
    try:
        mel.eval("file -save")
    except Exception as e:
        first = "failed"
    st["check"]()
    return {"first": first, "writable": os.access(p, os.W_OK),
            "saved": os.path.getsize(p) != sz0 or os.path.getmtime(p) != m0,
            "buttons": [a["button"] for a in asked], "modified": cmds.file(q=True, modified=True)}
res["p4_cmd"] = st["p4_cmd"](os.path.join(ws, "x.ma"))
res["p4_cmd_outer"] = st["p4_cmd"](os.path.join(d, "y.ma"))
res["rw"] = save_via_menu(mk("rw.ma"), [RW])
p = mk("co.ma")
res["co"] = save_via_menu(p, [CO])
res["co_args"] = json.load(open(os.path.join(d, "p4_args.json")))
res["co_path_ok"] = os.path.normcase(os.path.normpath(res["co_args"][-1])) == os.path.normcase(os.path.normpath(p))
open(os.path.join(d, "p4_fail"), "w").close()
res["cofail"] = save_via_menu(mk("cofail.ma"), [CO, NO])
res["cofail_msg2"] = asked[1]["message"] if len(asked) > 1 else ""
res["no"] = save_via_menu(mk("no.ma"), [NO])
# 書き込めるファイル
p = os.path.join(ws, "ok.ma").replace("\\", "/")
cmds.file(new=True, force=True); cmds.polyCube(); cmds.file(rename=p); cmds.file(save=True, type="mayaAscii", force=True)
res["ok"] = save_via_menu(p, [])
# Manager の «シーンを保存»（上書き先が読み取り専用）
p = mk("m.ma")
src = open(os.path.join(d, "mfmsave.txt"), encoding="utf-8").read()
del asked[:]; answers[:] = [NO]
ns = {}; exec(src, ns)
res["mfm_no"] = [ns.get("_mfm_result"), os.access(p, os.W_OK), len(asked)]
del asked[:]; answers[:] = [RW]
ns = {}; exec(src, ns)
res["mfm_rw"] = [str(ns.get("_mfm_result"))[:6], os.access(p, os.W_OK), len(asked)]
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)
maya.standalone.uninitialize()
''')
    out = os.path.join(d, "out.json")
    r = subprocess.run([sys.executable, script, d, out, bat], capture_output=True, text=True, timeout=300)
    res = json.load(io.open(out, encoding="utf-8")) if os.path.isfile(out) else {}
    if not res:
        print(r.stdout[-1500:], r.stderr[-3000:], flush=True)
    print({k: v for k, v in res.items() if k != "cofail_msg2"}, flush=True)
    recs = []
    try:
        for ln in io.open(log, encoding="utf-8"):
            recs.append(json.loads(ln))
    except OSError:
        pass
    fix = [(x.get("how"), x.get("ok")) for x in recs if x.get("ev") == "ro_fix"]
    print("ro_fix:", fix, flush=True)
    CO, RW, NO = "Check out and save", "Make writable and save", "Don't save"
    check(res.get("p4_cmd", [None])[1:] == ["-p", "srv:1666", "-c", "ws_test"]
          and res.get("p4_cmd_outer", [None])[1:] == ["-p", "srv:1666", "-c", "ws_outer"],
          "p4 の引数は «そのファイルが入っているワークスペース» の -c に差し替える（%r）" % (res.get("p4_cmd"),))
    x = res.get("rw") or {}
    check(x.get("first") == "failed" and x.get("buttons") == [[CO, RW, NO]] and x.get("writable")
          and x.get("saved") and x.get("modified") is False,
          "読み取り専用を外して保存: 聞いて、外して、保存し直す（%r）" % (x,))
    x = res.get("co") or {}
    check(x.get("writable") and x.get("saved") and res.get("co_path_ok")
          and res.get("co_args", [])[:5] == ["-p", "srv:1666", "-c", "ws_test", "edit"],
          "チェックアウトして保存: p4 edit を実行して保存し直す（%r）" % (res.get("co_args", [])[:5],))
    x = res.get("cofail") or {}
    check(x.get("buttons") == [[CO, RW, NO], [RW, NO]] and not x.get("writable") and not x.get("saved")
          and "not on client" in res.get("cofail_msg2", ""),
          "チェックアウトできない時: 理由を出し、残りの選択肢だけで聞き直す（%r）" % (x.get("buttons"),))
    x = res.get("no") or {}
    check(x.get("buttons") == [[CO, RW, NO]] and not x.get("writable") and not x.get("saved"),
          "保存しない: ファイルに触らない。お知らせも重ねて出さない（%r）" % (x.get("buttons"),))
    x = res.get("ok") or {}
    check(x.get("first") == "" and x.get("buttons") == [] and x.get("saved"),
          "書き込めるファイルの保存では何も聞かない")
    check(res.get("mfm_no") == ["Cancelled", False, 1] and res.get("mfm_rw") == ["saved:", True, 1],
          "Manager の «シーンを保存» も同じ確認を通す（%r / %r）" % (res.get("mfm_no"), res.get("mfm_rw")))
    check(("chmod", True) in fix and ("p4 edit", True) in fix and ("p4 edit", False) in fix
          and ("declined", False) in fix, "直した内容が記録に残る（%r）" % (fix,))
else:
    print("SKIP 本物の Maya での確認（Windows のみ）", flush=True)
finish(not fails)
