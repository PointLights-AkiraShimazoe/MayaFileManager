"""
File Operations
===============
All file I/O helpers used by the browser panel.
Operations run synchronously here; callers should use QThread for UI.

FBX import/export wrappers exist for both standalone (FBX SDK via fbx module)
and inside-Maya (maya.cmds.file) contexts.
"""

import os
import re
import shutil
import subprocess
import platform
from pathlib import Path
from typing import Callable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Basic operations
# ---------------------------------------------------------------------------

class FileOperationError(Exception):
    pass


def copy_items(src_paths: List[str], dst_dir: str,
               progress_cb: Optional[Callable[[int, int], None]] = None) -> List[str]:
    """
    Copy files/directories to dst_dir.
    Returns list of destination paths.
    """
    dst = Path(dst_dir)
    if not dst.exists():
        raise FileOperationError(f"Destination does not exist: {dst_dir}")

    results = []
    total = len(src_paths)
    for i, src_str in enumerate(src_paths, 1):
        src = Path(src_str)
        dest = _unique_dest(dst / src.name)
        try:
            if src.is_dir():
                shutil.copytree(str(src), str(dest))
            else:
                shutil.copy2(str(src), str(dest))
            results.append(str(dest))
        except Exception as e:
            raise FileOperationError(f"Cannot copy {src}: {e}") from e
        if progress_cb:
            progress_cb(i, total)

    return results


def move_items(src_paths: List[str], dst_dir: str,
               progress_cb: Optional[Callable[[int, int], None]] = None) -> List[str]:
    """Move files/directories to dst_dir. Returns destination paths."""
    dst = Path(dst_dir)
    if not dst.exists():
        raise FileOperationError(f"Destination does not exist: {dst_dir}")

    results = []
    total = len(src_paths)
    for i, src_str in enumerate(src_paths, 1):
        src = Path(src_str)
        dest = _unique_dest(dst / src.name)
        try:
            shutil.move(str(src), str(dest))
            results.append(str(dest))
        except Exception as e:
            raise FileOperationError(f"Cannot move {src}: {e}") from e
        if progress_cb:
            progress_cb(i, total)

    return results


def delete_items(paths: List[str], use_trash: bool = True) -> List[str]:
    """
    Delete files/directories.
    When use_trash=True, attempt to send to OS trash (requires 'send2trash').
    Returns list of paths that were NOT deleted (errors).
    """
    failed = []
    for path_str in paths:
        p = Path(path_str)
        if not p.exists():
            continue
        try:
            if use_trash:
                try:
                    import send2trash
                    send2trash.send2trash(str(p))
                    continue
                except ImportError:
                    pass  # Fall through to permanent delete
            if p.is_dir():
                shutil.rmtree(str(p))
            else:
                p.unlink()
        except Exception as e:
            print(f"[FileOps] Cannot delete {p}: {e}")
            failed.append(path_str)
    return failed


# ---------------------------------------------------------------------------
# Batch rename
# ---------------------------------------------------------------------------

class RenameRule:
    """
    Describes a batch rename operation.

    Modes
    -----
    "replace"    : find → replace (supports regex)
    "prefix"     : prepend prefix to name (before extension)
    "suffix"     : append suffix to name (before extension)
    "sequence"   : replace counter token {n} with zero-padded sequence
    "regex"      : full regex pattern → replacement (with group references)
    """

    def __init__(self, mode: str = "replace", **kwargs):
        self.mode = mode
        self.params = kwargs

    def apply(self, name: str, index: int = 0) -> str:
        stem = Path(name).stem
        ext = Path(name).suffix

        if self.mode == "replace":
            find = self.params.get("find", "")
            repl = self.params.get("replace", "")
            new_stem = stem.replace(find, repl)

        elif self.mode == "prefix":
            new_stem = self.params.get("prefix", "") + stem

        elif self.mode == "suffix":
            new_stem = stem + self.params.get("suffix", "")

        elif self.mode == "sequence":
            pad = self.params.get("pad", 3)
            start = self.params.get("start", 1)
            token = self.params.get("token", "{n}")
            new_stem = stem.replace(token, str(start + index).zfill(pad))

        elif self.mode == "regex":
            pattern = self.params.get("pattern", "")
            replacement = self.params.get("replacement", "")
            new_stem = re.sub(pattern, replacement, stem)

        else:
            new_stem = stem

        return new_stem + ext


def batch_rename(paths: List[str], rule: RenameRule,
                 dry_run: bool = False) -> List[Tuple[str, str, Optional[str]]]:
    """
    Apply RenameRule to a list of file/dir paths.
    Returns list of (old_path, new_path, error_str_or_None).
    When dry_run=True, computes new names but doesn't rename.
    """
    results = []
    for i, old_path_str in enumerate(paths):
        old_path = Path(old_path_str)
        new_name = rule.apply(old_path.name, index=i)
        new_path = old_path.parent / new_name

        if dry_run:
            results.append((old_path_str, str(new_path), None))
            continue

        try:
            if new_path.exists() and new_path != old_path:
                raise FileOperationError(f"Target already exists: {new_path}")
            old_path.rename(new_path)
            results.append((old_path_str, str(new_path), None))
        except Exception as e:
            results.append((old_path_str, str(new_path), str(e)))

    return results


