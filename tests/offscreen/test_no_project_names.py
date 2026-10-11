# -*- coding: utf-8 -*-
"""r145: 実在プロジェクトのパス・名前をソースに残さない。

ユーザー指摘 2026-10-09:
  「例として入っている文言がプロジェクトがわかるテキストで表示されています。
    対象階層やファイル名も独自のため、絶対に記載しては駄目です。
    同様の例が他にあると大問題となるため、すべてを調査し、すべて消してください。」

このツールは守秘のかかった案件のフォルダを日常的に扱う。調査中に見た実パスを
そのままコメントやプレースホルダに書くと、画面にも配布物にも残ってしまう。

**ここでは «禁止ワードの一覧» を持たない。** 一覧を書くこと自体が、そのまま
実在名をソースに残すことになるからである。代わりに «形» を検査する:

  ui / core の文字列とコメントに «具体的な絶対パス» を書いてはいけない。
  例示が要るときは伏せ字（C:\\...\\Foo）か、下の ALLOWED にある
  «誰のものでもない» 見本パスを使う。

これなら将来どんな案件名が来ても、実パスを書いた時点で落ちる。
判定は «文字列の値» に対して行う（ソースの生テキストだと "…です:\\n%s" の
ような改行エスケープを «X:\\ で始まるパス» と誤検出するため）。
"""
import ast
import io
import os
import re
import tokenize

from _common import *  # noqa: F401,F403
from _common import finish

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# «誰のものでもない» 第 1 段。実在案件のパスは必ずここに無い名前で始まる。
ALLOWED_FIRST = {
    "work", "users", "windows", "temp", "tmp",
    "program", "program files", "program files (x86)",
    "projects", "proj", "path", "dir", "folder", "scenes",
    "sampleproject", "foo", "bar", "a", "b", "x", "y", "src", "dst",
}
# 伏せ字・書式指定・ワイルドカード・正規表現は «具体的なパス» ではない。
_MASK_HEAD = ("...", "<", "%", "{", "*", "$", "[", "?", "(")
# 第 1 段が «ただの名前» であること（正規表現や書式が紛れたら対象外）
_PLAIN = re.compile(r"^[\w.\- ()]+$", re.U)
# URL（https://… の "s:" を拾わない）や、直前が英数のものは除く
_PATHISH = re.compile(r"(?<![\w])([A-Za-z]):([\\/]{1,2})([^\s\"\'`,)\];|]*)")

fails = []
fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _ok(tail: str) -> bool:
    t = (tail or "").replace("\\", "/").lstrip("/")
    if not t:
        return True                      # "C:/" だけ＝ドライブそのもの
    if t.startswith(_MASK_HEAD):
        return True
    first = t.split("/", 1)[0]
    if not _PLAIN.match(first):
        return True                      # 正規表現・書式の断片
    return first.lower() in ALLOWED_FIRST


def _texts(path):
    """この .py に書かれている «文字列の値» と «コメント» を列挙する。"""
    src = io.open(path, encoding="utf-8").read()
    try:
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield getattr(node, "lineno", 0), node.value
    except SyntaxError:
        pass
    try:
        with io.open(path, "rb") as f:
            for tok in tokenize.tokenize(f.readline):
                if tok.type == tokenize.COMMENT:
                    yield tok.start[0], tok.string
    except Exception:
        pass


bad = []
for sub in ("ui", "core"):
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, sub)):
        if "__pycache__" in dirpath:
            continue
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            for lineno, text in _texts(path):
                for _drive, _sep, tail in _PATHISH.findall(text):
                    if _ok(tail):
                        continue
                    bad.append("%s:%d: %s" % (rel, lineno, tail[:60]))

check(not bad,
      "ソースに具体的な絶対パスが残っていない（伏せ字か見本パスを使う）"
      + ("" if not bad else ":\n  " + "\n  ".join(sorted(set(bad))[:20])))

# 画面に出る «自動命名の例» は特に重要なので名指しで押さえる
import core.auto_name as _an          # noqa: E402
import ui.settings_dialog as _sd      # noqa: E402

leaked = []
for mod in (_an, _sd):
    for lineno, text in _texts(mod.__file__):
        for _d, _s, tail in _PATHISH.findall(text):
            if not _ok(tail):
                leaked.append("%s:%d: %s"
                              % (os.path.basename(mod.__file__), lineno, tail))
check(not leaked,
      "自動命名の例が架空のパスになっている（%s）" % ("; ".join(leaked[:5]) or "OK"))

finish(not fails)
