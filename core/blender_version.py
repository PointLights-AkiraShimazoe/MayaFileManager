"""
Blender インストール検出と起動（r65）
=====================================

Maya の core/maya_version.py に対応する Blender 版。
- 標準インストール先（Program Files\\Blender Foundation\\Blender X.Y）、Steam 版、
  環境変数 MFM_BLENDER_EXE（ポータブル版の明示指定）を走査する。
- 起動時は resources/blender_bridge.py を --python で読み込み、連携用 TCP
  サーバ（Maya の commandPort 相当）を開く。
"""

import os
import re
import platform
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

_BRIDGE_SCRIPT = Path(__file__).resolve().parent.parent / "resources" / "blender_bridge.py"


class BlenderInstallation:
    def __init__(self, version: str, executable: Path):
        self.version = version            # "4.2" 等
        self.executable = executable
        self.path = executable.parent

    @property
    def is_available(self) -> bool:
        return _exe_present(self.executable)

    @property
    def version_tuple(self):
        try:
            return tuple(int(x) for x in self.version.split(".")[:2])
        except ValueError:
            return (0, 0)

    def __repr__(self):
        return f"Blender {self.version} @ {self.executable}"


def _version_from_dir(name: str) -> Optional[str]:
    m = re.match(r"Blender\s*(\d+\.\d+)", name)
    return m.group(1) if m else None


def _version_from_exe_dir(exe: Path) -> Optional[str]:
    """blender.exe の隣にある «X.Y» フォルダ（バージョン別リソース）から判定。"""
    try:
        for entry in exe.parent.iterdir():
            if entry.is_dir() and re.fullmatch(r"\d+\.\d+", entry.name):
                return entry.name
    except OSError:
        pass
    return None


def _fixed_drives() -> List[str]:
    """固定ドライブのみ（ネットワークドライブは絶対に触らない: CLAUDE.md）。"""
    out = []
    try:
        import ctypes
        import string
        k32 = ctypes.windll.kernel32
        mask = k32.GetLogicalDrives()
        for i, ch in enumerate(string.ascii_uppercase):
            if mask & (1 << i):
                root = f"{ch}:\\"
                if k32.GetDriveTypeW(root) == 3:      # DRIVE_FIXED
                    out.append(root)
    except Exception:
        out = ["C:\\"]
    return out


