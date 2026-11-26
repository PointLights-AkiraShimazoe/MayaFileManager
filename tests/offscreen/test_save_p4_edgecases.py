# -*- coding: utf-8 -*-
"""r155（H-20 の詰め、島添さん指示 2026-11-26「疑える内容があれば、完全に潰しきってください」）:
実機の Perforce（P4CHARSET=utf8・ジャンクションのワークスペース）で確かめた、
«管理下のファイルなのに、ただの読み取り専用に見えてチェックアウトが出ない» 穴を塞いだことを確かめる。

偽の p4 は実機で見た振る舞いをそのまま真似る:
  * 日本語を含むパスをコマンド行で渡すと «No Translation for parameter ...» で失敗する
    （実機: P4CHARSET=utf8 だとコマンド行の引数が CP932 で渡り弾かれる）。
    -C utf8 -x <UTF-8 のファイル> で渡せば通る
  * クライアントルートの外（ジャンクションの実体側のパス）だと
    «Path '...' is not under client's root '...'.»
  * つながらない時は «Connect to server failed»

確かめること:
  1) 日本語の名前: 状態が読める（管理下・他のユーザー）／チェックアウトできる
  2) ジャンクションの実体側のパスで開いたファイル: ルート側のパスに直して状態が読める／チェックアウトできる
  3) Perforce に聞けない時: «管理外» と言わず «確認できませんでした» と言い、チェックアウトの選択肢を消さない
  4) 本当に管理外（ルートの外・未登録）は今までどおり «読み取り専用を解除して保存／保存しない»
  5) Maya 側の見張りも同じ処理を使っている
"""
import io
import os
import stat
import subprocess
import sys
from _common import *  # noqa: F401,F403
from _common import finish, tmpdir

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


from core import save_target_guard as sg

d = tmpdir("mfm_p4edge_")
real = os.path.join(d, "real")            # ジャンクションの実体
os.makedirs(os.path.join(real, "sub"))
ws = os.path.join(d, "ws")                # クライアントルート（実体へのジャンクション）
if os.name == "nt":
    subprocess.run(["cmd", "/c", "mklink", "/J", ws, real], capture_output=True)
else:
    os.symlink(real, ws)
check(os.path.isdir(ws) and os.path.realpath(ws) != os.path.normpath(ws), "ワークスペースのジャンクションを作れた")

io.open(os.path.join(d, "fakep4.py"), "w", encoding="utf-8").write(r'''
import io, os, stat, sys
here = os.path.dirname(os.path.abspath(__file__))
a = sys.argv[1:]
client = a[a.index("-c") + 1] if "-c" in a else "other"
ROOT = os.path.join(here, "ws" if client == "ws_me" else "otherroot")   # 既定のワークスペースは別
# Windows の .bat 経由だと日本語の引数は化けて（サロゲートで）届く。本物の p4 と同じく «日本語＝弾く» として扱う
io.open(os.path.join(here, "calls.log"), "a", encoding="utf-8", errors="replace").write(" | ".join(a) + "\n")
if os.path.exists(os.path.join(here, "down")):
    print("Perforce client error:\n\tConnect to server failed; check $P4PORT.")
    sys.exit(1)
xfile, cs, i = None, None, 0
while i < len(a) and a[i].startswith("-") and a[i] not in ("-ztag",):
    if a[i] == "-x":
        xfile = a[i + 1]; i += 2
    elif a[i] == "-C":
        cs = a[i + 1]; i += 2
    elif a[i] in ("-c", "-p", "-u"):
        i += 2
    else:
        i += 1
rest = a[i:]
if rest and rest[0] == "-ztag":
    rest = rest[1:]
cmd, args = rest[0], rest[1:]
if xfile:
    raw = open(xfile, "rb").read()
    try:
        args = args + [raw.decode("utf-8" if cs == "utf8" else "ascii").strip()]
    except UnicodeDecodeError:
        print("Perforce client warning:\n\tNo Translation for parameter '' value 'xx'!"); sys.exit(1)
path = args[-1]
if xfile is None and any(ord(c) > 127 for c in path):
    print("Perforce client warning:\n\tNo Translation for parameter '' value '%s'!" % path.encode("utf-8", "replace").hex().upper())
    sys.exit(1)
rk = os.path.normcase(ROOT)
if not (os.path.normcase(path) == rk or os.path.normcase(path).startswith(rk + os.sep)):
    sys.stderr.write("Path '%s' is not under client's root '%s'.\n" % (path, ROOT)); sys.exit(1)
if os.path.basename(path).startswith("new_"):
    print(path + " - no such file(s)."); sys.exit(0)
if cmd == "fstat":
    print("... depotFile //depot/" + os.path.basename(path) + "\n... haveRev 2\n... headRev 2\n... otherOpen0 bob@ws_bob\n... otherOpen 1")
    sys.exit(0)
if cmd == "edit":
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    print("//depot/" + os.path.basename(path) + "#2 - opened for edit"); sys.exit(0)
print("unknown"); sys.exit(1)
''')
if os.name == "nt":
    exe = os.path.join(d, "fakep4.bat")
    io.open(exe, "w").write('@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, os.path.join(d, "fakep4.py")))
else:
    exe = os.path.join(d, "fakep4.sh")
    io.open(exe, "w").write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, os.path.join(d, "fakep4.py")))
    os.chmod(exe, 0o755)