# ---------------------------------------------------------------------------
# Auto-naming
# ---------------------------------------------------------------------------

def apply_auto_name(directory: str, rules: dict) -> str:
    """
    Given a directory and an auto-naming rule set, generate the next
    filename according to the matching rule.

    Rule schema example:
    {
        "/projects/CHR": {
            "template": "CHR_{seq:04d}_{desc}",
            "seq_start": 1,
            "counter_file": ".seq_counter"
        }
    }
    Returns empty string if no rule matches.
    """
    directory = os.path.normpath(directory)
    for rule_dir, rule in rules.items():
        rule_dir_norm = os.path.normpath(rule_dir)
        if directory.startswith(rule_dir_norm):
            return _expand_auto_name_template(directory, rule)
    return ""


def _expand_auto_name_template(directory: str, rule: dict) -> str:
    template = rule.get("template", "{seq:04d}")
    counter_file = os.path.join(directory, rule.get("counter_file", ".seq_counter"))

    seq = rule.get("seq_start", 1)
    if os.path.exists(counter_file):
        try:
            with open(counter_file) as f:
                seq = int(f.read().strip())
        except Exception:
            pass

    return template.replace("{seq}", str(seq)).replace(f"{{seq:{rule.get('pad','04d')}}}", str(seq).zfill(int(rule.get('pad', '04d').replace('0', '').replace('d', ''))))


# ---------------------------------------------------------------------------
# Open with associated application
# ---------------------------------------------------------------------------

def open_with_default_app(path: str):
    """Open file/folder with the OS default application."""
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(path)
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        raise FileOperationError(f"Cannot open {path}: {e}") from e


def reveal_in_explorer(path: str):
    """Show the file in the platform file manager."""
    system = platform.system()
    p = Path(path)
    if not p.exists():
        return
    try:
        if system == "Windows":
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif system == "Darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
    except Exception as e:
        raise FileOperationError(f"Cannot reveal {path}: {e}") from e


# ---------------------------------------------------------------------------
# FBX helpers
# ---------------------------------------------------------------------------

def fbx_import_maya(file_path: str, **kwargs):
    """Import FBX inside Maya using maya.cmds."""
    try:
        import maya.cmds as cmds
        cmds.loadPlugin("fbxmaya", quiet=True)
        cmds.file(file_path, i=True, type="FBX", ignoreVersion=True,
                  mergeNamespacesOnClash=False, **kwargs)
    except ImportError:
        raise FileOperationError("FBX import requires an active Maya session.")


def fbx_export_maya(file_path: str, selection_only: bool = True, **kwargs):
    """Export FBX inside Maya using maya.cmds."""
    try:
        import maya.cmds as cmds
        cmds.loadPlugin("fbxmaya", quiet=True)
        if selection_only:
            cmds.file(file_path, force=True, type="FBX export",
                      exportSelected=True, **kwargs)
        else:
            cmds.file(file_path, force=True, type="FBX export",
                      exportAll=True, **kwargs)
    except ImportError:
        raise FileOperationError("FBX export requires an active Maya session.")


# ---------------------------------------------------------------------------
# Thumbnail helpers
# ---------------------------------------------------------------------------

THUMBNAIL_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff",
                        ".bmp", ".gif", ".webp", ".exr", ".hdr"}

MAYA_EXTENSIONS = {".ma", ".mb"}
FBX_EXTENSIONS = {".fbx"}
SCENE_EXTENSIONS = MAYA_EXTENSIONS | FBX_EXTENSIONS | {".obj", ".abc", ".usd", ".usda", ".usdc"}


def get_file_type_category(path: str) -> str:
    """Return a broad category string for the given file path."""
    ext = Path(path).suffix.lower()
    if ext in MAYA_EXTENSIONS:
        return "maya"
    if ext in FBX_EXTENSIONS:
        return "fbx"
    if ext in SCENE_EXTENSIONS:
        return "3d"
    if ext in THUMBNAIL_EXTENSIONS:
        return "image"
    if ext in {".py", ".mel"}:
        return "script"
    if ext in {".txt", ".md", ".json", ".xml", ".yaml"}:
        return "text"
    if ext in {".zip", ".rar", ".7z", ".tar", ".gz"}:
        return "archive"
    return "generic"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _unique_dest(path: Path) -> Path:
    """If path already exists, append _1, _2 ... until unique."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def list_drives() -> List[str]:
    """Return available drive roots (Windows: C:\\, D:\\; Unix: /)."""
    system = platform.system()
    if system == "Windows":
        import string
        return [f"{d}:\\" for d in string.ascii_uppercase
                if Path(f"{d}:\\").exists()]
    else:
        return ["/"]


def get_directory_size(path: str) -> int:
    """Return total size in bytes (walks recursively). Slow for large trees."""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