def _windows_registry_candidates() -> List[Path]:
    """レジストリから blender.exe を探す（r71）:
    - アンインストール情報（DisplayName が Blender*）の InstallLocation / DisplayIcon
    - .blend の関連付け（HKCR\\blendfile\\shell\\open\\command）
    - App Paths\\blender.exe
    標準外の場所（D: ドライブ等）へ入れた Blender はここでしか見つからない。"""
    out: List[Path] = []
    try:
        import winreg
    except ImportError:
        return out

    def _add_from_cmd(val: str):
        if not val:
            return
        m = re.search(r'"?([A-Za-z]:\\[^"]*?blender(?:-launcher)?\.exe)', val, re.IGNORECASE)
        if m:
            p = Path(m.group(1))
            if p.name.lower() == "blender-launcher.exe":
                p = p.with_name("blender.exe")
            out.append(p)

    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, sub in roots:
        try:
            with winreg.OpenKey(hive, sub) as k:
                n = winreg.QueryInfoKey(k)[0]
                for i in range(n):
                    try:
                        name = winreg.EnumKey(k, i)
                        with winreg.OpenKey(k, name) as sk:
                            try:
                                disp = winreg.QueryValueEx(sk, "DisplayName")[0]
                            except OSError:
                                continue
                            if not str(disp).lower().startswith("blender"):
                                continue
                            for v in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                try:
                                    val = str(winreg.QueryValueEx(sk, v)[0])
                                except OSError:
                                    continue
                                if v == "InstallLocation" and val:
                                    out.append(Path(val.strip('"')) / "blender.exe")
                                else:
                                    _add_from_cmd(val)
                                    # アンインストーラは Blender フォルダ直下にある
                                    m = re.search(r'"?([A-Za-z]:\\[^"]*?)\\[^\\"]*\.exe', val)
                                    if m:
                                        out.append(Path(m.group(1)) / "blender.exe")
                    except OSError:
                        continue
        except OSError:
            continue
    for hive, sub in ((winreg.HKEY_CLASSES_ROOT, r"blendfile\shell\open\command"),
                      (winreg.HKEY_CLASSES_ROOT, r"Applications\blender.exe\shell\open\command"),
                      (winreg.HKEY_LOCAL_MACHINE,
                       r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\blender.exe")):
        try:
            with winreg.OpenKey(hive, sub) as k:
                _add_from_cmd(str(winreg.QueryValueEx(k, "")[0]))
        except OSError:
            continue
    return out


_STORE_PKG_RE = re.compile(r"BlenderFoundation\.Blender_(\d+\.\d+)(?:\.\d+)*_", re.IGNORECASE)


def _version_from_store_path(exe: Path) -> Optional[str]:
    """Store 版: パッケージフォルダ名 «BlenderFoundation.Blender_5.2.1.0_x64__…» から版。"""
    for part in exe.parts:
        m = _STORE_PKG_RE.search(part)
        if m:
            return m.group(1)
    return None


def _windows_store_candidates(use_powershell: bool = False) -> List[Path]:
    out: List[Path] = []
    la = os.environ.get("LOCALAPPDATA", "")
    # 1) 実行エイリアス（アプリ実行エイリアスが有効な場合）
    if la:
        alias_dir = Path(la) / "Microsoft" / "WindowsApps"
        for n in ("blender.exe", "blender-launcher.exe"):
            out.append(alias_dir / n)
        try:
            for entry in alias_dir.iterdir():
                if entry.is_dir() and entry.name.lower().startswith("blenderfoundation."):
                    for n in ("blender.exe", "blender-launcher.exe"):
                        out.append(entry / n)
        except OSError:
            pass
    # 2) パッケージフォルダ（WindowsApps 直下の列挙は ACL で失敗し得るので
    #    glob と Get-AppxPackage の両方を試す）
    roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WindowsApps"]
    for drv in _fixed_drives():
        roots.append(Path(drv) / "Program Files" / "WindowsApps")
    seen = set()
    for root in roots:
        k = str(root).lower()
        if k in seen:
            continue
        seen.add(k)
        try:
            for entry in root.glob("BlenderFoundation.Blender_*"):
                out.append(entry / "Blender" / "blender.exe")
                out.append(entry / "blender.exe")
        except OSError:
            pass
    if not use_powershell:
        return out
    try:
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-AppxPackage -Name BlenderFoundation.Blender* | Select-Object -ExpandProperty InstallLocation)"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in (cp.stdout or "").splitlines():
            line = line.strip()
            if line:
                out.append(Path(line) / "Blender" / "blender.exe")
                out.append(Path(line) / "blender.exe")
    except Exception:
        pass
    return out


def _exe_present(exe) -> bool:
    """実在判定。Store 版（WindowsApps）は ACL で stat が拒否されることがあるため、
    PermissionError は «存在する» とみなす（起動自体は可能）。"""
    try:
        return Path(exe).is_file()
    except PermissionError:
        return "windowsapps" in str(exe).lower()
    except OSError:
        return False


def find_installed_blender_versions(extra_paths=None) -> List[BlenderInstallation]:
    """extra_paths: 設定「blender_exe」等、ユーザーが明示した実行ファイル。"""
    found: Dict[str, BlenderInstallation] = {}
    system = platform.system()
    cands: List[Path] = []

    for p in list(extra_paths or []):
        if p:
            cands.append(Path(str(p)))
    env_exe = os.environ.get("MFM_BLENDER_EXE")
    if env_exe:
        cands.append(Path(env_exe))

    if system == "Windows":
        roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation",
                 Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "Blender Foundation"]
        # 他の固定ドライブの標準配置（D:\Program Files\Blender Foundation 等）と
        # ドライブ直下の Blender Foundation
        for drv in _fixed_drives():
            roots.append(Path(drv) / "Program Files" / "Blender Foundation")
            roots.append(Path(drv) / "Blender Foundation")
        seen = set()
        for root in roots:
            key = str(root).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                if root.exists():
                    for entry in root.iterdir():
                        # フォルダ名に版が無くても拾う（版は隣の «X.Y» フォルダから）
                        if entry.is_dir():
                            cands.append(entry / "blender.exe")
            except OSError:
                pass
        for steam in (Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
                      / "Steam" / "steamapps" / "common" / "Blender" / "blender.exe",):
            cands.append(steam)
        # Microsoft Store 版（r75）: 実体は C:\Program Files\WindowsApps\
        # BlenderFoundation.Blender_<ver>_x64__<id>\Blender\blender.exe。
        # WindowsApps 直下は ACL で列挙できないことがあるので、パッケージ名の
        # 直指定・エイリアス・Get-AppxPackage の 3 経路で探す。
        cands.extend(_windows_store_candidates())
        cands.extend(_windows_registry_candidates())
        # ここまでで 1 つも実在しなければ Get-AppxPackage（PowerShell、〜1 秒）
        if not any(_exe_present(c) for c in cands):
            cands.extend(_windows_store_candidates(use_powershell=True))
        try:
            import shutil
            w = shutil.which("blender")
            if w:
                cands.append(Path(w))
        except Exception:
            pass
    elif system == "Darwin":
        cands.append(Path("/Applications/Blender.app/Contents/MacOS/Blender"))
    else:
        for p in ("/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender"):
            cands.append(Path(p))

    seen_exe = set()
    for exe in cands:
        exe = Path(os.path.normpath(str(exe)))
        k = str(exe).lower()
        if k in seen_exe or not _exe_present(exe):
            continue
        seen_exe.add(k)
        ver = (_version_from_store_path(exe) or _version_from_dir(exe.parent.name)
               or _version_from_exe_dir(exe) or "?")
        key = ver if ver not in found else f"{ver}@{exe}"
        found[key] = BlenderInstallation(ver, exe)

    # Store 版のエイリアス（版が取れない）しか無ければ、Get-AppxPackage で
    # パッケージ名（版）と InstallLocation を引いて差し替える（r76）
    unknown = [k for k, b in found.items() if b.version == "?" and "windowsapps" in str(b.executable).lower()]
    if unknown and system == "Windows":
        for ver, exe in _resolve_store_packages():
            for k in unknown:
                found.pop(k, None)
            unknown = []
            key = ver if ver not in found else f"{ver}@{exe}"
            found[key] = BlenderInstallation(ver, exe)

    return sorted(found.values(), key=lambda b: b.version_tuple)


