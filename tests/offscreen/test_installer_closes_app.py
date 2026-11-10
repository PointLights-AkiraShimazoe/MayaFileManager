# -*- coding: utf-8 -*-
"""v0.9.19: インストーラは起動中の Manager を «自分で» 閉じてから入れる。

ユーザー報告 2026-11-10:
  「インストールしようとしたらフリーズしました。アプリケーションを自動で落とすとして
    進めるとフリーズしました。落としたら大丈夫でした。」
  「自動で落とすを改修するか、難しいのであれば落としてからでないと先に進めない
    としてください。」

Inno Setup の既定（Restart Manager に閉じさせる）をやめ、PrepareToInstall で
«普通に閉じる → 待つ → 強制終了 → それでも残れば先へ進ませない» を行う。

確かめること（原文の検査。インストーラは CI と Windows で作る）:
  * Restart Manager を使わない（CloseApplications=no / RestartApplications=no）
  * PrepareToInstall がある
  * 先に «普通に閉じる»（/F なしの taskkill）、待ってから強制終了（/F /T）の順
  * 対象は AppExeName（MayaFileManager.exe）。待ち時間に上限がある
  * 閉じられなければ先へ進ませない（PrepareToInstall が文言を返す）
  * Windows で Inno Setup があれば実際にコンパイルが通る（出力はしない）
"""
import io
import os
import re
import subprocess
from _common import *  # noqa: F401,F403
from _common import app, finish

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
ISS = os.path.join(ROOT, "build_tools", "installer.iss")
s = io.open(ISS, encoding="utf-8").read()
setup = s.split("[Setup]", 1)[1].split("\n[", 1)[0]
code = s.split("[Code]", 1)[1] if "[Code]" in s else ""

check(re.search(r"^\s*CloseApplications\s*=\s*no\s*$", setup, re.M | re.I) is not None,
      "Restart Manager に閉じさせない（CloseApplications=no）")
check(re.search(r"^\s*RestartApplications\s*=\s*no\s*$", setup, re.M | re.I) is not None,
      "インストール後に勝手に起動し直さない（RestartApplications=no）")
check("function PrepareToInstall(" in code, "PrepareToInstall で閉じる")
m = re.search(r"#define\s+AppExeName\s+\"([^\"]+)\"", s)
check(m is not None and m.group(1) == "MayaFileManager.exe", "対象の EXE 名")
gentle = code.find("'/IM {#AppExeName}'")
force = code.find("'/F /T /IM {#AppExeName}'")
check(gentle > 0, "まず普通に閉じる（/F なしの taskkill）")
check(force > gentle > 0, "応答が無ければその後で強制終了（/F /T）")
check(re.search(r"MfmWaitGone\(\s*\d+\s*\)", code) is not None and "Sleep(" in code,
      "待ち時間に上限がある（無限に待たない）")
body = code.split("function PrepareToInstall(", 1)[1] if "function PrepareToInstall(" in code else ""
check("Result := 'Maya File Manager を終了できませんでした" in body,
      "閉じられなければ先へ進ませない（文言を返す）")
check("IMAGENAME eq {#AppExeName}" in code, "起動中かどうかは tasklist で EXE 名を見る")

# Windows で Inno Setup があれば、実際にコンパイルが通るか（出力しない /O-）
iscc = r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if os.name == "nt" and os.path.exists(iscc):
    p = subprocess.run([iscc, "/O-", "/Q", "/DAppVersion=0.0.0", ISS],
                       capture_output=True, cwd=ROOT)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    check(p.returncode == 0, "Inno Setup でコンパイルが通る（rc=%s %s）" % (p.returncode, out[-400:]))
else:
    print("SKIP Inno Setup が無いのでコンパイルは確かめない（CI のビルドで確かめる）")

finish(not fails)
