"""
Blender Bridge — マネージャー側（r65）
======================================

core/maya_bridge.py の Blender 版。プロトコルは同じ（1行の Python を送り、
文字列の応答を受ける）。Blender 側は resources/blender_bridge.py が待ち受ける。
ポートレンジは Maya（20261〜20269）と分けて 20271〜20279 にする。
"""

import os
import re
import shutil
from pathlib import Path
from typing import List, Optional

# 再エクスポート（blender_bridge 経由で使う側があるため消さないこと）
from core.maya_bridge import (  # noqa: F401
    MayaBridge, bridge_log, parse_identify,
)

PORT_RANGE = tuple(range(20271, 20280))
DEFAULT_PORT = 20271

_BRIDGE_SRC = Path(__file__).resolve().parent.parent / "resources" / "blender_bridge.py"
_STARTUP_NAME = "mfm_bridge.py"

# 識別: 単一の式（Blender 側は eval → 失敗時 exec）
IDENTIFY_CODE = (
    "'MFMID<' + bpy.app.version_string + '><' + (bpy.data.filepath or '')"
    " + '><' + str(os.getpid()) + '>MFMID'"
)


def scan_open_ports(host: str = "127.0.0.1", timeout: float = 0.15) -> List[int]:
    """Blender 用レンジ内で待ち受け中のポート。"""
    return _scan_range(host, timeout)


def _scan_range(host, timeout):
    import socket
    found = []
    for p in PORT_RANGE:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((host, p))
            found.append(p)
        except OSError:
            pass
        finally:
            try:
                s.close()
            except OSError:
                pass
    return found


def find_free_port(host: str = "127.0.0.1") -> int:
    used = set(_scan_range(host, 0.15))
    for p in PORT_RANGE:
        if p not in used:
            return p
    return PORT_RANGE[-1]


class BlenderBridge(MayaBridge):
    """クライアントは Maya と同一（ホスト/ポートのみ）。"""

    def __init__(self, port: int = DEFAULT_PORT, host: str = "127.0.0.1"):
        super().__init__(port=port, host=host)


# ---------------------------------------------------------------------------
# Blender 側で実行するコード（送信用文字列）
# ---------------------------------------------------------------------------

def _q(path: str) -> str:
    return (path or "").replace("\\", "/").replace("'", "\\'")


def code_open(path: str) -> str:
    return "mfm_open('%s')" % _q(path)


def code_import(path: str) -> str:
    return "mfm_import('%s')" % _q(path)


def code_link(path: str) -> str:
    return "mfm_link('%s')" % _q(path)


# ---------------------------------------------------------------------------
# 自動起動スクリプトのインストール（Maya の userSetup.py 相当）
# ---------------------------------------------------------------------------

def blender_config_root() -> Optional[Path]:
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return Path(base) / "Blender Foundation" / "Blender" if base else None
    if os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Blender"
    return Path.home() / ".config" / "blender"


def startup_dirs(versions: Optional[List[str]] = None) -> List[Path]:
    """インストール先候補 <config>/<X.Y>/scripts/startup。versions を渡すと
    それらのバージョン分を（無ければ作って）返す。既存の設定フォルダも含める。"""
    root = blender_config_root()
    if root is None:
        return []
    vers = set(versions or [])
    try:
        if root.exists():
            for entry in root.iterdir():
                if entry.is_dir() and re.fullmatch(r"\d+\.\d+", entry.name):
                    vers.add(entry.name)
    except OSError:
        pass
    return [root / v / "scripts" / "startup" for v in sorted(vers)]


def is_startup_installed(versions: Optional[List[str]] = None) -> bool:
    dirs = startup_dirs(versions)
    return bool(dirs) and all((d / _STARTUP_NAME).exists() for d in dirs)


def install_startup(versions: Optional[List[str]] = None) -> List[str]:
    """ブリッジスクリプトを各バージョンの startup へコピーする。書いたパスを返す。"""
    if not _BRIDGE_SRC.exists():
        raise FileNotFoundError(str(_BRIDGE_SRC))
    written = []
    for d in startup_dirs(versions):
        d.mkdir(parents=True, exist_ok=True)
        dst = d / _STARTUP_NAME
        shutil.copy2(str(_BRIDGE_SRC), str(dst))
        written.append(str(dst))
        bridge_log("install_startup(blender): wrote %r" % str(dst))
    return written


def refresh_installed_startup() -> List[str]:
    """«既にインストール済み» の自動起動ブリッジが古ければ最新に差し替える（r88）。

    startup へコピーした mfm_bridge.py はマネージャー更新に追従しないため、
    新機能（例: Info エディタへのログ mfm_log）が届かない。起動時に中身を比べ、
    違うものだけ上書きする。未インストールのバージョンには何もしない。"""
    if not _BRIDGE_SRC.exists():
        return []
    try:
        src = _BRIDGE_SRC.read_bytes()
    except OSError:
        return []
    updated = []
    for d in startup_dirs():
        dst = d / _STARTUP_NAME
        try:
            if dst.exists() and dst.read_bytes() != src:
                shutil.copy2(str(_BRIDGE_SRC), str(dst))
                updated.append(str(dst))
                bridge_log("refresh_startup(blender): updated %r" % str(dst))
        except OSError:
            continue
    return updated