def _resolve_store_packages():
    """Get-AppxPackage で Blender の Store パッケージを列挙 → [(版, exe)]。
    exe は InstallLocation\\Blender\\blender.exe が実在（ACL 拒否含む）ならそれ、
    無ければ実行エイリアス %LOCALAPPDATA%\\Microsoft\\WindowsApps\\<family>\\blender-launcher.exe。"""
    out = []
    try:
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-AppxPackage -Name BlenderFoundation.Blender* | ForEach-Object { $_.PackageFullName + '|' + $_.InstallLocation + '|' + $_.PackageFamilyName }"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return out
    la = os.environ.get("LOCALAPPDATA", "")
    for line in (cp.stdout or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) < 3:
            continue
        full, loc, family = parts[0], parts[1], parts[2]
        m = _STORE_PKG_RE.search(full + "_")
        ver = m.group(1) if m else "?"
        # 起動は «実行エイリアス» 経由でないと CreateProcess が拒否される
        # （WindowsApps 直下の exe を直接起動 → WinError 5 アクセス拒否、
        # 2026-09-17 実機）。エイリアスを優先し、無い時だけパッケージ内 exe。
        cands = []
        if la:
            cands += [Path(la) / "Microsoft" / "WindowsApps" / family / "blender.exe",
                      Path(la) / "Microsoft" / "WindowsApps" / family / "blender-launcher.exe",
                      Path(la) / "Microsoft" / "WindowsApps" / "blender.exe",
                      Path(la) / "Microsoft" / "WindowsApps" / "blender-launcher.exe"]
        cands += [Path(loc) / "Blender" / "blender.exe", Path(loc) / "blender.exe"]
        for c in cands:
            if _exe_present(c):
                out.append((ver, c))
                break
    return out


