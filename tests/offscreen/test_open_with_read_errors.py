# -*- coding: utf-8 -*-
"""r149（ユーザー報告 2026-11-18）:
  「ファイルを開くと、連携エラーがでました」（プラグインのアトリビュートが無い、が並ぶ）
  「Manager から開いたシーンを Maya の Save Scene で保存しようとすると、何故かエラーが
    出て保存出来ない事があります。一度 Manager で上書き保存をすると、次からは問題なく
    出来る」
r150（ユーザー指示 2026-11-18、H-14 の実機確認）:
  「文面が長すぎます。端的にエラーが起きたことと読み取り専用を解除する事を伝えて
    意思確認をしてください。詳細はスクリプトエディターで大丈夫です。maya の標準の
    エラーログと被ってでてしまい、ボタンが見えずに maya 操作ができなくなったかと
    思いました」

確かめること:
  1) «エラー付きで開けた» の応答は «連携エラー» にしない。本当の失敗は今までどおり出す
  2) どうするかは Manager で短く聞く（2 行まで・エラーの中身は出さない）。
     «解除する» は、開いた Maya（同じ送り先）へ解除を送る。«解除しない» は何も送らない
  3) Maya 側ではダイアログを出さない（SRC に confirmDialog が無い）
  4) Windows: 本物の Maya で
     * 読み込み中にエラーが出るシーンでも «開けた» と返る（例外にしない・何も聞かない）
     * 詳細（エラーの行・Arnold の見当）はスクリプトエディターに出る
     * そのままでは素の保存が止まる（Maya の動き。変わったらここで分かる）
     * 解除を送ると素の保存が通る。シーンが変わっていたら何もしない
     * 開けないファイルは今までどおり例外。エラーの無いシーンでは何も返さない
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

check(og.parse("OpenedWithErrors:4:pending:D:/w/a b.ma") == (4, "pending", "D:/w/a b.ma")
      and og.parse("OpenedWithErrors:2:kept") == (2, "kept", "")
      and og.parse("Error: x") is None and og.parse(None) is None
      and og.parse_unlock("MfmUnlocked:ok") == "ok" and og.parse_unlock("saved:x") is None,
      "応答から «エラー付きで開けた»・«解除した» を見分ける（パスに : があっても）")
compile(og.SRC, "<maya>", "exec")
compile(og.unlock_code("D:\\w\\it's.ma"), "<maya>", "eval")
check("confirmDialog" not in og.SRC and "confirmDialog" not in og.UNLOCK_SRC,
      "Maya 側ではダイアログを出さない")
src = io.open(os.path.join(ROOT, "ui", "main_window_dcc.py"), encoding="utf-8").read()
check("_og.open_stmt(p" in src and "_og.open_in_this_maya(path)" in src,
      "外から送る時も Maya の中で動く時も、同じ «開く» を使う")

shown, warned, asks, sent = [], [], [], []


class _SB:
    def showMessage(self, m, *a):
        shown.append(m)


class _Bridge:
    def __init__(self, port):
        self.port = port

    def send_python(self, code, timeout=1.0, tag=""):
        sent.append((self.port, code))
        return True, "MfmUnlocked:ok"


class _Host:
    _inside_maya = False
    _last_done_port = 20261
    _bridge = None

    def statusBar(self):
        return _SB()

    def _run_bg(self, work, done):          # テストでは同じスレッドで
        done(work())

    _ask_unlock_after_open = mwd.MainWindowDccMixin._ask_unlock_after_open
    _show_unlock_result = mwd.MainWindowDccMixin._show_unlock_result
    _on_bridge_done = mwd.MainWindowDccMixin._on_bridge_done


answer = []
_orig = (mwd.QMessageBox.warning, mwd.ask_choice, mwd.MayaBridge)
mwd.QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a[1:]))
mwd.ask_choice = lambda parent, text, info, buttons, cb: (
    asks.append((text, info, list(buttons))), cb(answer.pop(0) if answer else buttons[-1]))
mwd.MayaBridge = _Bridge
try:
    T = og.ask_texts()
    answer[:] = [T["no"]]
    _Host()._on_bridge_done(True, "開く a.ma", "OpenedWithErrors:4:pending:D:/w/a.ma")
    check(not warned and shown and "a.ma" in shown[0],
          "エラー付きで開けた時は «連携エラー» を出さず、ステータスバーに出す（%r）" % (shown[:1],))
    txt, info, btns = asks[-1] if asks else ("", "", [])
    check(len(asks) == 1 and btns == [T["yes"], T["no"]] and txt.count("\n") <= 1
          and "上書き保存を止めています" in txt and "エラー" in txt and "line" not in txt
          and "読み取り専用" not in txt + info
          and "詳細はスクリプトエディターをご確認ください。" in info,
          "Manager で短く聞く（%r / %r）" % (txt, btns))
    check(not sent and "Save Scene As" in shown[-1], "«解除しない» は何も送らない（%r）" % (shown[-1],))
    del shown[:], asks[:]
    answer[:] = [T["yes"]]
    _Host()._on_bridge_done(True, "開く a.ma", "OpenedWithErrors:4:pending:D:/w/a.ma")
    check(len(sent) == 1 and sent[0][0] == 20261 and "_mfm_unlock" in sent[0][1]
          and "D:/w/a.ma" in sent[0][1],
          "«解除する» は開いた Maya（同じ送り先）へ解除を送る（%r）" % ([s[0] for s in sent],))
    check(shown and "上書き保存できるようにしました" in shown[-1] and not warned,
          "解除できたらステータスバーに出す（%r）" % (shown[-1:],))
    mwd.MainWindowDccMixin._on_bridge_done(_Host(), True, "開く b.ma", "Error: boom")
    check(len(warned) == 1, "本当の失敗は今までどおり «連携エラー» を出す")
finally:
    mwd.QMessageBox.warning, mwd.ask_choice, mwd.MayaBridge = _orig

# 本物の ask_choice: 非モーダル・常に手前。押したボタン、閉じた時は最後のボタン
from core.compat import QWidget, Qt as _Qt, QApplication as _QA
_w = QWidget()
got = []
box = mwd.ask_choice(_w, "T", "I", ["A", "B"], got.append)
check(box.windowModality() == _Qt.NonModal and bool(box.windowFlags() & _Qt.WindowStaysOnTopHint)
      and box.isVisible(), "Manager で聞く窓は非モーダル・常に手前")
box.buttons()[[b.text() for b in box.buttons()].index("A")].click()
_QA.processEvents()
box2 = mwd.ask_choice(_w, "T", "", ["A", "B"], got.append)
box2.close()
_QA.processEvents()
check(got == ["A", "B"], "押したボタンを返す。閉じた時は最後のボタン扱い（%r）" % (got,))

if os.name == "nt":
    d = tmpdir("mfm_readerr_")
    script = os.path.join(d, "run.py")
    io.open(os.path.join(d, "src.txt"), "w", encoding="utf-8").write(og.SRC)
    io.open(script, "w", encoding="utf-8").write(r'''
import io, os, sys, json, contextlib
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds, maya.mel as mel
d, out = sys.argv[1:3]
ns = {}
exec(open(os.path.join(d, "src.txt"), encoding="utf-8").read(), ns)
T = {"head": "H %d", "arnold": "ARNOLD", "why": "W"}
res = {}
asked = []
cmds.confirmDialog = lambda *a, **k: (asked.append(k), "x")[1]
_about = cmds.about
cmds.about = lambda *a, **k: False if k.get("batch") else _about(*a, **k)   # GUI のつもり
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
def unlock(path):
    return eval(open(os.path.join(d, "unlock_" + os.path.basename(path) + ".txt"), encoding="utf-8").read(), {})
# 1) 開けた、と返る（何も聞かない）。詳細はスクリプトエディター。素の保存は止まる
pb = bad("kept.ma"); cmds.file(new=True, force=True)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    res["kept"] = ns["_mfm_open"](pb, T)
res["log"] = buf.getvalue()
res["kept_scene"] = os.path.basename(cmds.file(q=True, sceneName=True) or "")
res["kept_save"] = plain()
res["asked"] = len(asked)
# 2) 解除を送る → 素の保存が通る
pb = bad("yes.ma"); cmds.file(new=True, force=True)
res["yes"] = ns["_mfm_open"](pb, T)
res["yes_unlock"] = unlock(pb)
res["yes_save"] = plain()
# 3) シーンが変わっていたら何もしない
pb = bad("other.ma"); cmds.file(new=True, force=True)
ns["_mfm_open"](pb, T)
cmds.file(new=True, force=True)
res["other"] = unlock(pb)
res["other_scene"] = cmds.file(q=True, sceneName=True) or ""
# 4) 開けないファイル
cmds.file(new=True, force=True)
try:
    ns["_mfm_open"](os.path.join(d, "none.ma").replace("\\", "/"), T); res["missing"] = "no error"
except Exception as e:
    res["missing"] = "raised"
# 5) エラーの無いシーン
cmds.file(new=True, force=True)
res["clean"] = ns["_mfm_open"](p, T)
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
res["asked_total"] = len(asked)
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)
maya.standalone.uninitialize()
''')
    for nm in ("yes.ma", "other.ma"):
        io.open(os.path.join(d, "unlock_" + nm + ".txt"), "w", encoding="utf-8").write(
            og.unlock_code(os.path.join(d, nm).replace("\\", "/")))
    out = os.path.join(d, "out.json")
    r = subprocess.run([sys.executable, script, d, out], capture_output=True, text=True, timeout=300)
    res = json.load(io.open(out, encoding="utf-8")) if os.path.isfile(out) else {}
    if not res:
        print(r.stdout[-1500:], r.stderr[-3000:], flush=True)
    print({k: v for k, v in res.items() if k != "log"}, flush=True)
    NEED = "A file must be given a name before saving"
    kp = str(res.get("kept", ""))
    check(kp.startswith("OpenedWithErrors:") and ":pending:" in kp and kp.endswith("kept.ma")
          and res.get("kept_scene") == "kept.ma" and res.get("asked_total") == 0,
          "読み込み中にエラーが出るシーンでも «開けた» と返し、Maya 側では何も聞かない（%r / 聞いた %r 回）"
          % (kp, res.get("asked_total")))
    lg = res.get("log", "")
    check("ai_override" in lg and "ARNOLD" in lg and "H " in lg,
          "詳細（エラーの行・Arnold の見当）はスクリプトエディターに出る")
    check(NEED in str(res.get("kept_save")), "そのままでは Maya の素の保存が止まる（Maya の動き）")
    check(res.get("yes_unlock") == "MfmUnlocked:ok" and res.get("yes_save") == "OK",
          "解除を送ると素の保存が通る（%r / %r）" % (res.get("yes_unlock"), res.get("yes_save")))
    check(res.get("other") == "MfmUnlocked:other" and res.get("other_scene") == "",
          "シーンが変わっていたら解除しない（%r）" % (res.get("other"),))
    check(res.get("missing") == "raised", "開けないファイルは今までどおり例外")
    check(res.get("clean") is None and res.get("clean_save") == "OK",
          "エラーの無いシーンでは何も返さない")
    check(res.get("reference") == ["", "OK"] and res.get("import") == ["", "OK"],
          "リファレンス・インポートでは起きない（%r / %r）" % (res.get("reference"), res.get("import")))
finish(not fails)
