"""
Maya version detection.

- Scans OS-standard install paths for installed Maya versions.
- Detects the required Maya version from .ma / .mb file headers.
- Provides launch helpers (standalone and socket-based Maya command execution).
"""

import os
import re
import struct
import subprocess
import platform
from pathlib import Path
from typing import Optional, List, Dict

# ---------------------------------------------------------------------------
# Platform-specific Maya install roots
# ---------------------------------------------------------------------------

def get_maya_install_roots() -> List[Path]:
    """Return candidate root directories where Maya versions are installed."""
    system = platform.system()
    roots = []
    if system == "Windows":
        roots = [
            Path(r"C:\Program Files\Autodesk"),
            Path(r"C:\Program Files (x86)\Autodesk"),
        ]
        # Also check ADSK_MAYA_INSTALL_PATH env
        env_path = os.environ.get("ADSK_MAYA_INSTALL_PATH")
        if env_path:
            roots.append(Path(env_path).parent)
    elif system == "Darwin":
        roots = [Path("/Applications/Autodesk")]
    else:  # Linux
        roots = [Path("/usr/autodesk"), Path("/opt/autodesk")]
    return roots


# ---------------------------------------------------------------------------
# Installed Maya version discovery
# ---------------------------------------------------------------------------

class MayaInstallation:
    """Represents a single Maya installation."""

    def __init__(self, version: str, path: Path):
        self.version = version          # e.g. "2023", "2024", "2025"
        self.path = path                # Root install dir
        self.executable = self._find_executable()

    def _find_executable(self) -> Optional[Path]:
        system = platform.system()
        if system == "Windows":
            candidates = [
                self.path / "bin" / "maya.exe",
                self.path / "Maya.exe",
            ]
        elif system == "Darwin":
            candidates = [
                self.path / "Maya.app" / "Contents" / "bin" / "maya",
                self.path / "bin" / "maya",
            ]
        else:
            candidates = [
                self.path / "bin" / "maya",
                self.path / "maya",
            ]
        for c in candidates:
            if c.exists():
                return c
        return None

    @property
    def is_available(self) -> bool:
        return self.executable is not None and self.executable.exists()

    @property
    def version_int(self) -> int:
        try:
            return int(self.version)
        except ValueError:
            return 0

    def __repr__(self) -> str:
        status = "✓" if self.is_available else "✗"
        return f"Maya {self.version} [{status}] @ {self.path}"


# 起動先として並べる Maya の下限（r146）。以前は 2023 で切っていたため、
# 新しく入れた Maya 2022 が一覧に出なかった（ユーザー報告 2026-10-26）。
# Manager «本体を Maya の中で動かす» のは 2023+ だが、«起動先として選ぶ»
# だけなら古い版でも困らない。«PC に入っているものが正»。
MIN_LISTED_MAYA = 2015


def _registry_maya_locations():
    """Windows のレジストリに登録された Maya（版, インストール先）の一覧。

    HKLM\\SOFTWARE\\Autodesk\\Maya\\<版>\\Setup\\InstallPath の
    MAYA_INSTALL_LOCATION。標準外の場所（別ドライブ等）に
    入れた Maya もここに載る。PC のインストーラが書く «正» の記録。"""
    out = []
    if platform.system() != "Windows":
        return out
    try:
        import winreg  # type: ignore
    except ImportError:
        return out
    for view in (getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
        try:
            base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Autodesk\Maya",
                                  0, winreg.KEY_READ | view)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    ver = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                if not re.match(r"^\d{4}(\.\d+)?$", ver):
                    continue
                try:
                    k = winreg.OpenKey(base, ver + r"\Setup\InstallPath", 0,
                                       winreg.KEY_READ | view)
                    loc, _t = winreg.QueryValueEx(k, "MAYA_INSTALL_LOCATION")
                    winreg.CloseKey(k)
                except OSError:
                    continue
                if loc:
                    out.append((ver, Path(str(loc).rstrip("\\/"))))
        finally:
            winreg.CloseKey(base)
    return out