def detection_report(extra_paths=None, installs=None) -> str:
    """「Blender が起動リストに出ない」調査用: 走査した場所と結果を1文字列で。
    installs を渡せば再走査しない（PowerShell を二重に起動しない）。"""
    lines = []
    for b in (installs if installs is not None else find_installed_blender_versions(extra_paths)):
        lines.append("found: %s" % b)
    if not lines:
        lines.append("found: (none)")
    lines.append("env MFM_BLENDER_EXE=%r" % os.environ.get("MFM_BLENDER_EXE"))
    if platform.system() == "Windows":
        lines.append("registry: %s" % (", ".join(str(p) for p in _windows_registry_candidates()) or "-"))
        lines.append("store: %s" % (", ".join(str(p) for p in _windows_store_candidates() if _exe_present(p)) or "-"))
        lines.append("fixed drives: %s" % ", ".join(_fixed_drives()))
    return "\n".join(lines)


def _launchable_exe(exe: Path) -> Path:
    """Store 版（C:\\Program Files\\WindowsApps\\… の exe）は直接起動できない
    （アクセス拒否）。同じパッケージの実行エイリアスがあればそちらを返す。"""
    try:
        p = str(exe)
        low = p.lower()
        if "\\windowsapps\\" not in low or "program files" not in low:
            return exe
        la = os.environ.get("LOCALAPPDATA", "")
        if not la:
            return exe
        m = re.search(r"BlenderFoundation\.Blender_[^\\]*__([A-Za-z0-9]+)", p)
        fams = []
        if m:
            fams.append("BlenderFoundation.Blender_" + m.group(1))
        base = Path(la) / "Microsoft" / "WindowsApps"
        for fam in fams:
            for n in ("blender.exe", "blender-launcher.exe"):
                c = base / fam / n
                if _exe_present(c):
                    return c
        for n in ("blender.exe", "blender-launcher.exe"):
            c = base / n
            if _exe_present(c):
                return c
    except Exception:
        pass
    return exe


def launch_blender(installation: BlenderInstallation,
                   file_path: Optional[str] = None,
                   command_port: Optional[int] = None) -> subprocess.Popen:
    """Blender を切り離しプロセスとして起動する。command_port を渡すと連携
    スクリプト（resources/blender_bridge.py）をそのポートで開く。"""
    if not installation.is_available:
        raise FileNotFoundError(f"Blender executable not found: {installation.executable}")
    exe = _launchable_exe(installation.executable)
    cmd: List[str] = [str(exe)]
    if file_path:
        cmd.append(file_path)
    if command_port and _BRIDGE_SCRIPT.exists():
        cmd += ["--python", str(_BRIDGE_SCRIPT), "--", "--mfm-port", str(int(command_port))]
    env = dict(os.environ)
    # mayapy 由来の Python 環境変数を Blender へ持ち込まない（別 Python を壊す）
    for k in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "PYTHONNOUSERSITE",
              "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM"):
        env.pop(k, None)
    kwargs: Dict = {"close_fds": True, "env": env}
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)
