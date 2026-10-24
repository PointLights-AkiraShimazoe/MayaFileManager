# -*- coding: utf-8 -*-
"""userSetup.py へ書くスニペットは «Maya 側で» 動く独立したコードであること。

r112 の一括変換が `_swallow()`（Manager のモジュール関数）をスニペットへ
混入させ、ポートが埋まっている 2 台目以降の Maya で NameError になり
«起動しているのに接続リストに出ない» を招いた（r119 で修復）。
ここでは「Manager の名前が混ざっていない」「単体で構文が通る」
「Maya 相当のスタブで実際にポート選択が動く」を検証する。"""
import ast
import io
import os
import re
from _common import *  # noqa: F401,F403
from _common import finish

from core import maya_bridge as _mb
from core.maya_bridge import (_US_SNIPPET, _US_BEGIN, _US_END,
                              MODULE_SNIPPET, MOD_FILE_TEXT, MODULE_NAME,
                              open_port_snippet, install_bridge,
                              is_usersetup_installed, usersetup_path,
                              PORT_RANGE)

BODIES = {"モジュール用 userSetup.py": MODULE_SNIPPET,
          "旧 userSetup スニペット（検出用）": _US_SNIPPET,
          "スクリプトエディタ用": open_port_snippet()}

# 0) r146: Maya の設定フォルダに置くファイルは ASCII のみ（文字コード事故の根絶）
assert MODULE_SNIPPET.isascii(), "モジュール用 userSetup.py に非 ASCII が混入"
assert MOD_FILE_TEXT.isascii() and MOD_FILE_TEXT.startswith("+ %s " % MODULE_NAME)
assert "./%s" % MODULE_NAME in MOD_FILE_TEXT, "モジュールのパスは .mod からの相対で書く（日本語ユーザー名を避ける）"
assert _mb.PORT_SNIPPET_BODY.isascii(), "ポート本体に非 ASCII（モジュール用に流れる）"
print("module files are pure ASCII: OK")


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
# 旧ブロックはマーカーに挟まれていること（remove_legacy_block の除去が成立する条件）
assert _US_SNIPPET.startswith(_US_BEGIN) and _US_END in _US_SNIPPET
# r146: モジュール用は **全体が try で包まれている**（Maya を止めない）
_mod_tree = ast.parse(MODULE_SNIPPET)
assert len(_mod_tree.body) == 1 and isinstance(_mod_tree.body[0], ast.Try), \
    "モジュール用 userSetup.py は 1 つの try/except で全体を包むこと"
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
    """Maya の commandPort の «要点» だけ真似る。

    q=True は «この Maya が開いているか» を答える（他プロセスのポートは
    False）。open は他プロセスが握っていれば失敗する。この違いを分けて
    持たないと «同一 Maya の二重オープン» を検出できない（r119b）。
    """

    def __init__(self, taken, mine=None):
        self.taken = set(taken)          # 他プロセス含め «使われている»
        self.mine = set(mine or [])      # この Maya が開いている
        self.opened = []

    def commandPort(self, name=None, sourceType=None, q=False, **kw):
        port = int(str(name).lstrip(":"))
        if q:
            return port in self.mine
        if port in self.taken:
            raise RuntimeError("port in use")
        self.taken.add(port)
        self.mine.add(port)
        self.opened.append(port)
        return name


def _run_snippet(taken, mine=None):
    """スニペット本体を独立した名前空間で実行し、選ばれたポートを返す。"""
    import sys
    import types
    fake = _FakeCmds(taken, mine)
    mod_cmds = types.ModuleType("maya.cmds")
    mod_cmds.commandPort = fake.commandPort
    mod_maya = types.ModuleType("maya")
    mod_maya.cmds = mod_cmds
    saved = {k: sys.modules.get(k) for k in ("maya", "maya.cmds")}
    sys.modules["maya"] = mod_maya
    sys.modules["maya.cmds"] = mod_cmds
    try:
        ns = {}
        body = re.sub(r"^#.*$", "", _mb.PORT_SNIPPET_BODY, flags=re.M)
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

# 2台目: «他の Maya» が先頭を使っている場合。query は False（他プロセスの
# ポートは見えない）だが open が失敗するので次へ進む。
# r119 の本丸: 従来は NameError でループが即死し 2 台目が接続できなかった。
port2, fake2 = _run_snippet(taken=[PORT_RANGE[0]], mine=[])
assert port2 == PORT_RANGE[1], port2
assert fake2.opened == [PORT_RANGE[1]], fake2.opened
print("second Maya skips the port another Maya holds: OK")

