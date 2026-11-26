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
check(sg.choice_keys(lk, True) == ["co", "rw", "no"] and sg.disabled_keys(lk) == ["co"],
      "排他チェックアウト中: «チェックアウトして保存» は見せるが押せない")
check(sg.disabled_keys(st) == ["co"], "他のユーザーがチェックアウト中: «チェックアウトして保存» は見せるが押せない")
check(sg.choice_keys(un, True) == ["unro", "no"], "管理外: «読み取り専用を解除して保存／保存しない»")
check(sg.choice_keys(None, True) == ["co", "rw", "no"] and sg.choice_keys(None, False) == ["unro", "no"],
      "調べられない時: Perforce があれば 3 択、無ければ読み取り専用の 2 択")
nt = {"notco": "N", "others": "O:%s", "locked": "L:%s", "ro": "R", "deleted": "D", "behind": "B:%d/%d"}
plain = sg.parse_fstat("... depotFile //d/x.ma\n... haveRev 4\n... headRev 4\n")
dl = sg.parse_fstat("... depotFile //d/x.ma\n... headAction delete\n")
check(sg.status_lines(plain, True, nt) == ("N", ""), "状態の 1 文: チェックアウトされていません")
check(sg.status_lines(st, True, nt) == ("O:bob, amy", "B:3/5"), "状態の 1 文: 他のユーザー（名前）＋最新でない（%r）" % (sg.status_lines(st, True, nt),))
check(sg.status_lines(lk, True, nt)[0] == "L:bob", "状態の 1 文: 排他チェックアウト")
check(sg.disabled_keys(plain) == [] and sg.disabled_keys(None) == [] and sg.disabled_keys(un) == [],
      "誰もチェックアウトしていない・不明・管理外では押せないボタンは無い")
TT0 = sg.status_texts(True)
check(sg.status_lines(st, True, TT0, scene=True)[1].startswith("現在のシーンは最新版ではありません")
      and sg.status_lines(st, True, TT0)[1].startswith("このファイルは最新版ではありません"),
      "最新でない時の文: Maya では «現在のシーン»、Manager では «このファイル»")
check(sg.status_lines(un, True, nt) == ("R", "") and sg.status_lines(None, False, nt) == ("R", ""),
      "状態の 1 文: 読み取り専用（管理外・Perforce 無し）")
check(sg.status_lines(dl, True, nt)[0] == "D" and sg.choice_keys(dl, True) == ["rw", "no"], "状態の 1 文: 削除済み")
TT = sg.status_texts(True)
check(len(set(TT[k] for k in ("notco", "others", "locked", "ro", "deleted"))) == 5
      and all(TT[k].count("。") == 1 for k in ("notco", "others", "locked", "ro", "deleted")),
      "状態の文は 5 通りとも違い、どれも 1 文")

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
mwd.ask_choice = lambda parent, text, info, buttons, cb, disabled=(): (
    asks.append((text, info, list(buttons), list(disabled))), cb(answer.pop(0) if answer else buttons[-1]))


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
        check(asks and asks[0][2] == [CO, RW, NO] and asks[0][3] == [CO] and "bob" in asks[0][0]
              and sends == [(pth, True)],
              "排他ロック中: チェックアウトを出さず、誰か添えて、書き込み可能にして保存できる（%r）"
              % (asks[0] if asks else None,))
        pth = _save("others.ma", "others", [NO])
        check(asks and asks[0][2] == [CO, RW, NO] and asks[0][3] == [CO] and "bob" in asks[0][0]
              and "#2" in asks[0][1]
              and not sends, "他の人がチェックアウト中: チェックアウトも出し、誰か・最新でないことを添える（%r）"
              % (asks[0] if asks else None,))
        pth = _save("untracked.ma", "untracked", [NO])
        check(asks and asks[0][2] == [T["unro"], NO] and sg.note_texts()["ro"] in asks[0][0] and not sends,
              "管理外: «このファイルは読み取り専用です» と «読み取り専用を解除して保存／保存しない»（%r）"
              % (asks[0] if asks else None,))
        pth = _save("plain.ma", "", [CO])
        check(asks and asks[0][2] == [CO, RW, NO] and asks[0][3] == [] and sends == [(pth, True)],
              "管理下・誰もいない: 3 つとも押せる")
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
def _layout(*a, **k):                       # 押せないボタンがある時の窓（中身は st["last_ask"]）
    la = dict(st.get("last_ask") or {})
    asked.append({"button": la.get("button"), "message": la.get("message", ""), "disabled": la.get("disabled")})
    return answers.pop(0) if answers else "dismiss"
cmds.layoutDialog = _layout
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
            "msg": asked[0]["message"] if asked else "",
            "disabled": [a.get("disabled") for a in asked]}
