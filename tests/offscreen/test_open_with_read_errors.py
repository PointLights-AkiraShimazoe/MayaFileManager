# -*- coding: utf-8 -*-
"""r149（ユーザー報告 2026-11-18）:
  「ファイルを開くと、連携エラーがでました」（プラグインのアトリビュートが無い、が並ぶ）
  「Manager から開いたシーンを Maya の Save Scene で保存しようとすると、何故かエラーが
    出て保存出来ない事があります。一度 Manager で上書き保存をすると、次からは問題なく
    出来る」

確かめること:
  1) «エラー付きで開けた» の応答は «連携エラー» にしない。本当の失敗は今までどおり出す
  2) Windows: 本物の Maya で
     * 読み込み中にエラーが出るシーンでも «開けた» と返る（例外にしない）
     * そのままでは素の保存が止まる（Maya の動き。変わったらここで分かる）
     * «保存できるようにする» を選ぶと、素の保存が通る
     * 開けないファイルは今までどおり例外
     * エラーの無いシーンでは何も聞かない
     * リファレンス・インポートでは起きない（対象を «開く» だけにしている根拠）
"""
import io
import json
import os
import subprocess
import sys
from _common import *  # noqa: F401,F403
from _common import finish, tmpdir

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import maya_open_guard as og
from ui import main_window_dcc as mwd

check(og.parse("OpenedWithErrors:4:saveable") == (4, "saveable")
      and og.parse("OpenedWithErrors:2:kept") == (2, "kept")
      and og.parse("Error: x") is None and og.parse(None) is None,
      "応答から «エラー付きで開けた» を見分ける")
compile(og.SRC, "<maya>", "exec")
src = io.open(os.path.join(ROOT, "ui", "main_window_dcc.py"), encoding="utf-8").read()
check("_og.open_stmt(p" in src and "_og.open_in_this_maya(path)" in src,
      "外から送る時も Maya の中で動く時も、同じ «開く» を使う")

shown, warned = [], []


class _SB:
    def showMessage(self, m, *a):
        shown.append(m)


class _Host:
    def statusBar(self):
        return _SB()


_orig_warn = mwd.QMessageBox.warning
mwd.QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a[1:]))
try:
    mwd.MainWindowDccMixin._on_bridge_done(_Host(), True, "開く a.ma", "OpenedWithErrors:4:kept")
    check(not warned and shown and "a.ma" in shown[-1],
          "エラー付きで開けた時は «連携エラー» を出さず、ステータスバーに出す（%r）" % (shown[-1:],))
    mwd.MainWindowDccMixin._on_bridge_done(_Host(), True, "開く b.ma", "Error: boom")
    check(len(warned) == 1, "本当の失敗は今までどおり «連携エラー» を出す")
finally:
    mwd.QMessageBox.warning = _orig_warn

