# -*- coding: utf-8 -*-
"""r154（ユーザー指示 2026-11-24）:
「Maya からの保存時、チェックアウトしてないと一度エラーが出てから選択になるが、
  先に状態をチェックして、チェックアウトする必要があれば選択肢を出す。
  単純に ReadOnly の場合も同じく。他の人にチェックアウトされている時は UE の様に、
  書き込みは出来るようにする。キャンセルすれば保存しないので、Maya のエラーが
  出る事はなくなるのが筋」

確かめること:
  1) p4 fstat の出力から «管理下か・他の人のチェックアウト・排他ロック・最新か» を読む
  2) 選択肢: 管理下で誰もロックしていなければ «チェックアウト／書き込み可能／保存しない»、
     排他ロック中・管理外は «書き込み可能／保存しない»（UE の Make Writable）
  3) Manager の «シーンを保存»: 状態を調べてから聞き、一言（誰がチェックアウト中か等）を添える
  4) Windows: 本物の Maya で
     * File > Save Scene（FileMenu_SaveItem）は保存の前に聞き、断れば保存を呼ばない
       （Maya のエラーが一切出ない）
     * それ以外の保存も保存の前に聞く（直せばそのまま保存、断れば保存しない）
     * 排他ロック中は «チェックアウト» を出さない
"""
import io
import json
import os
import stat
import subprocess
import sys
from _common import *  # noqa: F401,F403
from _common import finish, tmpdir

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


from core import save_target_guard as sg
from core import maya_save_watch as sw
from ui import main_window_dcc as mwd

# 1) 読み取り
st = sg.parse_fstat("... depotFile //d/x.ma\n... haveRev 3\n... headRev 5\n"
                    "... otherOpen0 bob@ws_bob\n... otherOpen1 amy@ws_amy\n... otherOpen 2\n")
check(st["tracked"] and st["others"] == ["bob@ws_bob", "amy@ws_amy"] and not st["locked"]
      and st["have"] == 3 and st["head"] == 5, "fstat を読む（%r）" % (st,))
lk = sg.parse_fstat("... depotFile //d/x.ma\n... otherOpen0 bob@ws\n... otherLock0 bob@ws\n... otherLock \n")
check(lk["locked"], "排他ロックを読む")
un = sg.parse_fstat("D:/w/x.ma - no such file(s).\n")
check(not un["tracked"], "管理外を読む")

# 2) 選択肢と一言
check(sg.choice_keys(st, True) == ["co", "rw", "no"], "他の人がチェックアウト中でも（ロック無し）チェックアウトを出す")
check(sg.choice_keys(lk, True) == ["rw", "no"], "排他ロック中はチェックアウトを出さない（書き込み可能・保存しない）")
check(sg.choice_keys(un, True) == ["rw", "no"], "管理外はチェックアウトを出さない")
check(sg.choice_keys(None, True) == ["co", "rw", "no"] and sg.choice_keys(None, False) == ["rw", "no"],
      "調べられない時は従来どおり")
NT = {"others": "O:%s", "locked": "L:%s", "behind": "B:%d/%d", "untracked": "U"}
n = sg.status_note(st, NT)
check("O:bob（ws_bob）, amy（ws_amy）" in n and "B:3/5" in n, "一言: 誰がチェックアウト中か・最新でない（%r）" % (n,))
check(sg.status_note(lk, NT).startswith("L:bob"), "一言: 排他ロック")
check(sg.status_note(un, NT) == "U" and sg.status_note(None, NT) == "", "一言: 管理外／不明")