# r119b: «この Maya が既に開いている» 場合は何もしない。
# マネージャーから起動した Maya は -command で先にポートを開くので、
# ここで別のポートを開くと 1 つの Maya が接続リストに 2 回出てしまう。
port_self, fake_self = _run_snippet(taken=[PORT_RANGE[0]],
                                    mine=[PORT_RANGE[0]])
assert port_self == PORT_RANGE[0], port_self
assert fake_self.opened == [], fake_self.opened
print("a Maya that already has a port does not open a second one: OK")

# 全部埋まっていても例外を投げない（Maya の起動を止めない）
port3, _ = _run_snippet(taken=list(PORT_RANGE), mine=[])
assert port3 is None, port3
print("all ports taken -> returns None without raising: OK")

# ---------------------------------------------------------------------------
# r119: userSetup.py の «置き場» を推測で決めない
#   実機: «ドキュメント» 既知フォルダは OneDrive 配下なのに、Maya の
#   internalVar(userAppDir=True) は %USERPROFILE%/Documents/maya だった。
#   推測で書くと Maya が読まない場所に置くことになる（= 入れたのに効かない）。
# ---------------------------------------------------------------------------
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
# r146: モジュール方式のインストール（零接触）
#   実機事故 2026-10-24: 旧方式は他人の userSetup.py を書き換え、文字コードを
#   捨てて書き戻したため Maya が起動不能になった。新方式は Manager 専用の
#   2 ファイル（.mod ＋ モジュール内 userSetup.py）だけを書き、既存の
#   userSetup.py には **1 バイトも触らない**。
# ---------------------------------------------------------------------------
import tempfile
import shutil
tmp_home = tempfile.mkdtemp(prefix="mfm_module_")
os.environ["MAYA_APP_DIR"] = tmp_home
_mb._app_dir_confirmed = None
_mb._app_dir_override = None

# 既存の userSetup.py（ユーザーのもの）: CP932・CRLF・coding 宣言・日本語
user_us = usersetup_path()
os.makedirs(os.path.dirname(user_us))
USER_BYTES = (u"# -*- coding: cp932 -*-\r\n"
              u"# スタジオのパイプライン\r\n"
              u"import sys\r\n"
              u"sys.path.append(u'D:/パイプライン/scripts')\r\n"
              u"print('existing')\r\n").encode("cp932")
with open(user_us, "wb") as f:
    f.write(USER_BYTES)
user_mtime = os.stat(user_us).st_mtime_ns

assert not is_usersetup_installed() and not _mb.is_module_installed()
mod = install_bridge()
assert mod == _mb.bridge_mod_path(), mod
assert os.path.isfile(mod)
mus = _mb.bridge_module_usersetup()
assert os.path.isfile(mus), mus
assert mus.startswith(os.path.join(tmp_home, "modules", MODULE_NAME)), mus
with open(mod, "rb") as f:
    mod_bytes = f.read()
assert mod_bytes == MOD_FILE_TEXT.encode("ascii"), mod_bytes
with open(mus, "rb") as f:
    mus_bytes = f.read()
mus_bytes.decode("ascii")                        # 非 ASCII が無いこと
compile(mus_bytes, mus, "exec")                  # 単体で構文が通ること
assert _mb.is_module_installed() and is_usersetup_installed()
assert _mb.module_snippet_version() == _mb.SNIPPET_VERSION
assert _mb.installed_snippet_version() == _mb.SNIPPET_VERSION
assert not _mb.usersetup_is_outdated() and _mb.outdated_targets() == []
print("install_bridge writes .mod + module userSetup.py (ASCII, compiles): OK")

# 本丸: 既存の userSetup.py は 1 バイトも変わらず、バックアップも作られない
with open(user_us, "rb") as f:
    assert f.read() == USER_BYTES, "既存の userSetup.py が書き換えられた"
assert os.stat(user_us).st_mtime_ns == user_mtime, "既存の userSetup.py に触れた"
assert not [n for n in os.listdir(os.path.dirname(user_us)) if "mfm_bak" in n], \
    "触っていないのにバックアップができた"
print("install_bridge never touches the user's userSetup.py: OK")

