# -*- coding: utf-8 -*-
"""r149（ユーザー報告 2026-11-18、記録は 2026-11-13 の 2 件）:
  読み取り専用のシーン（Perforce でチェックアウトしていないファイル）は、Maya の
  Save Scene が «Cannot save changes to read only file» で止まる。Manager の
  «シーンを保存» は強制の保存なので通り、**チェックアウト無しで書き換えていた**
  （Perforce から見ると知らない変更になる）。

確かめること:
  1) Perforce の接続（どのフォルダがどのワークスペースか）を Maya へ渡す
  2) Manager の «シーンを保存» は、上書き先が読み取り専用なら黙って書き換えない。
     r150（ユーザー指摘 2026-11-18）: Manager で聞き、チェックアウト／読み取り専用の
     解除も Manager が行ってから送る（Maya 側では聞かない）
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


# 2) Manager の «シーンを保存»（r150: 判断も処置も Manager で。Maya 側では聞かない）
def _inner(code):
    i = code.index("exec(") + 5
    j = code.rindex(", _ns)")
    return eval(code[i:j])


from core import save_target_guard as sg
inner = _inner(dcc_save.maya_code("D:/w/a.ma", "save", {}))
check("_mfm_save_watch" not in inner and "ro_fix" not in inner and "W_OK" not in inner,
      "Manager から送る保存のコードに Maya 側の確認を入れない")

d = tmpdir("mfm_ro_")
_orig_lp = sw.log_path
sw.log_path = lambda: os.path.join(d, "save.log").replace("\\", "/")
# 偽の p4（引数を残し、成功なら書き込めるようにする）
io.open(os.path.join(d, "fakep4.py"), "w", encoding="utf-8").write(r"""
import json, os, stat, sys
here = os.path.dirname(os.path.abspath(__file__))
if "fstat" in sys.argv:                       # r154: 保存の前の状態確認
    mode = open(os.path.join(here, "fstat_mode")).read().strip() if os.path.exists(os.path.join(here, "fstat_mode")) else ""
    if mode == "untracked":
        print(sys.argv[-1] + " - no such file(s).")
    else:
        print("... depotFile //depot/x.ma\n... haveRev 1\n... headRev 1")
        if mode in ("others", "locked"):
            print("... otherOpen0 bob@ws_bob\n... otherOpen 1")
        if mode == "locked":
            print("... otherLock0 bob@ws_bob\n... otherLock ")
    sys.exit(0)
json.dump(sys.argv[1:], open(os.path.join(here, "p4_args.json"), "w"))
if os.path.exists(os.path.join(here, "p4_fail")):
    print(sys.argv[-1] + " - file(s) not on client.")
else:
    os.chmod(sys.argv[-1], stat.S_IREAD | stat.S_IWRITE)
    print("//depot/x#1 - opened for edit")