def find_installed_maya_versions(min_version: int = MIN_LISTED_MAYA) -> List[MayaInstallation]:
    """
    PC に入っている Maya（min_version 以上）を版の昇順で返す。

    探す場所: 標準の配置（Program Files\\Autodesk\\MayaYYYY 等）と、Windows の
    レジストリ（インストーラが書く記録。標準外の場所も載る）。
    実行ファイルが実在するものだけ（アンインストール済みは出さない）。
    """
    found: Dict[str, MayaInstallation] = {}
    version_pattern = re.compile(r"Maya(\d{4}(?:\.\d+)?)")

    def _take(ver_str, path):
        try:
            ver_int = int(ver_str.split(".")[0])
        except ValueError:
            return
        if ver_int < min_version or ver_str in found:
            return
        inst = MayaInstallation(ver_str, path)
        if inst.is_available:
            found[ver_str] = inst

    for root in get_maya_install_roots():
        if not root.exists():
            continue
        try:
            for entry in root.iterdir():
                m = version_pattern.match(entry.name)
                if m:
                    _take(m.group(1), entry)
        except (PermissionError, OSError):
            continue
    try:
        for ver_str, path in _registry_maya_locations():
            _take(ver_str, path)
    except Exception:
        pass

    return sorted(found.values(), key=lambda x: x.version_int)


def installs_signature(installs) -> tuple:
    """インストール一覧の «中身» の指紋（版と実行ファイル）。変化の検出用。"""
    out = []
    for i in installs or []:
        exe = getattr(i, "executable", None)
        out.append((str(getattr(i, "version", "")),
                    os.path.normcase(str(exe)) if exe else ""))
    return tuple(sorted(out))


# ---------------------------------------------------------------------------
# Maya version detection from file content
# ---------------------------------------------------------------------------

# Maya ASCII header regex: requires "Maya ASCII <version>"
_MA_VERSION_RE = re.compile(
    r"//Maya ASCII (\S+) scene",
    re.IGNORECASE,
)

# Maya Binary magic bytes + version field offset
_MB_MAGIC = b"FOR4"          # Maya binary uses IFF format
_MB_VERSION_TAG = b"VERS"

def detect_version_from_file(file_path: str) -> Optional[str]:
    """
    Read a .ma or .mb file and return the Maya version string it was saved with,
    e.g. "2023", "2024.2".  Returns None on failure.
    """
    path = Path(file_path)
    if not path.exists():
        return None

    ext = path.suffix.lower()
    if ext == ".ma":
        return _detect_from_ma(path)
    elif ext == ".mb":
        return _detect_from_mb(path)
    return None