# 再インストールは同じ内容（増殖しない・壊れない）
install_bridge()
with open(mus, "rb") as f:
    assert f.read() == mus_bytes
assert sorted(os.listdir(os.path.join(tmp_home, "modules"))) == [MODULE_NAME, MODULE_NAME + ".mod"]
print("install_bridge is idempotent: OK")

# 古い版のモジュールは «要更新» と判定される
with open(mus, "w", encoding="ascii", newline="\n") as f:
    f.write(MODULE_SNIPPET.replace("mfm-snippet-version: %d" % _mb.SNIPPET_VERSION,
                                   "mfm-snippet-version: 1"))
assert _mb.module_snippet_version() == 1
assert _mb.usersetup_is_outdated() and _mb.outdated_targets() == [None]
install_bridge()
assert not _mb.usersetup_is_outdated()
print("an old module version is detected and refreshed: OK")

# uninstall は Manager のファイルだけを消す
assert _mb.uninstall_bridge()
assert not os.path.exists(mod) and not os.path.exists(_mb.bridge_module_root())
with open(user_us, "rb") as f:
    assert f.read() == USER_BYTES
assert not _mb.uninstall_bridge(), "何も無いのに True"
print("uninstall_bridge removes only the manager's files: OK")


# ---------------------------------------------------------------------------
# r146: Maya のモジュール機構の再現 — .mod が指す scripts/ が sys.path に
#   乗り、Maya が sys.path 上の userSetup.py を順に実行する。そこで
#   専用 userSetup.py がポートを開くこと。ユーザーの userSetup.py が例外を
#   出しても、専用側は自分の try で守られていて Maya を止めないこと。
# ---------------------------------------------------------------------------
import sys
import types


def _mod_file_target(mod_path):
    """.mod の «+ 名前 版 パス» からモジュールフォルダを解く（相対は .mod 基準）。"""
    with open(mod_path, "rb") as f:
        line = f.read().decode("ascii").splitlines()[0]
    parts = line.split()
    assert parts[0] == "+" and parts[1] == MODULE_NAME, line
    rel = parts[3]
    return os.path.normpath(os.path.join(os.path.dirname(mod_path), rel))


def _fake_maya(taken, mine=None, deferred_runs=True):
    fake = _FakeCmds(taken, mine)
    mod_cmds = types.ModuleType("maya.cmds")
    mod_cmds.commandPort = fake.commandPort
    mod_utils = types.ModuleType("maya.utils")
    ran = []

    def executeDeferred(fn, *a, **k):
        ran.append(fn)
        if deferred_runs:
            fn(*a, **k)
    mod_utils.executeDeferred = executeDeferred
    mod_maya = types.ModuleType("maya")
    mod_maya.cmds = mod_cmds
    mod_maya.utils = mod_utils
    return fake, ran, {"maya": mod_maya, "maya.cmds": mod_cmds, "maya.utils": mod_utils}


def _maya_like_startup(app_dir, taken=(), mine=None, extra_paths=()):
    """Maya の起動を真似る: modules/*.mod → scripts を sys.path へ、
    その後 sys.path 上の userSetup.py を全部 exec。"""
    fake, ran, mods = _fake_maya(taken, mine)
    saved_mods = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    saved_path = list(sys.path)
    try:
        paths = [os.path.join(app_dir, "scripts")] + list(extra_paths)
        mdir = os.path.join(app_dir, "modules")
        if os.path.isdir(mdir):
            for n in sorted(os.listdir(mdir)):
                if n.endswith(".mod"):
                    paths.append(os.path.join(_mod_file_target(os.path.join(mdir, n)), "scripts"))
        sys.path[:0] = paths
        executed, errors = [], []
        for d in list(sys.path):
            us = os.path.join(d, "userSetup.py")
            if os.path.isfile(us):
                try:
                    with open(us, "rb") as f:
                        code = compile(f.read(), us, "exec")
                    exec(code, {"__name__": "__main__"})   # noqa: S102
                    executed.append(us)
                except Exception as e:
                    errors.append((us, e))
        return fake, ran, executed, errors
    finally:
        sys.path[:] = saved_path
        for k, v in saved_mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