res = {}
mel.eval('source "FileMenu.mel"')
res["wrap"] = st["wrap_menu"]()
res["what"] = mel.eval("whatIs FileMenu_SaveItem")
res["menu_no"] = run(mk("menu_no.ma"), "FileMenu_SaveItem", "", ["Don't save"])
res["menu_rw"] = run(mk("menu_rw.ma"), "FileMenu_SaveItem", "", ["Make writable and save"])
res["file_rw"] = run(mk("file_rw.ma"), "file -save", "others", ["Make writable and save"])
res["file_no"] = run(mk("file_no.ma"), "file -save", "", ["Don't save"])
res["locked"] = run(mk("locked.ma"), "file -save", "locked", ["Don't save"])
res["untracked"] = run(mk("untracked.ma"), "FileMenu_SaveItem", "untracked", ["Clear read-only and save"])
# r154 修正（実機 2026-11-24）: «エラー付きで開いた» シーン（読み込めないアトリビュート）。
# Maya の素の保存は «A file must be given a name» でチェック用コールバックより前に止まる。
def mk_err(name, ro):
    p = os.path.join(d, name).replace("\\", "/")
    cmds.file(new=True, force=True); cmds.polyCube(name="errCube")
    cmds.file(rename=p); cmds.file(save=True, type="mayaAscii", force=True)
    with open(p, "a") as f:
        f.write('setAttr "errCube.ai_bogus" 1;\n')
    try:
        cmds.file(p, open=True, force=True)
        opened = "clean"
    except Exception:
        opened = "errors"
    if ro:
        os.chmod(p, stat.S_IREAD)
    return p, opened
def bogus(p):
    return "ai_bogus" in open(p).read()
p, res["err_opened"] = mk_err("err_ro_no.ma", True)
res["err_open_flag"] = st.get("open_err") == p
res["err_ro_no"] = run(p, "FileMenu_SaveItem", "", ["Don't save"])
p, _o = mk_err("err_ro_rw.ma", True)
res["err_ro_rw"] = run(p, "FileMenu_SaveItem", "", ["Make writable and save"])
res["err_ro_rw"]["bogus_left"] = bogus(p)
res["err_ro_rw_again"] = run(p, "FileMenu_SaveItem", "", [])
p, _o = mk_err("err_rw_no.ma", False)
res["err_rw_no"] = run(p, "FileMenu_SaveItem", "", ["Don't save"])
p, _o = mk_err("err_rw_save.ma", False)
res["err_rw_save"] = run(p, "FileMenu_SaveItem", "", ["Save"])
res["err_rw_save"]["bogus_left"] = bogus(p)
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
    check(x.get("buttons") == [[CO_, RW_, NO_]] and x.get("disabled") == [[CO_]] and "bob" in x.get("msg", ""),
          "排他チェックアウト中: «チェックアウトして保存» は見せるが押せない（%r）" % (x,))
    x = res.get("file_rw") or {}
    check(x.get("disabled") == [[CO_]], "他のユーザーがチェックアウト中: «チェックアウトして保存» は押せない（%r）" % (x.get("disabled"),))
    x = res.get("menu_no") or {}
    check(x.get("msg", "").splitlines()[:2] == ["menu_no.ma", "This file is not checked out."],
          "Maya 側の文: ファイル名と状態の 1 文だけ（%r）" % (x.get("msg"),))
    x = res.get("untracked") or {}
    check(x.get("buttons") == [["Clear read-only and save", NO_]] and x.get("saved") and x.get("err") == ""
          and x.get("msg", "").splitlines()[1:2] == ["This file is read-only."] and "Perforce" not in x.get("msg", ""),
          "ただの読み取り専用: «読み取り専用を解除して保存／保存しない» の 2 択（%r）" % (x,))
    ERR = "had errors when it was opened"
    check(res.get("err_opened") == "errors" and res.get("err_open_flag"),
          "エラー付きで開いたことを、開いた時点で覚える（%r / %r）" % (res.get("err_opened"), res.get("err_open_flag")))
    x = res.get("err_ro_no") or {}
    check(x.get("buttons") == [[CO_, RW_, NO_]] and ERR in x.get("msg", "") and x.get("err") == ""
          and not x.get("saved"),
          "エラー付き＋読み取り専用で «保存しない»: 先に聞き、Maya のエラーなし（%r）" % (x,))
    x = res.get("err_ro_rw") or {}
    check(x.get("err") == "" and x.get("saved") and x.get("writable") and not x.get("bogus_left"),
          "エラー付き＋読み取り専用で «書き込み可能にして保存»: 1 回で保存できる（%r）" % (x,))
    x = res.get("err_ro_rw_again") or {}
    check(x.get("buttons") == [] and x.get("err") == "" and x.get("saved"),
          "保存できた後の Ctrl+S では何も聞かない（%r）" % (x,))
    x = res.get("err_rw_no") or {}
    check(x.get("buttons") == [["Save", NO_]] and ERR in x.get("msg", "") and x.get("err") == ""
          and not x.get("saved"),
          "エラー付き（書き込める）で «保存しない»: 先に聞き、Maya のエラーなし（%r）" % (x,))
    x = res.get("err_rw_save") or {}
    check(x.get("err") == "" and x.get("saved") and not x.get("bogus_left"),
          "エラー付き（書き込める）で «保存する»: 1 回で保存できる（%r）" % (x,))
else:
    print("SKIP 本物の Maya での確認（Windows のみ）", flush=True)
finish(not fails)