def _detect_from_ma(path: Path) -> Optional[str]:
    """Parse Maya ASCII header (first ~50 lines)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 60:
                    break
                m = _MA_VERSION_RE.search(line)
                if m:
                    raw = m.group(1)
                    # e.g. "2023" or "2023.0.1"
                    return raw.split(".")[0]
    except OSError:
        pass
    return None


def _detect_from_mb(path: Path) -> Optional[str]:
    """
    Parse Maya Binary (IFF) header to extract VERS chunk.
    Maya binary structure: FOR4 <size> Maya <chunks...>
    VERS chunk contains the version string.
    """
    try:
        with open(path, "rb") as f:
            data = f.read(4096)  # Header is always in first 4 KB

        # Quick sanity check
        if not data.startswith(_MB_MAGIC) and not data.startswith(b"FOR8"):
            return None

        # Walk IFF chunks looking for VERS
        i = 0
        while i < len(data) - 8:
            tag = data[i:i+4]
            try:
                size = struct.unpack_from(">I", data, i+4)[0]
            except struct.error:
                break

            if tag == _MB_VERSION_TAG:
                raw = data[i+8:i+8+size].rstrip(b"\x00").decode("ascii", errors="replace")
                return raw.split(".")[0]

            # Skip to next chunk (align to 4 bytes)
            skip = 8 + size
            if skip % 4:
                skip += 4 - (skip % 4)
            i += skip

    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Version string → MayaInstallation matching
# ---------------------------------------------------------------------------

def best_match(detected_version: Optional[str],
               installations: List[MayaInstallation]) -> Optional[MayaInstallation]:
    """
    Given a detected version string and available installations,
    return the best matching MayaInstallation (exact > major fallback > latest).
    """
    if not installations:
        return None
    if not detected_version:
        return installations[-1]  # Latest

    # Exact match
    for inst in installations:
        if inst.version == detected_version:
            return inst

    # Major version match
    major = detected_version.split(".")[0]
    for inst in installations:
        if inst.version.startswith(major):
            return inst

    # Fallback: latest
    return installations[-1]


# ---------------------------------------------------------------------------
# Launch Maya
# ---------------------------------------------------------------------------

# mayapy / Maya standalone が設定し、別バージョンの maya.exe 起動を壊す環境変数。
# これらを継承したまま maya.exe を起動すると、起動中の Maya（mayapy）の Python/
# スクリプトパスを参照してしまい、MayaUSD メニュー初期化(capitalizeString)等が
# 失敗する。launch 時に除去してクリーンに起動する。
_CONFLICTING_ENV_KEYS = (
    "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "PYTHONNOUSERSITE",
    "MAYA_LOCATION", "MAYA_SCRIPT_PATH", "MAYA_PLUG_IN_PATH",
    "MAYA_MODULE_PATH", "MAYA_PRESET_PATH", "MAYA_SCRIPT_BASE",
    "MAYA_CUSTOM_TEMPLATE_PATH", "XBMLANGPATH",
    "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM",
    "QT_AUTO_SCREEN_SCALE_FACTOR", "QT_SCALE_FACTOR",
)


def _clean_launch_env() -> Dict[str, str]:
    """maya.exe を素のシェルから起動したのと同等のクリーンな環境を返す。"""
    env = dict(os.environ)
    for k in _CONFLICTING_ENV_KEYS:
        env.pop(k, None)
    return env


def parse_env_text(text: str) -> Dict[str, str]:
    """「KEY=VALUE」を並べた文字列を辞書にする（r146）。

    区切りは **改行か ;**。``#`` 以降は注記として捨てる。値の前後の空白と
    引用符は落とす（``MAYA_UI_LANGUAGE = "en_US"`` も通る）。
    bat の ``SET KEY=VALUE`` を貼れるよう、先頭の ``set`` も許す。
    """
    out: Dict[str, str] = {}
    for raw in re.split(r"[\r\n;]+", str(text or "")):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line[:4].lower() == "set ":
            line = line[4:].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if not k:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def _expand_wrapper(wrapper: str, exe: str, file_path: str,
                    port: Optional[int]) -> List[str]:
    """ラッパー指定を実際のコマンド列にする（r146）。

    ``{exe}`` ``{file}`` ``{port}`` を差し替える。プレースホルダが 1 つも
    書かれていなければ «ラッパー exe file» の順で後ろに付ける（よくある
    ``run.bat <maya.exe> <file>`` の形）。
    """
    import shlex
    try:
        parts = shlex.split(wrapper, posix=False)
    except ValueError:
        parts = wrapper.split()
    # posix=False は引用符を token に残す。Popen にリストで渡すと «"D:/a b/x.bat"»
    # という名前のファイルを探しに行って失敗するので、ここで外す
    # （空白入りのパスは引用符で書くのが自然なので、必ず通る道）。
    parts = [(p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p)
             for p in parts]
    if not parts:
        return []
    has_ph = any(("{exe}" in p or "{file}" in p or "{port}" in p) for p in parts)
    out: List[str] = []
    for p in parts:
        p = (p.replace("{exe}", exe)
              .replace("{file}", file_path or "")
              .replace("{port}", str(port or "")))
        if p == "" and has_ph:
            continue                       # {file} が空だった分は落とす
        out.append(p)
    if not has_ph:
        out.append(exe)
    # .bat / .cmd は CreateProcess が直接は扱えないことがあるので cmd 経由で。
    # /c は «実行して終わる»（/k と違って窓が残らない）。
    if out and os.path.splitext(out[0])[1].lower() in (".bat", ".cmd"):
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        out = [comspec, "/c"] + out
    return out


def launch_maya(installation: MayaInstallation,
                file_path: Optional[str] = None,
                extra_args: Optional[List[str]] = None,
                command_port: Optional[int] = None,
                clean_env: bool = True,
                env_extra: Optional[Dict[str, str]] = None,
                wrapper: str = "") -> subprocess.Popen:
    """
    Launch Maya as a detached subprocess.

    command_port: 指定すると起動後にその Python commandPort を開く
                  （FileManager から socket 接続して操作を送るため）。
    clean_env:    mayapy 由来の衝突環境変数を除去してクリーンに起動する（既定 True）。
    env_extra:    起動時に足す環境変数（r146）。MAYA_UI_LANGUAGE のように
                  **Maya が起動時に読む** ものは引数では渡せないのでこちら。
    wrapper:      maya.exe の代わりに走らせる .bat / .exe（r146）。
                  {exe} {file} {port} を書けばその位置へ差し込む。
                  書かなければ «ラッパー maya.exe» の順で繋ぐ。
    Returns the Popen object (already detached).
    """
    if not installation.is_available:
        raise FileNotFoundError(f"Maya executable not found: {installation.path}")

    exe = str(installation.executable)
    if wrapper:
        cmd: List[str] = _expand_wrapper(wrapper, exe, file_path or "",
                                         command_port)
        wrapper_took_file = "{file}" in wrapper
    else:
        cmd = [exe]
        wrapper_took_file = False
    if command_port:
        # 起動後に Python commandPort を開く（既に開いていればエラーを握りつぶす）
        # 注意: -echoOutput は付けない。付けるとスクリプトエディタの全出力が
        # ソケットへ垂れ流され、応答にMELダンプ等のゴミが混入する（実機で発生）。
        mel = ('catchQuiet(`commandPort -name ":%d" -sourceType "python"`);'
               % int(command_port))
        cmd += ["-command", mel]
    if file_path and not wrapper_took_file:
        cmd += [file_path]
    if extra_args:
        cmd += extra_args

    kwargs: Dict = {"close_fds": True}
    env = _clean_launch_env() if clean_env else dict(os.environ)
    if env_extra:
        env.update({str(k): str(v) for k, v in env_extra.items()})
    if clean_env or env_extra:
        kwargs["env"] = env
    system = platform.system()
    if system == "Windows":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    return subprocess.Popen(cmd, **kwargs)


# ---------------------------------------------------------------------------
# Runtime context detection
# ---------------------------------------------------------------------------

def is_running_inside_maya() -> bool:
    """True only when Maya commands are actually available.

    maya.cmds が import できるだけでは不十分。生の mayapy はモジュールだけ
    存在し、maya.standalone.initialize() 前は file/about 等のコマンドが無い。
    その状態を「Maya内」と誤判定すると、ファイル選択時などに cmds.file を呼んで
    'module maya.cmds has no attribute file' エラーになる。
    実コマンドの有無で判定する（生mayapy・EXEでは False になる）。
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        return False
    return hasattr(cmds, "about") and hasattr(cmds, "file")


def get_current_maya_version() -> Optional[str]:
    """Return Maya version when running inside Maya, else None."""
    if not is_running_inside_maya():
        return None
    try:
        import maya.cmds as cmds
        ver = cmds.about(version=True)
        return str(ver).split(".")[0]
    except Exception:
        return None