install_bridge()
fake, ran, executed, errors = _maya_like_startup(tmp_home, taken=[PORT_RANGE[0]])
assert errors == [], errors
assert any(os.path.normcase(e) == os.path.normcase(mus) for e in executed), executed
assert len(ran) == 1, "executeDeferred が 1 回でない: %r" % (ran,)
assert fake.opened == [PORT_RANGE[1]], fake.opened
print("module userSetup.py runs through Maya's module path and opens a port: OK")

# ユーザーの userSetup.py が壊れていても（SyntaxError）専用側は走り、ポートが開く。
# ※ Maya 本体は 1 つの userSetup.py の例外で残りを止めることがあるが、
#   それはユーザー側のファイルの問題。Manager 側が **原因になる** 経路を断つのが目的。
with open(user_us, "wb") as f:
    f.write(b"# -*- coding: cp932 -*-\r\nthis is not python\r\n")
fake, ran, executed, errors = _maya_like_startup(tmp_home)
assert fake.opened == [PORT_RANGE[0]], fake.opened
assert len(errors) == 1 and errors[0][0] == user_us, errors
with open(user_us, "wb") as f:
    f.write(USER_BYTES)
print("a broken user userSetup.py does not stop the module bridge: OK")

# maya が import できない環境（mayapy 以外で exec された等）でも例外を外に出さない
saved = {k: sys.modules.get(k) for k in ("maya", "maya.cmds", "maya.utils")}
for k in saved:
    sys.modules.pop(k, None)
import builtins
_real_import = builtins.__import__


def _no_maya(name, *a, **k):
    if name == "maya" or name.startswith("maya."):
        raise ImportError("no maya here")
    return _real_import(name, *a, **k)
builtins.__import__ = _no_maya
try:
    exec(compile(MODULE_SNIPPET, "<module userSetup>", "exec"), {})   # noqa: S102
finally:
    builtins.__import__ = _real_import
    for k, v in saved.items():
        if v is not None:
            sys.modules[k] = v
print("module userSetup.py never raises even without maya: OK")


# ---------------------------------------------------------------------------
# r146: 旧方式のブロックの除去 — 文字・文字コード・改行を 1 つも変えない
# ---------------------------------------------------------------------------
_mb.uninstall_bridge()
OLD_BLOCK = (_US_BEGIN + "\n# MayaFileManager: old\n# mfm-snippet-version: 4\n"
             "def _mfm_open_bridge_port():\n    pass\n" + _US_END + "\n")
LEGACY = (u"# -*- coding: cp932 -*-\r\n"
          + OLD_BLOCK.replace("\n", "\r\n")
          + u"\r\n# スタジオのパイプライン\r\n"
          u"import sys\r\n"
          u"sys.path.append(u'D:/パイプライン/scripts')\r\n"
          u"print('existing')\r\n")
EXPECT = (u"# -*- coding: cp932 -*-\r\n"
          u"\r\n# スタジオのパイプライン\r\n"
          u"import sys\r\n"
          u"sys.path.append(u'D:/パイプライン/scripts')\r\n"
          u"print('existing')\r\n")
with open(user_us, "wb") as f:
    f.write(LEGACY.encode("cp932"))
assert _mb.legacy_block_present() and _mb.legacy_targets() == [None]
assert is_usersetup_installed(), "旧方式でも «入っている» ことは変わらない"
assert _mb.installed_snippet_version() == 4
assert _mb.usersetup_is_outdated() and _mb.outdated_targets() == [None], \
    "旧方式が残っているのに «要更新» にならない（移行の入口が無い）"
print("legacy block is detected as installed-but-outdated: OK")

p = _mb.remove_legacy_block()
assert p == user_us, p
with open(user_us, "rb") as f:
    after = f.read()
assert after == EXPECT.encode("cp932"), after
baks = [n for n in os.listdir(os.path.dirname(user_us)) if n.startswith("userSetup.py.mfm_bak_")]
assert len(baks) == 1, baks
with open(os.path.join(os.path.dirname(user_us), baks[0]), "rb") as f:
    assert f.read() == LEGACY.encode("cp932"), "バックアップが原本と違う"
assert not _mb.legacy_block_present() and not is_usersetup_installed()
assert _mb.remove_legacy_block() is None, "無いのに何かした"
print("remove_legacy_block keeps every other byte (cp932, CRLF) and backs up: OK")

# 旧ブロックが末尾にある場合（r120 以前の «末尾追記» 版）
with open(user_us, "wb") as f:
    f.write((u"# -*- coding: utf-8 -*-\nprint('a')\n\n" + OLD_BLOCK).encode("utf-8"))
