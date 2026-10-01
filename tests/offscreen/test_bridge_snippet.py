# -*- coding: utf-8 -*-
"""userSetup.py へ書くスニペットは «Maya 側で» 動く独立したコードであること。

r112 の一括変換が `_swallow()`（Manager のモジュール関数）をスニペットへ
混入させ、ポートが埋まっている 2 台目以降の Maya で NameError になり
«起動しているのに接続リストに出ない» を招いた（r119 で修復）。
ここでは「Manager の名前が混ざっていない」「単体で構文が通る」
「Maya 相当のスタブで実際にポート選択が動く」を検証する。"""
import ast
import os
import re
from _common import *  # noqa: F401,F403
from _common import finish

from core.maya_bridge import (_US_SNIPPET, _US_BEGIN, _US_END,
                              open_port_snippet, install_usersetup,
                              is_usersetup_installed, usersetup_path,
                              PORT_RANGE)

BODIES = {"userSetup スニペット": _US_SNIPPET,
          "スクリプトエディタ用": open_port_snippet()}

# 1) Manager 側の名前が混ざっていないこと（Maya では NameError になる）
FORBIDDEN = ("_swallow", "core.diag", "from core", "import core",
             "_mfm_log", "bridge_log", "tr(")
for name, body in BODIES.items():
    for bad in FORBIDDEN:
        assert bad not in body, "%s に Manager 側の名前 %r が混入" % (name, bad)
print("snippet has no manager-side names: OK")

# 2) 単体で構文が通ること（userSetup.py は丸ごと exec される）
for name, body in BODIES.items():
    ast.parse(body)
# マーカーに挟まれていること（install_usersetup の置換が成立する条件）
assert _US_SNIPPET.startswith(_US_BEGIN) and _US_END in _US_SNIPPET
print("snippet parses standalone and keeps its markers: OK")

# 3) except は «素の pass» であること（将来の一括変換への歯止め）
for name, body in BODIES.items():
    tree = ast.parse(body)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            assert all(isinstance(st, ast.Pass) for st in node.body), \
                "%s の except が pass 以外になっている" % name
print("every except in the snippet is a bare pass: OK")


# 4) Maya 相当のスタブで実際に動かす: 埋まっているポートは飛ばし、
#    空いている最初のポートを開いて返すこと
class _FakeCmds(object):
    def __init__(self, taken):
        self.taken = set(taken)
        self.opened = []

    def commandPort(self, name=None, sourceType=None, q=False, **kw):
        if q:                       # 問い合わせ: 使用中なら True
            port = int(str(name).lstrip(":"))
            return port in self.taken
        port = int(str(name).lstrip(":"))
        if port in self.taken:
            raise RuntimeError("port in use")
        self.taken.add(port)
        self.opened.append(port)
        return name


def _run_snippet(taken):
    """スニペット本体を独立した名前空間で実行し、選ばれたポートを返す。"""
    import sys
    import types
    fake = _FakeCmds(taken)
    mod_cmds = types.ModuleType("maya.cmds")
    mod_cmds.commandPort = fake.commandPort
    mod_maya = types.ModuleType("maya")
    mod_maya.cmds = mod_cmds
    saved = {k: sys.modules.get(k) for k in ("maya", "maya.cmds")}
    sys.modules["maya"] = mod_maya
    sys.modules["maya.cmds"] = mod_cmds
    try:
        ns = {}
        body = re.sub(r"^#.*$", "", _US_SNIPPET, flags=re.M)
        exec(compile(body, "<usersetup>", "exec"), ns)   # noqa: S102
        return ns["_mfm_open_bridge_port"](), fake
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


port, fake = _run_snippet(taken=[])
assert port == PORT_RANGE[0], port
print("first Maya takes the first port: OK")

# 2台目: 先頭が埋まっていても «次» を取る（r119 の本丸。従来は NameError で
# ループが即死し、2 台目以降が一切接続できなかった）
port2, fake2 = _run_snippet(taken=[PORT_RANGE[0]])
assert port2 == PORT_RANGE[1], port2
assert fake2.opened == [PORT_RANGE[1]], fake2.opened
print("second Maya skips the taken port and opens the next: OK")

# 全部埋まっていても例外を投げない（Maya の起動を止めない）
port3, _ = _run_snippet(taken=list(PORT_RANGE))
assert port3 is None, port3
print("all ports taken -> returns None without raising: OK")

# 5) install_usersetup は書いた内容を is_usersetup_installed が認識すること
import tempfile
tmp_home = tempfile.mkdtemp(prefix="mfm_usersetup_")
os.environ["MAYA_APP_DIR"] = tmp_home
p = install_usersetup()
assert os.path.isfile(p) and p == usersetup_path(), p
assert is_usersetup_installed()
with open(p, encoding="utf-8") as f:
    written = f.read()
assert "_swallow" not in written, "書き込んだ userSetup.py に _swallow が入っている"
ast.parse(written)
# 二重実行で重複しない（既存ブロックの置換）
install_usersetup()
with open(p, encoding="utf-8") as f:
    again = f.read()
assert again.count(_US_BEGIN) == 1, "再インストールでブロックが増えた"
print("install_usersetup writes a clean, idempotent block: OK")

finish()
