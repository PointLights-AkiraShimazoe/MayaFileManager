# -*- coding: utf-8 -*-
"""Blender 連携（r65）: 形式振り分け・送信コード・ブリッジスクリプトの実行ループ・
インストール先の算出。Blender 本体は不要。"""
import importlib.util
import os
import threading
from _common import *  # noqa: F401,F403
from _common import finish, ROOT
from core.file_operations import dcc_for_path, BLENDER_IMPORT_EXTENSIONS, MAYA_IMPORT_EXTENSIONS
from core import blender_bridge as bb
from core.blender_version import find_installed_blender_versions, BlenderInstallation

# 1) 振り分け
assert dcc_for_path("a.blend", "maya") == "blender"
assert dcc_for_path("a.ma", "blender") == "maya"
assert dcc_for_path("a.fbx", "blender") == "blender" and dcc_for_path("a.fbx", "maya") == "maya"
assert dcc_for_path("a.gltf", "maya") == "blender"     # Blender のみの形式
assert ".fbx" in BLENDER_IMPORT_EXTENSIONS and ".fbx" in MAYA_IMPORT_EXTENSIONS
print("dcc_for_path: OK")

# 2) 送信コード（パスのエスケープ）
assert bb.code_open("C:\\x\\it's.blend") == "mfm_open('C:/x/it\\'s.blend')"
assert bb.code_link("/a/b.blend").startswith("mfm_link(")
assert bb.PORT_RANGE[0] == 20271 and not (set(bb.PORT_RANGE) & set(range(20261, 20270)))
assert "MFMID<" in bb.IDENTIFY_CODE and "bpy.app.version_string" in bb.IDENTIFY_CODE
print("bridge codes / port range: OK")

# 3) Blender 側スクリプトを bpy 無しで読み込み、実行ループが eval/exec/エラーを返す
spec = importlib.util.spec_from_file_location(
    "mfm_blender_bridge", os.path.join(ROOT, "resources", "blender_bridge.py"))
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
assert mod.bpy is None
def run_code(code):
    ev = threading.Event(); box = {}
    mod._queue.put((code, ev, box)); mod._pump(); assert ev.is_set()
    return box["reply"]
assert run_code("1 + 1") == "2"
assert run_code("x = 5") == ""                      # 文は exec（応答なし）
assert run_code("1/0").startswith("Error:")         # 例外はテキストで返す
assert run_code("os.path.basename('/a/b.blend')") == "b.blend"
print("blender_bridge.py pump (eval/exec/error): OK")

# 4) サーバ: 実ソケットで往復（レンジ内の空きポートで）
import socket
srv = None
for p in bb.PORT_RANGE:
    try:
        srv = mod._bind(p); break
    except OSError:
        continue
assert srv is not None
threading.Thread(target=mod._serve, args=(srv,), daemon=True).start()
def _pump_loop():
    import time
    for _ in range(50):
        mod._pump(); time.sleep(0.02)
threading.Thread(target=_pump_loop, daemon=True).start()
ok, reply = bb.BlenderBridge(p).send_python("'MFMID<' + '4.2.0' + '><><123>MFMID'", timeout=3.0)
assert ok and bb.parse_identify(reply) == ("4.2.0", "", 123), (ok, reply)
assert p in bb.scan_open_ports()
srv.close()
print("bridge socket round trip + identify parse: OK")

# 5) インストール先・検出（環境依存だが落ちないこと）
insts = find_installed_blender_versions()
assert all(isinstance(i, BlenderInstallation) for i in insts)
dirs = bb.startup_dirs(["4.2"])
assert dirs and all(str(d).endswith(os.path.join("scripts", "startup")) for d in dirs), dirs
print("blender detection/startup dirs: OK (%d installs)" % len(insts))
finish()
