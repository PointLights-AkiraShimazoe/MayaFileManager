# -*- coding: utf-8 -*-
"""r146: 他アプリで作業中にフォーカスを奪わない。

ユーザー報告 2026-10-13:
  「コンパクトモードにしていて、Premiere でスペースを押しても効かず、
    まさかと思い Manager を閉じたら Premiere のスペース再生が反応する
    ようになりました。コンパクトモードであれ、通常ウィンドウであれ、
    アクティブではないとユーザーが思っている時に、操作が拘束されるのは
    有りえません。」

原因は compact の expand() が **ホバーのたびに activateWindow()** を
呼んでいたこと。パックは常に手前にいるので、Premiere へマウスを運ぶ途中で
掠めるだけでマネージャーが前に出てフォーカスを奪い、奪われた Premiere には
キー入力が届かなくなる。

ここで固定すること:
  1) ホバーは «少し留まってから» 開く（かすめただけでは開かない）
  2) 開く時、**このアプリが前にいる時だけ** アクティブにする
  3) 奪ってしまった場合に備えて、畳む時に元のアプリへ返す
  4) 定期処理（監視タイマー）からフォーカスを触らない
"""
import ast
import io
import os

from _common import *  # noqa: F401,F403
from _common import finish

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


cm = io.open(os.path.join(ROOT, "ui", "compact_mode.py"), encoding="utf-8").read()

# 1) ホバーの滞留
from ui.compact_mode import _OpenButton, CompactController
check(hasattr(_OpenButton, "HOVER_OPEN_MS") and _OpenButton.HOVER_OPEN_MS >= 150,
      "かすめただけでは開かない（%s ms 留まってから）"
      % getattr(_OpenButton, "HOVER_OPEN_MS", None))
check("def leaveEvent" in cm, "離れたら開くのを取り消す")

# 2) フォアグラウンドを奪わない
from core import win_topmost
check(hasattr(win_topmost, "app_is_foreground"),
      "«このアプリが前にいるか» を判定できる")
check("app_is_foreground()" in cm, "開く前にそれを確かめている")
check("WA_ShowWithoutActivating" in cm,
      "前にいない時はフォーカスを奪わずに出す")

# expand の中で activateWindow が «無条件» に呼ばれていないこと
tree = ast.parse(cm)
expand = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "expand":
        expand = node
hits = []
for node in ast.walk(expand):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "activateWindow":
        hits.append(node.lineno)
check(len(hits) <= 1, "expand での activateWindow は 1 箇所だけ（%r）" % hits)
for ln in hits:
    # その行が条件分岐の中にあること（無条件の呼び出しを許さない）
    src_line = cm.splitlines()[ln - 1]
    indent = len(src_line) - len(src_line.lstrip())
    check(indent >= 12, "条件の中でだけ呼ぶ（行 %d, indent=%d）" % (ln, indent))

# 3) 返す口
check(hasattr(win_topmost, "set_foreground"), "元のアプリへ返す口がある")
check("_return_foreground" in cm and "self._return_foreground()" in cm,
      "畳む時に返している")

# 4) 監視タイマーからフォーカスを触らない
tick = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_tick":
        tick = node
bad = [n.func.attr for n in ast.walk(tick)
       if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
       and n.func.attr in ("activateWindow", "setFocus", "raise_")]
check(not bad, "定期監視からフォーカスを触らない（%r）" % (bad,))

# アプリ全体で «無条件に前へ出る» 箇所が増えていないこと
allowed = {
    "ui/dialog_util.py",          # 道具窓を開く（ユーザーが押した）
    "ui/compact_mode.py",         # 上で個別に確かめた
    "ui/main_window.py",          # テーマ/言語の作り直し（ユーザー操作）
    "main.py",                    # 起動・二重起動ガード
    "core/win_topmost.py",        # «返す» 側の実装（docstring の説明文）
}
found = []
for pkg in ("ui", "core"):
    for dirpath, _d, files in os.walk(os.path.join(ROOT, pkg)):
        if "__pycache__" in dirpath:
            continue
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT).replace("\\", "/")
            if rel in allowed:
                continue
            text = io.open(os.path.join(dirpath, name), encoding="utf-8").read()
            if "activateWindow()" in text:
                found.append(rel)
check(not found,
      "想定外の場所から前面化していない（%s）" % (", ".join(found) or "なし"))

finish(not fails)
