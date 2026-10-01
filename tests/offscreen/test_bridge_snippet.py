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


# ---------------------------------------------------------------------------
# r119: userSetup.py の «置き場» を推測で決めない
#   実機: «ドキュメント» 既知フォルダは OneDrive 配下なのに、Maya の
#   internalVar(userAppDir=True) は %USERPROFILE%/Documents/maya だった。
#   推測で書くと Maya が読まない場所に置くことになる（= 入れたのに効かない）。
# ---------------------------------------------------------------------------
from core import maya_bridge as _mb

os.environ.pop("MAYA_APP_DIR", None)
_mb._app_dir_confirmed = None
_mb._app_dir_override = None

# 1) Maya 本人の答え（APP_DIR_CODE の応答）を取り出せる
reply = "MFMDIR<C:/Users/owner/Documents/maya/>MFMDIR"
got = _mb.parse_app_dir(reply)
assert got and got.replace("\\", "/").rstrip("/") == "C:/Users/owner/Documents/maya", got
assert _mb.parse_app_dir("") is None and _mb.parse_app_dir("ごみ") is None
print("parse_app_dir extracts Maya's own answer: OK")

# 2) 確定値は他の全てに優先する（MAYA_APP_DIR より «も» 上）
#    MAYA_APP_DIR は Maya.env や起動バッチで «Maya 側にだけ» 設定され得る。
#    Manager から見えている値が Maya の実際の値とは限らないので、
#    聞けた時は必ずそちらを採る。
os.environ["MAYA_APP_DIR"] = "C:/env_only/maya"
_mb.set_maya_app_dir("C:/real/maya", confirmed=True)
assert _mb.app_dir_confirmed() and _mb.app_dir_source() == "confirmed"
assert _mb.maya_app_dir().replace("\\", "/") == "C:/real/maya"
assert _mb.usersetup_path().replace("\\", "/") == "C:/real/maya/scripts/userSetup.py"
print("confirmed app dir beats MAYA_APP_DIR and drives usersetup_path: OK")

# 2b) 聞けていない時は 手動指定 > MAYA_APP_DIR > 実在性 の順
_mb._app_dir_confirmed = None
assert _mb.app_dir_confirmed() is False, "env だけで «確認済み» にしてはいけない"
assert _mb.app_dir_source() == "env"
assert _mb.maya_app_dir().replace("\\", "/") == "C:/env_only/maya"
_mb.set_maya_app_dir("C:/chosen/maya")
assert _mb.app_dir_source() == "manual"
assert _mb.maya_app_dir().replace("\\", "/") == "C:/chosen/maya"
_mb._app_dir_override = None
os.environ.pop("MAYA_APP_DIR", None)
print("manual > MAYA_APP_DIR, and env alone is never 'confirmed': OK")

# 3) 聞けない時は «実在するそれらしい方» を選ぶ（空の既知フォルダに負けない）
_mb._app_dir_confirmed = None
_mb._app_dir_override = None
base = tempfile.mkdtemp(prefix="mfm_appdir_")
real = os.path.join(base, "home_docs", "maya")       # 本物: 2026/ と scripts/ がある
fake = os.path.join(base, "onedrive_docs", "maya")   # 既知フォルダ側: 空
os.makedirs(os.path.join(real, "2026"))
os.makedirs(os.path.join(real, "scripts"))
os.makedirs(fake)
_orig_cands = _mb.maya_app_dir_candidates
_mb.maya_app_dir_candidates = lambda: [real, fake]
try:
    assert _mb._looks_like_maya_app_dir(real) > _mb._looks_like_maya_app_dir(fake)
    assert _mb.maya_app_dir() == real, _mb.maya_app_dir()
    assert _mb.app_dir_source() == "found"
    # どちらも実在しないなら «先頭»（%USERPROFILE%/Documents/maya）を採る。
    # 既知フォルダ（OneDrive 側）へ作ると Maya が読まないファイルになる。
    _mb.maya_app_dir_candidates = lambda: [os.path.join(base, "nope1"),
                                           os.path.join(base, "nope2")]
    assert _mb.maya_app_dir() == os.path.join(base, "nope1")
    assert _mb.app_dir_source() == "guess"
finally:
    _mb.maya_app_dir_candidates = _orig_cands
    _mb._app_dir_confirmed = None
    _mb._app_dir_override = None
print("falls back to the candidate that actually looks like Maya's dir: OK")


# ---------------------------------------------------------------------------
# r119: 全バージョン共通 / バージョン別 を選べること
#   セットアップ時に Maya は起動していないことの方が多いので、
#   «実在するバージョンフォルダ» を見て対象を出す。
# ---------------------------------------------------------------------------
_mb._app_dir_confirmed = None
_mb._app_dir_override = None
os.environ.pop("MAYA_APP_DIR", None)
appdir = tempfile.mkdtemp(prefix="mfm_appdir2_")
for v in ("2024", "2026", "2025", "notaversion", "1999"):
    os.makedirs(os.path.join(appdir, v))
os.makedirs(os.path.join(appdir, "scripts"))
_mb.set_maya_app_dir(appdir, confirmed=True)

assert _mb.installed_versions() == ["2026", "2025", "2024"], _mb.installed_versions()
common = _mb.usersetup_path()
per = _mb.usersetup_path("2026")
assert common == os.path.join(appdir, "scripts", "userSetup.py"), common
assert per == os.path.join(appdir, "2026", "scripts", "userSetup.py"), per
assert common != per
print("version folders are listed and each has its own userSetup path: OK")

assert not _mb.is_usersetup_installed() and not _mb.is_usersetup_installed("2026")
assert _mb.installed_targets() == []
_mb.install_usersetup("2026")
assert _mb.is_usersetup_installed("2026")
assert not _mb.is_usersetup_installed(), "バージョン別が共通まで «済み» にしている"
assert _mb.installed_targets() == ["2026"], _mb.installed_targets()
_mb.install_usersetup()
assert _mb.installed_targets() == [None, "2026"], _mb.installed_targets()
for t in (None, "2026"):
    with open(_mb.usersetup_path(t), encoding="utf-8") as f:
        body = f.read()
    assert "_swallow" not in body
    ast.parse(body)
print("install targets are independent and both write a clean snippet: OK")

# 対象を選ぶダイアログ: 共通＋実在バージョンが並び、既定は «共通» だけ
from ui.maya_bridge_dialog import MayaBridgeDialog
_mb._app_dir_confirmed = None
_mb.set_maya_app_dir(appdir, confirmed=True)
fresh = tempfile.mkdtemp(prefix="mfm_appdir3_")
os.makedirs(os.path.join(fresh, "2027"))
_mb._app_dir_confirmed = None
_mb.set_maya_app_dir(fresh, confirmed=True)
d = MayaBridgeDialog()
vers = [v for v, _cb in d._targets]
assert vers == [None, "2027"], vers
assert d.selected_targets() == [None], d.selected_targets()
# バージョン別だけを選べる
d._targets[0][1].setChecked(False)
d._targets[1][1].setChecked(True)
assert d.selected_targets() == ["2027"], d.selected_targets()
# 設定済みのものは既定でチェックしない（勝手に上書きしない）
_mb.install_usersetup(None)
d2 = MayaBridgeDialog()
assert d2.selected_targets() == [], d2.selected_targets()
d.deleteLater(); d2.deleteLater()
print("dialog offers all-versions + each version and never pre-checks "
      "what is already installed: OK")

finish()