# 3) Manager の «シーンを保存»
d = tmpdir("mfm_pre_")
io.open(os.path.join(d, "fakep4.py"), "w", encoding="utf-8").write(r'''
import json, os, stat, sys
here = os.path.dirname(os.path.abspath(__file__))
mode = open(os.path.join(here, "fstat_mode")).read().strip() if os.path.exists(os.path.join(here, "fstat_mode")) else ""
if "fstat" in sys.argv:
    open(os.path.join(here, "fstat_called"), "w").write(" ".join(sys.argv[1:]))
    if mode == "untracked":
        print(sys.argv[-1] + " - no such file(s).")
    else:
        print("... depotFile //depot/x.ma\n... haveRev 2\n... headRev 4")
        if mode in ("others", "locked"):
            print("... otherOpen0 bob@ws_bob\n... otherOpen 1")
        if mode == "locked":
            print("... otherLock0 bob@ws_bob\n... otherLock ")
    sys.exit(0)
os.chmod(sys.argv[-1], stat.S_IREAD | stat.S_IWRITE)
print("//depot/x#1 - opened for edit")
''')
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
CO, RW, NO = T["co"], T["rw"], T["no"]
asks, sends, answer = [], [], []


class _SB:
    def showMessage(self, m, *a):
        pass


class _Host:
    def statusBar(self):
        return _SB()

    def _p4_provider(self):
        return _P4()

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


def _save(name, mode, ans):
    io.open(os.path.join(d, "fstat_mode"), "w").write(mode)
    pth = os.path.join(d, name)
    io.open(pth, "w").write("x")
    os.chmod(pth, stat.S_IREAD)
    del asks[:], sends[:]
    answer[:] = list(ans)
    _Host()._dcc_save_accepted("maya", "save", pth, "code")
    return pth


try:
    probe = os.path.join(d, "probe")
    io.open(probe, "w").write("x")
    os.chmod(probe, stat.S_IREAD)
    if os.access(probe, os.W_OK):
        print("SKIP この環境では読み取り専用にしても書き込める（管理者）", flush=True)
    else:
        pth = _save("locked.ma", "locked", [RW])
        check(asks and asks[0][2] == [RW, NO] and "bob" in asks[0][1] and sends == [(pth, True)],
              "排他ロック中: チェックアウトを出さず、誰か添えて、書き込み可能にして保存できる（%r）"
              % (asks[0][1:] if asks else None,))
        pth = _save("others.ma", "others", [NO])
        check(asks and asks[0][2] == [CO, RW, NO] and "bob" in asks[0][1] and "#2" in asks[0][1]
              and not sends, "他の人がチェックアウト中: チェックアウトも出し、誰か・最新でないことを添える（%r）"
              % (asks[0][1] if asks else None,))
        pth = _save("untracked.ma", "untracked", [NO])
        check(asks and asks[0][2] == [RW, NO] and not sends, "管理外: チェックアウトを出さない")
        pth = _save("plain.ma", "", [CO])
        check(asks and asks[0][2] == [CO, RW, NO] and sends == [(pth, True)], "管理下・誰もいない: 従来どおり")
        check(os.path.isfile(os.path.join(d, "fstat_called"))
              and "-c ws_test -ztag fstat" in io.open(os.path.join(d, "fstat_called")).read(),
              "状態はそのファイルのワークスペースで調べる")
finally:
    mwd.ask_choice = _orig_ask