def mkro(p):
    io.open(p, "w").write("//Maya ASCII\n")
    os.chmod(p, stat.S_IREAD)
    return p


def calls():
    p = os.path.join(d, "calls.log")
    return io.open(p, encoding="utf-8").read().splitlines() if os.path.exists(p) else []


os.makedirs(os.path.join(d, "otherroot"))


class Prov:
    """ワークスペースが 2 つ（ws_me: ws、other: otherroot、既定は other）。
    フォルダの表記（文字列）でワークスペースを決める本物の provider と同じ振る舞い。"""
    p4 = exe
    available = True

    def _all_clients(self):
        return [(os.path.normcase(ws), "ws_me", ws),
                (os.path.normcase(os.path.join(d, "otherroot")), "other", os.path.join(d, "otherroot"))]

    def client_for(self, directory):
        k = os.path.normcase(os.path.normpath(directory))
        for rk, name, _r in self._all_clients():
            if k == rk or k.startswith(rk + os.sep):
                return name
        return ""

    def conn_args_for(self, directory):
        return ["-c", self.client_for(directory) or "other"]


T = sg.status_texts(True)
prov = Prov()

# 1) 日本語の名前
jp = mkro(os.path.join(ws, "sub", u"ギー_レイアウト.ma"))
st = sg.run_fstat(sg.p4_fstat_cmd(prov, jp), jp)
check(st is not None and st.get("tracked") and st.get("others") == ["bob@ws_bob"],
      "日本語の名前: 管理下・他のユーザー（bob）と読める（%r）" % (st,))
check(any("-x" in c and "utf8" in c for c in calls()), "日本語の名前: UTF-8 の引数ファイル（-C utf8 -x）で聞き直している")
check(sg.status_lines(st, True, T)[0] == T["others"] % "bob" and sg.disabled_keys(st) == ["co"],
      "日本語の名前: «他のユーザー（bob）にチェックアウトされています»・チェックアウトは押せない")
jp2 = mkro(os.path.join(ws, "sub", u"背景_A.ma"))
ok, out = sg.run_p4_edit(sg.p4_edit_cmd(prov, jp2), jp2)
check(ok and os.access(jp2, os.W_OK), "日本語の名前: チェックアウトできる（%r）" % (out[:80],))

