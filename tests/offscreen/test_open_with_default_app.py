# -*- coding: utf-8 -*-
"""r143: 既定アプリで開く時、フォルダの文脈ごと渡す。

ユーザー報告 2026-10-08:
  「manager から画像をフォトで開くと、左右のカーソルで他のファイルに移動が
    できないです。エクスプローラーからだと出来るので、原因を調査して改修して
    ください。」

原因は 2 つ:
  1) ブラウザ内部のパスは _safe_file_path が «/ 区切り» で組み立てている
     （I/O を避けるための自前連結）。そのままシェルへ渡していた。
  2) os.startfile() は ShellExecuteW に «文字列のパス» を渡すだけなので、
     受け取るアプリには «この 1 ファイル» しか伝わらない。エクスプローラーは
     アイテム ID リスト（PIDL）付きで起動するため、アプリ側が親フォルダを
     たどれる。フォトの左右送りはここに乗っている。

直し方: パスを正規化してから、ShellExecuteEx に SEE_MASK_INVOKEIDLIST を
付けて «アイテムに対して» 既定動作を呼ぶ。親フォルダも lpDirectory で渡す。
"""
import os
from _common import *  # noqa: F401,F403
from _common import tmpdir, finish, run

from core import file_operations as fo

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def s1():
    d = tmpdir()
    img = os.path.join(d, "shot.png")
    open(img, "w").close()

    # ── 1) パスの正規化 ─────────────────────────────────────────────
    slashed = img.replace("\\", "/")
    got = fo.normalize_for_shell(slashed)
    check(got == os.path.normpath(os.path.abspath(img)),
          "/ 区切りを OS のパスへ正す（%r → %r）" % (slashed, got))
    if os.sep == "\\":
        check("/" not in got.replace("//", ""),
              "Windows では区切りが \\ になる（%r）" % got)
    check(fo.normalize_for_shell("") == "", "空文字は素通し")

    # ── 2) 既定アプリで開く時に «正規化済み» が渡ること ───────────────
    seen = []
    orig_win = fo._windows_shell_open
    orig_start = os.startfile if hasattr(os, "startfile") else None
    orig_popen = fo.subprocess.Popen

    fo._windows_shell_open = lambda p: (seen.append(("shell", p)), True)[1]
    if orig_start is not None:
        os.startfile = lambda p: seen.append(("startfile", p))
    fo.subprocess.Popen = lambda a, *x, **k: seen.append(("popen", a[-1]))
    try:
        fo.open_with_default_app(slashed)
    finally:
        fo._windows_shell_open = orig_win
        if orig_start is not None:
            os.startfile = orig_start
        fo.subprocess.Popen = orig_popen

    check(len(seen) == 1, "開く経路は 1 本だけ通る（%s）" % (seen,))
    if seen:
        check(seen[0][1] == os.path.normpath(os.path.abspath(img)),
              "渡すパスが正規化されている（%r）" % (seen[0][1],))
        if os.name == "nt":
            check(seen[0][0] == "shell",
                  "Windows ではアイテム付きの経路を使う（%s）" % (seen[0][0],))

    # ── 3) アイテム経路が失敗したら os.startfile へ逃げる ──────────────
    if os.name == "nt":
        seen2 = []
        fo._windows_shell_open = lambda p: False      # 失敗させる
        os.startfile = lambda p: seen2.append(p)
        try:
            fo.open_with_default_app(slashed)
        finally:
            fo._windows_shell_open = orig_win
            os.startfile = orig_start
        check(len(seen2) == 1, "失敗したら従来の経路へ逃げる（%s）" % (seen2,))

    finish(not fails)


run(s1, delay=200)
