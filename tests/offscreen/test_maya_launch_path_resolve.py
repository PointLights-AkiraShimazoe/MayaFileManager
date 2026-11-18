# -*- coding: utf-8 -*-
"""r150（2026-11-18）: Maya 起動時の PATH のジャンクション解決（Codex の修正を取り込み）。

原因: PATH にジャンクション（ディレクトリのリンク）があると、Maya 2027 は
      «未信頼のマウントポイント»（WinError 448）として DLL 探索を中断する。
      mtoa の依存 DLL が読めず、Arnold が載らなかった。
修正: 起動する Maya に渡す PATH の写しだけ、項目を実パスへ解決する。

確かめること:
  1) ローカルの固定ディスク上の絶対パスだけを解決する
  2) ネットワークドライブ・UNC・リムーバブル・相対パス・空の項目には触らない
     （realpath はそのドライブへ問い合わせる。切れたネットワークドライブで
       UI が止まらないように。起動は UI スレッドから呼ばれる）
  3) 順番を保つ。解決できない項目はそのまま。親の環境変数は変えない。
     衝突する環境変数（PYTHONHOME 等）は今までどおり外す
  4) Windows: 本物のジャンクションが実フォルダへ解決される
"""
import os
import subprocess
import sys
from unittest import mock
from _common import *  # noqa: F401,F403
from _common import finish, tmpdir

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


from core import maya_version as mv

SEP = os.pathsep
called = []
items = ["D:\\tools\\link", "Z:\\net\\bin", "\\\\nas\\share\\bin", "", "relative\\bin",
         "E:\\usb\\bin", "C:\\Windows\\System32"]
types = {"C:": 3, "D:": 3, "E:": 2, "Z:": 4}
asked = []


def _fake_real(p):
    called.append(p)
    return p.replace("link", "real")


with mock.patch.object(mv, "_IS_WIN", True), \
        mock.patch.object(mv, "_drive_type", lambda d: (asked.append(d), types.get(d, 0))[1]), \
        mock.patch.object(mv.os.path, "realpath", _fake_real):
    out = mv._resolve_local_path_entries(";".join(items), ";").split(";")
check(called == ["D:\\tools\\link", "C:\\Windows\\System32"],
      "ローカルの固定ディスク上の絶対パスだけを解決する（%r）" % (called,))
check(out == ["D:\\tools\\real"] + items[1:],
      "ネットワーク・UNC・リムーバブル・相対・空の項目はそのまま、順番も保つ（%r）" % (out,))
check(sorted(asked) == ["C:", "D:", "E:", "Z:"], "ドライブの種類は 1 回ずつだけ調べる（%r）" % (asked,))

with mock.patch.object(mv, "_IS_WIN", True), mock.patch.object(mv, "_drive_type", lambda d: 3), \
        mock.patch.object(mv.os.path, "realpath", side_effect=OSError("x")):
    check(mv._resolve_local_path_entries("D:\\a;D:\\b", ";") == "D:\\a;D:\\b",
          "解決できない項目はそのまま")

env0 = {"PATH": SEP.join([os.path.abspath("alias"), "", "relative"]), "KEEP": "yes",
        "PYTHONHOME": "old"}
with mock.patch.dict(os.environ, env0, clear=True):
    with mock.patch.object(mv.os.path, "realpath", lambda p: p + "_real"), \
            mock.patch.object(mv, "_drive_type", lambda d: 3):
        r = mv._clean_launch_env()
    check(dict(os.environ) == env0, "親の環境変数は変えない")
check(r.get("KEEP") == "yes" and "PYTHONHOME" not in r
      and r["PATH"].split(SEP)[0].endswith("_real") and r["PATH"].split(SEP)[1:] == ["", "relative"],
      "起動用の写しだけ解決し、衝突する環境変数は今までどおり外す（%r）" % (r.get("PATH"),))

if os.name == "nt":
    d = tmpdir("mfm_junc_")
    real = os.path.join(d, "real_dir")
    link = os.path.join(d, "link_dir")
    os.makedirs(real)
    open(os.path.join(real, "x.txt"), "w").close()
    subprocess.run(["cmd", "/c", "mklink", "/J", link, real], capture_output=True)
    if not os.path.isdir(link):
        print("SKIP ジャンクションを作れない環境", flush=True)
    else:
        res = mv._resolve_local_path_entries(link + SEP + "relative")
        first = res.split(SEP)[0]
        check(os.path.normcase(first) != os.path.normcase(link)
              and os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(real))
              and os.path.isfile(os.path.join(first, "x.txt")),
              "本物のジャンクションは実フォルダへ解決される（%r）" % (first,))
        try:
            os.rmdir(link)            # ジャンクションだけ外す（中身は消さない）
        except OSError:
            pass
else:
    print("SKIP 本物のジャンクション（Windows のみ）", flush=True)
finish(not fails)