# 4) 本物の Maya
if os.name == "nt":
    md = tmpdir("mfm_pre_maya_")
    log = os.path.join(md, "save.log").replace("\\", "/")
    io.open(os.path.join(md, "code.txt"), "w", encoding="utf-8").write(sw.install_code(log, "en"))
    io.open(os.path.join(md, "fakep4.py"), "w", encoding="utf-8").write(
        io.open(os.path.join(d, "fakep4.py"), encoding="utf-8").read())
    bat = os.path.join(md, "fakep4.bat")
    io.open(bat, "w").write('@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, os.path.join(md, "fakep4.py")))
    script = os.path.join(md, "run.py")
    io.open(script, "w", encoding="utf-8").write(r'''
import os, stat, sys, json
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds, maya.mel as mel, maya.utils
import __main__
d, out, bat = sys.argv[1:4]
eval(open(os.path.join(d, "code.txt"), encoding="utf-8").read())
st = __main__._mfm_save_watch
__main__._mfm_p4 = {"exe": bat, "base": ["-p", "srv:1666"], "clients": [[d, "ws_test"]]}
_about = cmds.about
cmds.about = lambda *a, **k: False if k.get("batch") else _about(*a, **k)
maya.utils.executeDeferred = lambda *a, **k: None
asked, answers = [], []
def _dlg(*a, **k):
    asked.append({"button": k.get("button"), "message": k.get("message", "")})
    return answers.pop(0) if answers else "OK"
cmds.confirmDialog = _dlg
def mk(name):
    p = os.path.join(d, name).replace("\\", "/")
    cmds.file(new=True, force=True); cmds.polyCube()
    cmds.file(rename=p); cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(p, open=True, force=True)
    os.chmod(p, stat.S_IREAD)
    return p
def run(p, how, mode, ans):
    open(os.path.join(d, "fstat_mode"), "w").write(mode)
    del asked[:]; answers[:] = list(ans)
    sz0 = os.path.getsize(p); m0 = os.path.getmtime(p)
    cmds.polySphere()
    err = ""
    try:
        mel.eval(how)
    except Exception as e:
        err = str(e)
    return {"err": err, "saved": os.path.getsize(p) != sz0 or os.path.getmtime(p) != m0,
            "writable": os.access(p, os.W_OK), "buttons": [a["button"] for a in asked],
            "msg": asked[0]["message"] if asked else ""}
res = {}
mel.eval('source "FileMenu.mel"')
res["wrap"] = st["wrap_menu"]()
res["what"] = mel.eval("whatIs FileMenu_SaveItem")
res["menu_no"] = run(mk("menu_no.ma"), "FileMenu_SaveItem", "", ["Don't save"])
res["menu_rw"] = run(mk("menu_rw.ma"), "FileMenu_SaveItem", "", ["Make writable and save"])
res["file_rw"] = run(mk("file_rw.ma"), "file -save", "others", ["Make writable and save"])
res["file_no"] = run(mk("file_no.ma"), "file -save", "", ["Don't save"])
res["locked"] = run(mk("locked.ma"), "file -save", "locked", ["Don't save"])
res["wrap2"] = st["wrap_menu"]()
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)
maya.standalone.uninitialize()
''')
    out = os.path.join(md, "out.json")
    r = subprocess.run([sys.executable, script, md, out, bat], capture_output=True, text=True, timeout=300)
    res = json.load(io.open(out, encoding="utf-8")) if os.path.isfile(out) else {}
    if not res:
        print(r.stdout[-1500:], r.stderr[-3000:], flush=True)
    print(res, flush=True)
    CO_, RW_, NO_ = "Check out and save", "Make writable and save", "Don't save"
    check(str(res.get("wrap", "")).startswith("menu-wrap: ok") and str(res.get("wrap2", "")).startswith("menu-wrap: ok"),
          "File > Save Scene（FileMenu_SaveItem）の前に確認を挟める（%r / %r）" % (res.get("wrap"), res.get("wrap2")))
    x = res.get("menu_no") or {}
    check(x.get("buttons") == [[CO_, RW_, NO_]] and x.get("err") == "" and not x.get("saved"),
          "Save Scene で «保存しない»: 保存の前に聞き、保存を呼ばない（Maya のエラーなし）（%r）" % (x,))
    x = res.get("menu_rw") or {}
    check(x.get("buttons") == [[CO_, RW_, NO_]] and x.get("err") == "" and x.get("saved") and x.get("writable"),
          "Save Scene で «書き込み可能にして保存»: 1 回で保存される（%r）" % (x,))
    x = res.get("file_rw") or {}
    check(x.get("err") == "" and x.get("saved") and "bob" in x.get("msg", ""),
          "それ以外の保存も保存の前に聞き、誰がチェックアウト中か添える（%r）" % (x,))
    x = res.get("file_no") or {}
    check(not x.get("saved") and not x.get("writable") and len(x.get("buttons") or []) == 1,
          "それ以外の保存で «保存しない»: 保存しない・ファイルに触らない（%r）" % (x,))
    x = res.get("locked") or {}
    check(x.get("buttons") == [[RW_, NO_]] and "bob" in x.get("msg", ""),
          "排他ロック中はチェックアウトを出さない（%r）" % (x,))
else:
    print("SKIP 本物の Maya での確認（Windows のみ）", flush=True)
finish(not fails)