# 2) ジャンクションの実体側のパス
open(os.path.join(d, "calls.log"), "w").close()
rp = mkro(os.path.join(real, "sub", "shot_A.ma"))
st = sg.run_fstat(sg.p4_fstat_cmd(prov, rp), rp)
check(st is not None and st.get("tracked"), "実体側のパス: ルート側に直して管理下と読める（%r / %r）" % (st, calls()))
check(calls() and "ws_me" in calls()[0], "実体側のパス: 既定ではなく、そのファイルのワークスペース（-c ws_me）で聞く（%r）" % (calls()[:1],))
rp2 = mkro(os.path.join(real, "sub", u"実体_B.ma"))      # 実体側＋日本語
ok, out = sg.run_p4_edit(sg.p4_edit_cmd(prov, rp2), rp2)
check(ok and os.access(rp2, os.W_OK), "実体側のパス＋日本語: チェックアウトできる（%r）" % (out[:120],))

# 3) Perforce に聞けない
io.open(os.path.join(d, "down"), "w").write("1")
dn = mkro(os.path.join(ws, "sub", "down.ma"))
st = sg.run_fstat(sg.p4_fstat_cmd(prov, dn), dn)
check(st is None, "聞けない時は «分からない»（None）で、管理外とは扱わない（%r）" % (st,))
check(sg.choice_keys(st, True) == ["co", "rw", "no"] and sg.disabled_keys(st) == []
      and sg.status_lines(st, True, T)[0] == T["unknown"],
      "聞けない時: «確認できませんでした» と言い、チェックアウトの選択肢を残す")
os.remove(os.path.join(d, "down"))

# 4) 本当に管理外
out_dir = tmpdir("mfm_p4out_")
ou = mkro(os.path.join(out_dir, "plain.ma"))
st = sg.run_fstat(sg.p4_fstat_cmd(prov, ou), ou)
check(st is not None and not st.get("tracked") and sg.choice_keys(st, True) == ["unro", "no"]
      and sg.status_lines(st, True, T)[0] == T["ro"], "ルートの外: 今までどおり «読み取り専用です»（解除して保存／保存しない）")
nw = mkro(os.path.join(ws, "sub", "new_shot.ma"))
st = sg.run_fstat(sg.p4_fstat_cmd(prov, nw), nw)
check(st is not None and not st.get("tracked") and sg.choice_keys(st, True) == ["unro", "no"],
      "ルートの中だが未登録: «読み取り専用です»")
check(sg.fstat_state("") is None and sg.fstat_state("p4 failed: x") is None, "出力が無い・失敗は «分からない»")

if os.name == "nt":
    check(sg.map_to_root("S:\\a\\b.ma", "S:\\") == "S:\\a\\b.ma" and sg.map_to_root("S:\\a\\b.ma", "S:") == "S:\\a\\b.ma",
          "ドライブ直下のルート（S:\\）でも直せる（%r）" % (sg.map_to_root("S:\\a\\b.ma", "S:\\"),))

# 5) Maya 側の見張り
from core import maya_save_watch as mw
src = mw._maya_source("x.log", "ja")
compile(src, "watch", "exec")
check('fstat_state(p4_run(p4, ["-ztag", "fstat"], path, 8))' in src and 'p4_run(p4, ["edit"], path, 30)' in src
      and "def p4_run(" in src and "No Translation" in src and "map_to_root(path, root)" in src,
      "Maya 側の見張りも同じ処理（日本語・実体側・聞けない時）を使う")
ns = {}
exec(compile(sg.HELPERS_SRC, "helpers", "exec"), ns)
st = ns["fstat_state"](ns["p4_run"]([exe, "-c", "ws_me"], ["-ztag", "fstat"], jp, 8))
check(st is not None and st.get("tracked"), "Maya 側に入る関数だけで日本語の名前の状態が読める")

if os.name == "nt":
    subprocess.run(["cmd", "/c", "rmdir", ws], capture_output=True)
finish(not fails)
if fails:
    print("FAILED:", fails)
