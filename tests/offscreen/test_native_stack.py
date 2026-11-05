# -*- coding: utf-8 -*-
"""r146（ユーザー報告 2026-11-02「P4V で GetLatest をしていると Manager が
フリーズします」）: 33 秒の停止の間、メインスレッドは Qt の C++ の中にいて
Python 側の記録には app.exec() しか残らなかった。止まった瞬間の C++ 側の
呼び出し履歴を別プロセスに取らせて mfm_freeze.log に残す（core/native_stack）。

確かめること:
  1) 本当に止まっているスレッド（Windows の Sleep の中）の履歴に Sleep が出る
  2) 本番と同じ道（main.py --mfm-native-stack を別プロセスで起動）で
     ファイルに書かれる。相手のプロセスは止まったままにならない（再開される）
  3) Maya の中（sys.executable が maya.exe）では Maya を起動しない
  4) フリーズ見張りに組み込まれている
"""
import io
import os
import subprocess
import sys
import tempfile
import time
from _common import *  # noqa: F401,F403
from _common import finish, tmpdir
from core import native_stack as ns

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 3) Maya の中では Maya を起動しない
_orig_exe = sys.executable
try:
    d = tmpdir()
    sys.executable = os.path.join(d, "maya.exe")
    cmd = ns.helper_command(1, 2, "x.log")
    check(cmd is None, "mayapy が無い Maya では何も起動しない（%r）" % (cmd,))
    open(os.path.join(d, "mayapy.exe"), "w").close()
    cmd = ns.helper_command(1, 2, "x.log")
    check(cmd and os.path.basename(cmd[0]).lower() == "mayapy.exe",
          "Maya の中では mayapy.exe を使う（%r）" % (cmd[:1] if cmd else cmd,))
finally:
    sys.executable = _orig_exe

# 4) 組み込み
src = io.open(os.path.join(ROOT, "ui", "freeze_watchdog.py"), encoding="utf-8").read()
check("capture_async(" in src, "フリーズ見張りが C++ 側の履歴を取らせる")
msrc = io.open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
check("--mfm-native-stack" in msrc and msrc.index("--mfm-native-stack") < msrc.index("def _bootstrap_pyside"),
      "main.py は Qt を用意する前に履歴取りの入口を通す")

if os.name == "nt":
    CHILD = ("import ctypes, sys, threading\n"
             "print(threading.get_native_id(), flush=True)\n"
             "ctypes.windll.kernel32.Sleep(20000)\n")
    child = subprocess.Popen([sys.executable, "-c", CHILD], stdout=subprocess.PIPE, text=True)
    tid = int(child.stdout.readline().strip())
    time.sleep(0.5)
    # 1)
    t = time.perf_counter()
    lines = ns.walk(child.pid, tid)
    dt = time.perf_counter() - t
    text = "\n".join(lines)
    print(text[:1500], flush=True)
    check(any("Sleep" in ln or "DelayExecution" in ln for ln in lines),
          "止まっているスレッドの履歴に Sleep が出る（%.1f 秒）" % dt)
    # 2) 本番と同じ道
    out = os.path.join(tmpdir(), "freeze.log")
    cmd = ns.helper_command(child.pid, tid, out, "test")
    rc = subprocess.run(cmd, timeout=120).returncode
    got = io.open(out, encoding="utf-8").read() if os.path.isfile(out) else ""
    check(rc == 0 and "C++ の呼び出し履歴" in got and "Sleep" in got,
          "別プロセス（main.py --mfm-native-stack）でファイルに書かれる（rc=%s）" % rc)
    check(child.poll() is None, "調べた相手は動き続けている（止めたままにしない）")
    child.kill()
else:
    print("SKIP 実際の履歴取り（Windows のみ）", flush=True)

finish(not fails)