if os.name == "nt":
    d = tmpdir("mfm_readerr_")
    script = os.path.join(d, "run.py")
    io.open(os.path.join(d, "src.txt"), "w", encoding="utf-8").write(og.SRC)
    io.open(script, "w", encoding="utf-8").write(r'''
import os, sys, json
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds, maya.mel as mel
d, out = sys.argv[1:3]
ns = {}
exec(open(os.path.join(d, "src.txt"), encoding="utf-8").read(), ns)
T = {"head": "H %d", "more": "M %d", "arnold": "ARNOLD", "why": "W", "ask": "A", "yes": "Y", "no": "N"}
res = {}
p = os.path.join(d, "a.ma").replace("\\", "/")
cmds.file(new=True, force=True); cmds.polyCube()
cmds.file(rename=p); cmds.file(save=True, type="mayaAscii", force=True)
s = open(p, encoding="utf-8").read()
def bad(name):
    pb = os.path.join(d, name).replace("\\", "/")
    open(pb, "w", encoding="utf-8").write(s + '\nselect -ne :initialShadingGroup;\n\tsetAttr ".ai_override" yes;\n')
    return pb
def plain():
    cmds.polySphere()
    try:
        mel.eval("file -save"); return "OK"
    except Exception as e:
        return str(e).strip().splitlines()[-1]
asked = []
def dialog(answer):
    cmds.about = (lambda _o: (lambda *a, **k: False if k.get("batch") else _o(*a, **k)))(_about)
    cmds.confirmDialog = lambda *a, **k: (asked.append(k), answer)[1]
_about, _confirm = cmds.about, cmds.confirmDialog
def restore():
    cmds.about, cmds.confirmDialog = _about, _confirm
# 1) 何も聞けない状況（バッチ）: 開けた、と返る。素の保存は止まる
pb = bad("kept.ma"); cmds.file(new=True, force=True)
res["kept"] = ns["_mfm_open"](pb, T)
res["kept_scene"] = os.path.basename(cmds.file(q=True, sceneName=True) or "")
res["kept_save"] = plain()
# 2) «そのままにする» を選ぶ
pb = bad("no.ma"); cmds.file(new=True, force=True); dialog("N")
res["no"] = ns["_mfm_open"](pb, T); restore()
res["no_save"] = plain()
res["msg"] = asked[-1].get("message", "") if asked else ""
res["buttons"] = asked[-1].get("button") if asked else None
# 3) «保存できるようにする» を選ぶ
pb = bad("yes.ma"); cmds.file(new=True, force=True); dialog("Y")
res["yes"] = ns["_mfm_open"](pb, T); restore()
res["yes_save"] = plain()
# 4) 開けないファイル
cmds.file(new=True, force=True)
try:
    ns["_mfm_open"](os.path.join(d, "none.ma").replace("\\", "/"), T); res["missing"] = "no error"
except Exception as e:
    res["missing"] = "raised"
# 5) エラーの無いシーン
n = len(asked); cmds.file(new=True, force=True); dialog("Y")
res["clean"] = ns["_mfm_open"](p, T); restore()
res["clean_asked"] = len(asked) - n
res["clean_save"] = plain()
# 6) リファレンス・インポートでは起きない
for kind in ("reference", "import"):
    pb = bad(kind + "_bad.ma"); cmds.file(new=True, force=True)
    host = os.path.join(d, kind + "_host.ma").replace("\\", "/")
    cmds.polyCone(); cmds.file(rename=host); cmds.file(save=True, type="mayaAscii", force=True)
    err = ""
    try:
        if kind == "reference":
            cmds.file(pb, reference=True, namespace="x", ignoreVersion=True, mergeNamespacesOnClash=False)
        else:
            cmds.file(pb, i=True, ignoreVersion=True, mergeNamespacesOnClash=False)
    except Exception as e:
        err = "raised"
    res[kind] = [err, plain()]
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)
maya.standalone.uninitialize()
''')
    out = os.path.join(d, "out.json")
    r = subprocess.run([sys.executable, script, d, out], capture_output=True, text=True, timeout=300)
    res = json.load(io.open(out, encoding="utf-8")) if os.path.isfile(out) else {}
    if not res:
        print(r.stdout[-1500:], r.stderr[-3000:], flush=True)
    print({k: v for k, v in res.items() if k != "msg"}, flush=True)
    NEED = "A file must be given a name before saving"
    check(str(res.get("kept", "")).startswith("OpenedWithErrors:") and res.get("kept", "").endswith(":kept")
          and res.get("kept_scene") == "kept.ma",
          "読み込み中にエラーが出るシーンでも «開けた» と返る（%r）" % (res.get("kept"),))
    check(NEED in str(res.get("kept_save")) and NEED in str(res.get("no_save")),
          "そのままでは Maya の素の保存が止まる（Maya の動き）")
    check(res.get("buttons") == ["Y", "N"] and "ARNOLD" in res.get("msg", "")
          and "line " in res.get("msg", "") and "kept.ma" not in res.get("msg", ""),
          "何が起きたか（行番号つきのエラー・Arnold の見当）を出して、どうするかを聞く")
    check(str(res.get("yes", "")).endswith(":saveable") and res.get("yes_save") == "OK",
          "«保存できるようにする» を選ぶと、素の保存が通る（%r / %r）" % (res.get("yes"), res.get("yes_save")))
    check(res.get("missing") == "raised", "開けないファイルは今までどおり例外")
    check(res.get("clean") is None and res.get("clean_asked") == 0 and res.get("clean_save") == "OK",
          "エラーの無いシーンでは何も聞かない")
    check(res.get("reference") == ["", "OK"] and res.get("import") == ["", "OK"],
          "リファレンス・インポートでは起きない（%r / %r）" % (res.get("reference"), res.get("import")))
finish(not fails)