""")
if os.name == "nt":
    exe = os.path.join(d, "fakep4.bat")
    io.open(exe, "w").write('@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, os.path.join(d, "fakep4.py")))
else:
    exe = os.path.join(d, "fakep4.sh")
    io.open(exe, "w").write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, os.path.join(d, "fakep4.py")))
    os.chmod(exe, 0o755)


class _P4:
    key = "p4"
    available = True
    p4 = exe

    def conn_args_for(self, directory):
        return ["-p", "srv:1666", "-c", "ws_test"]


T = sg.texts()
CO2, RW2, NO2 = T["co"], T["rw"], T["no"]
asks, sends, shown, answer = [], [], [], []


class _SB:
    def showMessage(self, m, *a):
        shown.append(m)


class _SaveHost:
    prov = _P4()

    def statusBar(self):
        return _SB()

    def _p4_provider(self):
        return self.prov

    def _run_bg(self, work, done):
        done(work())

    def _dcc_once(self, *a):
        return True

    def _dcc_begin_op(self, *a):
        pass

    def _dcc_end_op(self):
        pass

    def _dcc_save_send(self, dcc, code, label, act, path):
        sends.append((path, os.access(path, os.W_OK)))

    _dcc_save_accepted = mwd.MainWindowDccMixin._dcc_save_accepted
    _ask_readonly_then = mwd.MainWindowDccMixin._ask_readonly_then


_orig_ask = mwd.ask_choice
mwd.ask_choice = lambda parent, text, info, buttons, cb: (
    asks.append((text, info, list(buttons))), cb(answer.pop(0) if answer else buttons[-1]))


def _save(name, ans, ro=True, prov=True):
    pth = os.path.join(d, name)
    io.open(pth, "w").write("x")
    if ro:
        os.chmod(pth, stat.S_IREAD)
    del asks[:], sends[:]
    answer[:] = list(ans)
    h = _SaveHost()
    if not prov:
        h.prov = None
    h._dcc_save_accepted("maya", "save", pth, "code")
    return pth


try:
    probe = os.path.join(d, "probe")
    io.open(probe, "w").write("x")
    os.chmod(probe, stat.S_IREAD)
    if os.access(probe, os.W_OK):
        print("SKIP この環境では読み取り専用にしても書き込める（管理者）", flush=True)
    else:
        pth = _save("no.ma", [NO2])
        check(asks and asks[0][2] == [CO2, RW2, NO2] and not sends and not os.access(pth, os.W_OK),
              "保存しない: Manager で聞いて、送らない・ファイルに触らない（%r / 送信 %d）"
              % (asks[0][2] if asks else None, len(sends)))
        check(asks and asks[0][0].count("\n") <= 1 and "no.ma" in asks[0][0],
              "聞く文は短い（%r）" % (asks[0][0] if asks else "",))
        pth = _save("rw.ma", [RW2])
        check(sends == [(pth, True)] and len(asks) == 1,
              "読み取り専用を外して保存: Manager が外してから送る（%r）" % (sends,))
        pth = _save("co.ma", [CO2])
        args = json.load(io.open(os.path.join(d, "p4_args.json")))
        check(sends == [(pth, True)] and args[:5] == ["-p", "srv:1666", "-c", "ws_test", "edit"]
              and os.path.normcase(args[-1]) == os.path.normcase(pth),
              "チェックアウトして保存: Manager が p4 edit（そのワークスペースの -c）してから送る（%r）"
              % (args[:5],))
        io.open(os.path.join(d, "p4_fail"), "w").close()
        pth = _save("cofail.ma", [CO2, NO2])
        check(len(asks) == 2 and asks[1][2] == [RW2, NO2] and "not on client" in asks[1][1]
              and not sends and not os.access(pth, os.W_OK),
              "チェックアウトできない時: 理由を出し、残りの選択肢だけで聞き直す（%r）"
              % ([a[2] for a in asks],))
        pth = _save("noprov.ma", [NO2], prov=False)
        check(asks and asks[0][2] == [RW2, NO2], "Perforce が無い時は «チェックアウト» を出さない")
        pth = _save("ok.ma", [], ro=False)
        check(not asks and sends == [(pth, True)], "書き込める上書き先では何も聞かない")
        recs = [json.loads(ln) for ln in io.open(sw.log_path(), encoding="utf-8")]
        fix = [(x.get("how"), x.get("ok"), x.get("by")) for x in recs if x.get("ev") == "ro_fix"]
        check(("chmod", True, "manager") in fix and ("p4 edit", True, "manager") in fix
              and ("p4 edit", False, "manager") in fix and ("declined", False, "manager") in fix,
              "Manager で直した内容も記録に残る（%r）" % (fix,))
finally:
    mwd.ask_choice = _orig_ask
    sw.log_path = _orig_lp
    _main.__dict__.pop("_mfm_p4", None)

# 3) 本物の Maya
if os.name == "nt":
    d = tmpdir("mfm_ro_maya_")
    log = os.path.join(d, "save.log").replace("\\", "/")
    io.open(os.path.join(d, "code.txt"), "w", encoding="utf-8").write(sw.install_code(log, "en"))
    io.open(os.path.join(d, "fakep4.py"), "w", encoding="utf-8").write(r'''
import json, os, stat, sys
here = os.path.dirname(os.path.abspath(__file__))
if "fstat" in sys.argv:                       # r154: 保存の前の状態確認
    mode = open(os.path.join(here, "fstat_mode")).read().strip() if os.path.exists(os.path.join(here, "fstat_mode")) else ""
    if mode == "untracked":
        print(sys.argv[-1] + " - no such file(s).")
    else:
        print("... depotFile //depot/x.ma\n... haveRev 1\n... headRev 1")
        if mode in ("others", "locked"):
            print("... otherOpen0 bob@ws_bob\n... otherOpen 1")
        if mode == "locked":
            print("... otherLock0 bob@ws_bob\n... otherLock ")
    sys.exit(0)
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
    check(x.get("first") == "" and x.get("buttons") == [[CO, RW, NO]] and x.get("writable")
          and x.get("saved") and x.get("modified") is False,
          "書き込み可能にして保存: 保存の前に聞いて、外して、そのまま保存する（失敗させない）（%r）" % (x,))
    x = res.get("co") or {}
    check(x.get("first") == "" and x.get("writable") and x.get("saved") and res.get("co_path_ok")
          and res.get("co_args", [])[:5] == ["-p", "srv:1666", "-c", "ws_test", "edit"],
          "チェックアウトして保存: p4 edit を実行して保存し直す（%r）" % (res.get("co_args", [])[:5],))
    x = res.get("cofail") or {}
    check(x.get("buttons") == [[CO, RW, NO], [RW, NO]] and not x.get("writable") and not x.get("saved")
          and "not on client" in res.get("cofail_msg2", ""),
          "チェックアウトできない時: 理由を出し、残りの選択肢だけで聞き直す（%r）" % (x.get("buttons"),))
    x = res.get("no") or {}
    check(x.get("buttons") == [[CO, RW, NO]] and not x.get("writable") and not x.get("saved"),
          "保存しない: 保存の前に止める。ファイルに触らない。お知らせも重ねて出さない（%r）" % (x.get("buttons"),))
    x = res.get("ok") or {}
    check(x.get("first") == "" and x.get("buttons") == [] and x.get("saved"),
          "書き込めるファイルの保存では何も聞かない")
    check(("chmod", True) in fix and ("p4 edit", True) in fix and ("p4 edit", False) in fix
          and ("declined", False) in fix, "直した内容が記録に残る（%r）" % (fix,))
else:
    print("SKIP 本物の Maya での確認（Windows のみ）", flush=True)
finish(not fails)