_mb.remove_legacy_block()
with open(user_us, "rb") as f:
    assert f.read() == b"# -*- coding: utf-8 -*-\nprint('a')\n\n", f
print("tail-appended legacy block is removed too: OK")

# ブロックしか無いファイル（元々 Manager が作った）→ ファイルごと消え、バックアップは残る
for n in os.listdir(os.path.dirname(user_us)):
    if "mfm_bak" in n:
        os.remove(os.path.join(os.path.dirname(user_us), n))
with open(user_us, "wb") as f:
    f.write(OLD_BLOCK.encode("utf-8"))
assert _mb.remove_legacy_block() == user_us
assert not os.path.exists(user_us)
assert [n for n in os.listdir(os.path.dirname(user_us)) if "mfm_bak" in n]
print("a userSetup.py that held only our block is removed (backup kept): OK")

# 終端マーカーが欠けた壊れたブロック → マーカー以降を捨てる（手前は保つ）
with open(user_us, "wb") as f:
    f.write(("print('keep')\n" + _US_BEGIN + "\nbroken\n").encode("utf-8"))
_mb.remove_legacy_block()
with open(user_us, "rb") as f:
    assert f.read() == b"print('keep')\n", f
print("a block without its end marker is cut off safely: OK")

# 文字コードが判別できないファイルは **触らない**（ValueError、バックアップも作らない）
for n in os.listdir(os.path.dirname(user_us)):
    if "mfm_bak" in n:
        os.remove(os.path.join(os.path.dirname(user_us), n))
garbage = b"# -*- coding: utf-8 -*-\n" + _US_BEGIN.encode() + b"\n\xff\xfe\x81\x39\n" + _US_END.encode() + b"\nprint(1)\n"
with open(user_us, "wb") as f:
    f.write(garbage)
assert _mb.legacy_block_present()
try:
    _mb.remove_legacy_block()
    raise AssertionError("読めないのに書き換えた")
except ValueError:
    pass
with open(user_us, "rb") as f:
    assert f.read() == garbage
assert not [n for n in os.listdir(os.path.dirname(user_us)) if "mfm_bak" in n]
print("an undecodable userSetup.py is left untouched: OK")
os.remove(user_us)


# ---------------------------------------------------------------------------
# r119/r146: 全バージョン共通 / バージョン別 を選べること（モジュール方式）
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
common = _mb.bridge_mod_path()
per = _mb.bridge_mod_path("2026")
assert common == os.path.join(appdir, "modules", MODULE_NAME + ".mod"), common
assert per == os.path.join(appdir, "2026", "modules", MODULE_NAME + ".mod"), per
print("each target has its own .mod location: OK")

assert not is_usersetup_installed() and not is_usersetup_installed("2026")
assert _mb.installed_targets() == []
assert _mb.ineffective_versions() == ["2026", "2025", "2024"]
install_bridge("2026")
assert is_usersetup_installed("2026")
assert not is_usersetup_installed(), "バージョン別が共通まで «済み» にしている"
assert _mb.installed_targets() == ["2026"], _mb.installed_targets()
assert _mb.bridge_effective_for("2026") and not _mb.bridge_effective_for("2025")
assert _mb.ineffective_versions() == ["2025", "2024"]
install_bridge()
assert _mb.installed_targets() == [None, "2026"], _mb.installed_targets()
assert _mb.ineffective_versions() == []
# 共通側のモジュールが実際に Maya 流の読み方で動く
fake, ran, executed, errors = _maya_like_startup(appdir)
assert errors == [] and fake.opened == [PORT_RANGE[0]], (errors, fake.opened)
print("common / per-version module targets behave independently: OK")

# 旧方式が共通に残っている状態で outdated_targets が共通を指す（移行の入口）
with open(_mb.usersetup_path(), "wb") as f:
    f.write(OLD_BLOCK.encode("utf-8"))
assert _mb.outdated_targets() == [None], _mb.outdated_targets()
assert _mb.legacy_targets() == [None]
_mb.remove_legacy_block()
assert _mb.outdated_targets() == []
print("legacy block in the common userSetup.py is offered for migration: OK")

_mb._app_dir_confirmed = None
_mb._app_dir_override = None
shutil.rmtree(tmp_home, ignore_errors=True)
shutil.rmtree(appdir, ignore_errors=True)

finish()
