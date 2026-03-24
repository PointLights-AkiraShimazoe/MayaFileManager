"""
Maya version detection.

- Scans OS-standard install paths for installed Maya versions.
- Detects the required Maya version from .ma / .mb file headers.
- Provides launch helpers (standalone and socket-based Maya command execution).
"""

import os
import re
import sys
import struct
import subprocess
import platform
from pathlib import Path
from typing import Optional, List, Dict, Tuple

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


def find_installed_maya_versions(min_version: int = 2023) -> List[MayaInstallation]:
    """
    Scan standard install paths and return sorted list of MayaInstallation
    objects for Maya >= min_version.
    """
    found: Dict[str, MayaInstallation] = {}
    version_pattern = re.compile(r"Maya(\d{4}(?:\.\d+)?)")

    for root in get_maya_install_roots():
        if not root.exists():
            continue
        try:
            for entry in root.iterdir():
                m = version_pattern.match(entry.name)
                if m:
                    ver_str = m.group(1)
                    try:
                        ver_int = int(ver_str.split(".")[0])
                    except ValueError:
                        continue
                    if ver_int >= min_version and ver_str not in found:
                        inst = MayaInstallation(ver_str, entry)
                        if inst.is_available:
                            found[ver_str] = inst
        except PermissionError:
            continue

    return sorted(found.values(), key=lambda x: x.version_int)


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

def launch_maya(installation: MayaInstallation,
                file_path: Optional[str] = None,
                extra_args: Optional[List[str]] = None) -> subprocess.Popen:
    """
    Launch Maya as a detached subprocess.
    Returns the Popen object (already detached).
    """
    if not installation.is_available:
        raise FileNotFoundError(f"Maya executable not found: {installation.path}")

    cmd: List[str] = [str(installation.executable)]
    if file_path:
        cmd += [file_path]
    if extra_args:
        cmd += extra_args

    kwargs: Dict = {"close_fds": True}
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
    """True when this code is executed inside a Maya Python session."""
    try:
        import maya.cmds  # noqa: F401
        return True
    except ImportError:
        return False


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
