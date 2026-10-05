# -*- coding: utf-8 -*-
"""r121: **EXE 版だけ機能が欠ける** を二度と起こさないための関所。

2026-10-05 実機障害:
  MayaFileManager.spec の excludes に 'xml' が入っていた。
  core/integrations/svn_provider.py はモジュール先頭で
      import xml.etree.ElementTree
  しているため、EXE 版でだけ core.integrations の import が丸ごと失敗し、
  Git / SVN / Perforce / クラウドの連携が **すべて無効**（バッジも
  右クリックメニューも出ない）になっていた。開発版では標準ライブラリが
  普通に入っているので誰も気付けず、そのまま配布してしまった。

ここで検証すること:
  1. spec の excludes に挙げた名前を、アプリのソースが import していないこと
  2. 連携の各プロバイダが «実際に import できる» こと（import 時エラーの検知）
  3. 連携の起動失敗を «黙って» 握り潰していないこと
"""
import ast
import io
import os
import re
from _common import *  # noqa: F401,F403
from _common import finish

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 1) excludes とソースの import を突き合わせる ──────────────────────
spec = io.open(os.path.join(ROOT, "MayaFileManager.spec"),
               encoding="utf-8").read()


def _names(block: str):
    """リスト内の文字列リテラルだけを拾う（# 以降のコメントは無視）。
    コメントに名前を書いただけで «除外している» と誤判定しないため。"""
    body = "\n".join(re.sub(r"#.*$", "", ln) for ln in block.split("\n"))
    return set(re.findall(r"'([A-Za-z_][\w.]*)'", body))


m = re.search(r"excludes=\[(.*?)\]", spec, re.S)
assert m, "spec から excludes を読めない"
excluded = _names(m.group(1))
assert excluded, "excludes が空に見える（正規表現が壊れている）"
print("spec の excludes: %d 件" % len(excluded))

# アプリが実行時に読むソース（テストとビルド補助は対象外）
targets = []
for sub in ("core", "ui", "resources"):
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, sub)):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                targets.append(os.path.join(dirpath, fn))
targets.append(os.path.join(ROOT, "main.py"))
targets.append(os.path.join(ROOT, "run_in_maya.py"))

# Maya 内でしか使わないものは EXE に不要（excludes で正しい）
ALLOWED_IN_MAYA_ONLY = {"maya", "maya.cmds", "maya.mel"}

bad = []
for path in targets:
    if not os.path.exists(path):
        continue
    try:
        tree = ast.parse(io.open(path, encoding="utf-8").read())
    except SyntaxError as e:
        bad.append((path, "構文エラー", str(e)))
        continue
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for nm in names:
            top = nm.split(".")[0]
            if top in ALLOWED_IN_MAYA_ONLY:
                continue
            if top in excluded or nm in excluded:
                bad.append((os.path.relpath(path, ROOT), nm, node.lineno))

assert not bad, (
    "spec の excludes に入っているモジュールをアプリが import している。\n"
    "このまま EXE を作ると «開発版では動くのにリリース版だけ落ちる» 状態に\n"
    "なります。excludes から外すか、その import をやめてください:\n"
    + "\n".join("  %s:%s  import %s" % (f, l, n) for f, n, l in bad))
print("excludes とソースの import に食い違いなし: OK")

# ── 2) 連携プロバイダが実際に import できること ───────────────────────
import importlib
for mod in ("core.integrations",
            "core.integrations.manager",
            "core.integrations.base",
            "core.integrations.git_provider",
            "core.integrations.svn_provider",
            "core.integrations.p4_provider",
            "core.integrations.cloud_provider"):
    importlib.import_module(mod)
from core.integrations import get_manager
mgr = get_manager()
keys = sorted(p.key for p in mgr.providers)
assert keys == ["cloud", "git", "p4", "svn"], ("プロバイダが欠けている", keys)
print("連携プロバイダ 4 種をすべて import できる: OK")

# spec の hiddenimports にも連携モジュールが挙がっていること
hm = re.search(r"hiddenimports=\[(.*?)\]", spec, re.S)
assert hm, "spec から hiddenimports を読めない"
hidden = _names(hm.group(1))
for need in ("core.integrations", "core.integrations.svn_provider",
             "xml.etree.ElementTree"):
    assert need in hidden, ("hiddenimports に %s が無い" % need, sorted(hidden))
print("hiddenimports に連携モジュールと xml が明示されている: OK")

# ── 3) 起動失敗を黙って握り潰していないこと ───────────────────────────
bp = io.open(os.path.join(ROOT, "ui", "browser_panel.py"), encoding="utf-8").read()
i = bp.index("integrations start error")
tail = bp[i:i + 1200]
assert "status_message.emit" in tail, \
    "連携の起動に失敗しても画面に何も出ない（黙って無効化している）"
print("integrations の起動失敗は画面にも出る: OK")

finish(True)
