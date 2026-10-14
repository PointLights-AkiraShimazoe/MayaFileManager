# -*- coding: utf-8 -*-
"""r146: Maya 起動プロファイルの «環境変数» と «ラッパー»。

ユーザーからの例（2026-10-13）— bat で組んでいた起動手順::

    SET MAYA_EXE="C:\\Program Files\\Autodesk\\Maya2025\\bin\\maya.exe"
    SET PATH_MAYA_BAT=%~DP0run.bat
    SET OPEN_FILE="%1"
    if %OPEN_FILE% == "" ( START %PATH_MAYA_BAT% %MAYA_EXE% en_EN )
    else ( START %PATH_MAYA_BAT% %MAYA_EXE% en_EN %OPEN_FILE% )

«追加引数» は maya.exe の引数しか渡せないので、これは再現できなかった。
en_EN（＝ UI 言語）は Maya が **起動時に環境変数として** 読むもので、
引数でも -command でも間に合わない。そこで 2 つ足した。

  環境変数 … KEY=VALUE を ; か改行で。bat の «SET KEY=VALUE» も通る。
  ラッパー … maya.exe の代わりに走らせる .bat / .exe。
             {exe} {file} {port} を書いた位置へ差し込む。
             書かなければ «ラッパー maya.exe» の順で繋ぐ（上の bat と同じ形）。

ここで固定すること:
  1) 環境変数の書式を取りこぼさない（引用符・空白・SET・注記・; 区切り）
  2) ラッパーの組み立てが bat と同じ並びになる
  3) 開くファイルが «二重に» 付かない（{file} を書いた時）
  4) .bat は cmd 経由で呼ぶ
  5) プロファイルに env / wrapper が保存される
"""
import os

from _common import *  # noqa: F401,F403
from _common import finish, sm

from core.maya_version import parse_env_text, _expand_wrapper

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


# ── 1) 環境変数の書式 ─────────────────────────────────────────────
got = parse_env_text('SET MAYA_UI_LANGUAGE=en_US\n'
                     'MAYA_MODULE_PATH = "D:/mods" ; FOO=bar  # memo\n'
                     '\n'
                     'BAD_LINE_WITHOUT_EQUALS')
check(got.get("MAYA_UI_LANGUAGE") == "en_US", "bat の SET 行をそのまま読める")
check(got.get("MAYA_MODULE_PATH") == "D:/mods", "引用符と前後の空白を落とす")
check(got.get("FOO") == "bar", "; 区切りと # 注記を扱える")
check(len(got) == 3, "= の無い行は捨てる（%r）" % (got,))
check(parse_env_text("") == {}, "空なら空")

# ── 2)(3)(4) ラッパーの組み立て ───────────────────────────────────
EXE = r"C:\Program Files\Autodesk\Maya2025\bin\maya.exe"
FILE = r"D:\work\scene.ma"

a = _expand_wrapper(r"D:\tools\run.bat", EXE, FILE, 20251)
check(a[:2] == [os.environ.get("COMSPEC") or "cmd.exe", "/c"],
      ".bat は cmd 経由で呼ぶ（%r）" % (a[:2],))
check(a[2:] == [r"D:\tools\run.bat", EXE],
      "プレースホルダ無しは «ラッパー maya.exe» の順（%r）" % (a[2:],))

b = _expand_wrapper(r"D:\tools\run.bat {exe} en_EN {file}", EXE, FILE, None)
check(b[2:] == [r"D:\tools\run.bat", EXE, "en_EN", FILE],
      "書いた位置へ差し込む（%r）" % (b[2:],))

c = _expand_wrapper(r"D:\tools\run.bat {exe} en_EN {file}", EXE, "", None)
check(c[2:] == [r"D:\tools\run.bat", EXE, "en_EN"],
      "開くファイルが無い時は {file} の分が消える（%r）" % (c[2:],))

d = _expand_wrapper(r"D:\tools\run.exe {exe} --port {port}", EXE, "", 20251)
check(d == [r"D:\tools\run.exe", EXE, "--port", "20251"],
      ".exe はそのまま／{port} も差し替わる（%r）" % (d,))

check(_expand_wrapper("", EXE, FILE, None) == [], "空なら何もしない")

e = _expand_wrapper(r'"D:\my tools\run.bat" {exe} en_EN {file}', EXE, FILE, None)
check(e[2] == r"D:\my tools\run.bat",
      "空白入りのパスを引用符で書ける（引用符は外れる）（%r）" % (e[2],))

# ── 5) プロファイルの保存 ────────────────────────────────────────
sm.save_maya_launch_profiles([{
    "id": "t1", "version": "2025", "label": "Maya 2025 (en)",
    "args": "-hideConsole", "env": "MAYA_UI_LANGUAGE=en_US",
    "wrapper": r"D:\tools\run.bat {exe} en_EN {file}",
}])
back = sm.get_maya_launch_profiles()
check(len(back) == 1, "1 行保存された")
check(back[0].get("env") == "MAYA_UI_LANGUAGE=en_US", "環境変数が残る")
check(back[0].get("wrapper") == r"D:\tools\run.bat {exe} en_EN {file}",
      "ラッパーが残る")
check(back[0].get("args") == "-hideConsole", "従来の追加引数も残る")

# 古い設定（env / wrapper の無い行）を読んでも壊れない
sm._global_state["maya_launch_profiles"] = [
    {"id": "old", "version": "2024", "label": "", "args": ""}]
old = sm.get_maya_launch_profiles()
check(old and old[0].get("env", "") == "",
      "古い設定を読んでも落ちない（env は空扱い）")

# ── ダイアログに欄が出ているか ───────────────────────────────────
from ui.maya_launch_dialog import MayaLaunchDialog
dlg = MayaLaunchDialog(sm, ["2027", "2026", "2025"])
row = dlg.add_row({"version": "2025", "env": "A=1", "wrapper": "w.bat"})
data = row.get_data()
check(data.get("env") == "A=1" and data.get("wrapper") == "w.bat",
      "行が env / wrapper を往復できる（%r）" % (data,))
check(hasattr(dlg, "_common_env"), "共通の環境変数の欄がある")
dlg.deleteLater()

finish(not fails)
