# -*- coding: utf-8 -*-
"""r153（2026-11-21）: 配布版（PyInstaller の 1 ファイル exe）から起動した Maya で
Arnold（mtoa.mll）が読み込めなかった件（WinError 448「信頼されていないマウントポイント」）。

失敗した Maya で取った診断（島添さん 2026-11-21）で、Manager を通さず起動した Maya と
違ったのは次の 3 つだけ:
  * PATH の上のほうに Manager の展開先（%TEMP%\\_MEIxxxx\\PySide6・%TEMP%\\_MEIxxxx）
  * _PYI_* の環境変数
  * 作業フォルダが Manager のインストール先
（«DLL を探す場所»＝SetDllDirectoryW は引き継がれていなかった）

確かめること:
  1) PATH から Manager の展開先（sys._MEIPASS とその下・_MEIxxxx）を外す。他の項目と順番はそのまま
  2) _PYI_* / _MEIPASS* を渡さない。親（Manager）の環境変数は変えない
  3) 作業フォルダは Maya 本体のフォルダ
  4) Blender の起動も同じ
"""
import os
import sys
from pathlib import Path
from unittest import mock
from _common import *  # noqa: F401,F403
from _common import finish, tmpdir

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


from core import maya_version as mv
from core import blender_version as bv

root = Path(tmpdir("mfm_frozen_env"))
MEI = str(root / "Temp" / "_MEI0000b9482")
OTHER_MEI = str(root / "Temp" / "_MEI0000aaaa1")          # 別の起動の展開先が残っていても外す
keep1 = str(root / "Maya2027" / "bin")
keep2 = str(root / "tools" / "bin")
keep3 = str(root / "Users" / "MEI_note")                   # «_MEI» で始まらないものは残す
PATHV = os.pathsep.join([keep1, os.path.join(MEI, "PySide6"), MEI, keep2, OTHER_MEI, keep3])
bindir = root / "Maya2027" / "bin"
bindir.mkdir(parents=True, exist_ok=True)
exe = bindir / ("maya.exe" if os.name == "nt" else "maya")
exe.write_text("")
exe.chmod(0o755)


class Inst:
    version = "2027"
    path = root / "Maya2027"
    executable = exe
    is_available = True


seen = {}


def fake_popen(cmd, **kw):
    seen["kw"] = kw
    return mock.MagicMock(pid=4242)


envx = {"PATH": PATHV, "KEEP": "1", "_PYI_APPLICATION_HOME_DIR": MEI, "_PYI_ARCHIVE_FILE": "x.exe",
        "_PYI_PARENT_PROCESS_LEVEL": "1", "PYTHONHOME": "x"}

with mock.patch.dict(os.environ, envx, clear=True), mock.patch.object(sys, "frozen", True, create=True), \
        mock.patch.object(sys, "_MEIPASS", MEI, create=True), mock.patch.object(mv, "_drive_type", lambda d: 0), \
        mock.patch.object(mv.os.path, "realpath", lambda p: p), \
        mock.patch.object(mv.subprocess, "Popen", fake_popen):
    mv.launch_maya(Inst(), command_port=20263)
    after_env = dict(os.environ)
env = seen["kw"].get("env") or {}
got = (env.get("PATH") or "").split(os.pathsep)
check(got == [keep1, keep2, keep3], "PATH から Manager の展開先だけを外す・順番はそのまま（%r）" % (got,))
check(not [k for k in env if k.startswith(("_PYI_", "_MEIPASS"))],
      "exe が自分用に足した環境変数を渡さない（%r）" % (sorted(env),))
check(env.get("KEEP") == "1" and "PYTHONHOME" not in env, "他の環境変数はそのまま・衝突するものは外す")
check(after_env == envx, "親（Manager）の環境変数は変えない")
check(os.path.normcase(seen["kw"].get("cwd", "")) == os.path.normcase(str(bindir)),
      "作業フォルダは Maya 本体のフォルダ（%r）" % (seen["kw"].get("cwd"),))

# 開発版（frozen でない・_MEIPASS 無し）でも _MEIxxxx の項目は外れ、他は触らない
seen.clear()
with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([keep1, keep2])}, clear=True), \
        mock.patch.object(mv, "_drive_type", lambda d: 0), mock.patch.object(mv.os.path, "realpath", lambda p: p), \
        mock.patch.object(mv.subprocess, "Popen", fake_popen):
    mv.launch_maya(Inst(), command_port=20263)
check((seen["kw"]["env"].get("PATH") or "").split(os.pathsep) == [keep1, keep2], "開発版の PATH はそのまま")

# Blender
bexe = root / "blender" / ("blender.exe" if os.name == "nt" else "blender")
bexe.parent.mkdir(parents=True, exist_ok=True)
bexe.write_text("")
bexe.chmod(0o755)
seen.clear()
with mock.patch.dict(os.environ, envx, clear=True), mock.patch.object(sys, "frozen", True, create=True), \
        mock.patch.object(sys, "_MEIPASS", MEI, create=True), \
        mock.patch.object(bv.subprocess, "Popen", fake_popen):
    bv.launch_blender(bv.BlenderInstallation("4.2", bexe), command_port=20271)
benv = seen["kw"].get("env") or {}
check((benv.get("PATH") or "").split(os.pathsep) == [keep1, keep2, keep3],
      "Blender も PATH から Manager の展開先を外す（%r）" % ((benv.get("PATH") or "").split(os.pathsep),))
check(not [k for k in benv if k.startswith(("_PYI_", "_MEIPASS"))], "Blender にも exe 用の環境変数を渡さない")

finish(not fails)
