# -*- coding: utf-8 -*-
"""共通ヘルパの import 漏れを検出する（r146）。

なぜ要るのか
------------
r145 で「ネイティブのファイルダイアログをやめる」改修をした時、呼び出し側を
``pick_directory`` / ``pick_open_file`` に差し替えたのに **import を足し忘れた**
箇所が 5 つ残った。この手の取りこぼしは

  * 画面を開くまで誰も気づかない（import 時には落ちない）
  * 落ちた先が ``except Exception`` の中だと «押しても無反応» にしか見えない

ので、実機で «コピー先の選択も、移動先の選択も、メニューの開く／インポート／
リファレンスも、全部効かない» という形になって初めて発覚した
（ユーザー報告 2026-10-11「もしかしてすべて壊れているのではないか」）。

ここで固定すること
------------------
共有モジュール（ui/dialog_util・ui/browser_util・core/file_operations）が
出している名前を関数の中で使っているのに、**その名前がどこからも取り込まれて
いない** ファイルが無いこと。AST だけで見るので実行環境に依存しない。
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


SHARED = [("ui", "dialog_util.py"), ("ui", "browser_util.py"),
          ("core", "file_operations.py")]


def _public_names(path):
    """そのモジュールが出している公開名（関数・クラス）。"""
    out = set()
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                out.add(node.name)
    return out


HELPERS = set()
for pkg, name in SHARED:
    HELPERS |= _public_names(os.path.join(ROOT, pkg, name))
check(len(HELPERS) > 10, "共有ヘルパの名前を拾えた（%d 個）" % len(HELPERS))


def _bound_names(node):
    """その AST の下で «束縛される» 名前（import・代入・def 等）。"""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                out.add(a.asname or a.name)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.Global):
            out.update(n.names)
    return out


bad = []
for pkg in ("ui", "core"):
    base = os.path.join(ROOT, pkg)
    for dirpath, _dirs, files in os.walk(base):
        if "__pycache__" in dirpath:
            continue
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            tree = ast.parse(io.open(path, encoding="utf-8").read())
            # モジュール直下の束縛（入れ子の関数の中は含めない）
            mod_names = set()
            for node in tree.body:
                mod_names |= _bound_names(node) if not isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    else {getattr(node, "name", "")}
            # 入れ子の関数は «外側の関数» ごと見る。内側の work() が
            # 外側で import した名前を使うのは正しいので、そこを誤検出しない。
            funcs = [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            nested = set()
            for f in funcs:
                for n in ast.walk(f):
                    if n is not f and isinstance(
                            n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        nested.add(id(n))
            for node in funcs:
                if id(node) in nested:
                    continue
                local = _bound_names(node)      # 入れ子の中まで含む
                for n in ast.walk(node):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) \
                            and n.id in HELPERS \
                            and n.id not in mod_names and n.id not in local:
                        bad.append("%s:%d %s" % (name, n.lineno, n.id))

check(not bad,
      "共有ヘルパの import 漏れが無い（%s）" % ("、".join(sorted(set(bad))) or "漏れなし"))

finish(not fails)
