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

# r154（実機 2026-11-24「Failed to remove temporary directory: …\\_MEIxxxx」）:
# 手で作った環境変数ではなく、**配布版の起動処理（build_tools/runtime_hook.py）が
# 実際に足すもの** で確かめる。以前のテストは PATH と _PYI_* だけを想定していて、
# 起動処理が足す QT_PLUGIN_PATH（展開先の PySide6\\plugins）を見落としていた。
import json, subprocess
os.makedirs(os.path.join(MEI, "PySide6", "plugins"), exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
probe = (
    "import os, sys, json\n"
    "sys._MEIPASS = %r\n"
    "os.environ.pop('QT_PLUGIN_PATH', None)\n"   # mayapy は自分で設定するので、素の起動に揃える
    "before = dict(os.environ)\n"
    "src = open(%r, encoding='utf-8').read()\n"
    "exec(compile(src, 'runtime_hook', 'exec'), {'__file__': %r, '__name__': '__main__'})\n"
    "print(json.dumps({k: v for k, v in os.environ.items() if before.get(k) != v}))\n"
    % (MEI, os.path.join(ROOT, "build_tools", "runtime_hook.py"),
       os.path.join(ROOT, "build_tools", "runtime_hook.py")))
env0 = {k: v for k, v in os.environ.items() if k not in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH")}
r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env0, timeout=60)
added = {}
try:
    added = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    print(r.stdout[-800:], r.stderr[-800:], flush=True)
check(any(MEI in v for v in added.values()),
      "配布版の起動処理が展開先を指す環境変数を足すこと自体を確かめる（%r）" % (sorted(added),))
user_qt = str(root / "my_qt_plugins")
envr = dict(envx)
envr.update(added)
envr["MY_TOOL_PATH"] = os.pathsep.join([user_qt, os.path.join(MEI, "x")])
seen.clear()
with mock.patch.dict(os.environ, envr, clear=True), mock.patch.object(sys, "frozen", True, create=True), \
        mock.patch.object(sys, "_MEIPASS", MEI, create=True), mock.patch.object(mv, "_drive_type", lambda d: 0), \
        mock.patch.object(mv.os.path, "realpath", lambda p: p), \
        mock.patch.object(mv.subprocess, "Popen", fake_popen):
    mv.launch_maya(Inst(), command_port=20263)
env2 = seen["kw"].get("env") or {}
leak = sorted(k for k, v in env2.items() if MEI.lower() in (v or "").lower())
check(not leak, "どの環境変数にも Manager の展開先を残さない（QT_PLUGIN_PATH 等）（%r）" % (leak,))
check(env2.get("MY_TOOL_PATH") == user_qt, "利用者の値は残し、展開先の項目だけ外す（%r）" % (env2.get("MY_TOOL_PATH"),))
# （Maya へは QT_PLUGIN_PATH 自体を渡さない決まり（_CONFLICTING_ENV_KEYS）なので、ここでは確かめない）
seen.clear()
with mock.patch.dict(os.environ, envr, clear=True), mock.patch.object(sys, "frozen", True, create=True), \
        mock.patch.object(sys, "_MEIPASS", MEI, create=True), \
        mock.patch.object(bv.subprocess, "Popen", fake_popen):
    bexe0 = root / "blender0" / ("blender.exe" if os.name == "nt" else "blender")
    bexe0.parent.mkdir(parents=True, exist_ok=True)
    bexe0.write_text("")
    bexe0.chmod(0o755)
    bv.launch_blender(bv.BlenderInstallation("4.2", bexe0), command_port=20271)
bleak = sorted(k for k, v in (seen["kw"].get("env") or {}).items() if MEI.lower() in (v or "").lower())
check(not bleak, "Blender にも展開先を指す環境変数を渡さない（%r）" % (bleak,))

# 既定のアプリで開く・エクスプローラーで表示する時も、開いたアプリに展開先を渡さない
from core import file_operations as fo
cap = []


def _cap_env(*a, **k):
    cap.append(dict(os.environ))
    return mock.MagicMock()


target = root / "open_me.txt"
target.write_text("x")
with mock.patch.dict(os.environ, envr, clear=True), mock.patch.object(sys, "frozen", True, create=True), \
        mock.patch.object(sys, "_MEIPASS", MEI, create=True), \
        mock.patch.object(fo, "_windows_shell_open", lambda p: (cap.append(dict(os.environ)), True)[1]), \
        mock.patch.object(fo.subprocess, "Popen", _cap_env):
    fo.open_with_default_app(str(target))
    fo.reveal_in_explorer(str(target))
    restored = dict(os.environ) == envr
oleak = sorted({k for e in cap for k, v in e.items() if MEI.lower() in (v or "").lower()})
check(len(cap) >= 1 and not oleak,
      "既定のアプリで開く・エクスプローラーで表示: 展開先を指す環境変数を渡さない（%d 回 / %r）" % (len(cap), oleak))
check(restored, "開いた後、Manager 自身の環境変数は元に戻す")

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
