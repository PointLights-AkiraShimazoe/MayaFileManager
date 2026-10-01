# -*- coding: utf-8 -*-
"""
ファイル操作の Undo / Redo（r62、Explorer 相当）
=================================================

対象: 名前変更 / 移動（切り取り→貼り付け、移動ダイアログ）/ コピー（貼り付け、
コピーダイアログ）/ 削除。

削除の可逆化
------------
OS のごみ箱からの復元は Windows シェル COM が必要で mayapy/EXE の両方で確実に
動かせないため、削除は «同一ボリューム内の作業ごみ箱» `<volume>\\.mfm_trash\\<id>\\`
へ移動（=高速な rename）して記録し、Undo はそこから元の場所へ戻す。
記録がスタックから消える時（上限超過・Redo 分岐の破棄・アプリ終了）に初めて
OS のごみ箱へ送る（recycle()）。作業ごみ箱へ移せない場合（ルートが書けない等）は
直接ごみ箱送りにし、その削除は Undo 不可として扱う。

スレッド: 全て UI スレッドから同期呼び出し（既存の file_operations と同じ方針）。
"""

import os
import shutil
import uuid
from typing import List, Optional

TRASH_DIRNAME = ".mfm_trash"
MAX_ENTRIES = 50


# ---------------------------------------------------------------------------
# ごみ箱送り（OS）
# ---------------------------------------------------------------------------

def recycle(paths: List[str]) -> List[str]:
    """OS のごみ箱へ送る。戻り値: 失敗したパス。
    Windows: SHFileOperationW(FO_DELETE, FOF_ALLOWUNDO)。他: send2trash があれば
    それ、無ければ恒久削除（最後の手段）。"""
    paths = [p for p in paths if p and os.path.lexists(p)]
    if not paths:
        return []
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR),
                ]
            FO_DELETE = 3
            FOF_SILENT = 0x0004
            FOF_NOCONFIRMATION = 0x0010
            FOF_ALLOWUNDO = 0x0040
            FOF_NOERRORUI = 0x0400
            op = SHFILEOPSTRUCTW()
            op.wFunc = FO_DELETE
            # 二重 NUL 終端のリスト
            op.pFrom = "\0".join(os.path.normpath(p) for p in paths) + "\0\0"
            op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
            rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
            if rc == 0 and not op.fAnyOperationsAborted:
                return []
            return [p for p in paths if os.path.lexists(p)]
        except Exception:
            pass
    try:
        import send2trash
        failed = []
        for p in paths:
            try:
                send2trash.send2trash(p)
            except Exception:
                failed.append(p)
        return failed
    except ImportError:
        pass
    failed = []
    for p in paths:
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.unlink(p)
        except OSError:
            failed.append(p)
    return failed


def _volume_root(path: str) -> str:
    drive, _ = os.path.splitdrive(os.path.abspath(path))
    if drive:
        return drive + os.sep
    return os.sep


def _clear_readonly(path: str):
    import stat
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 操作
# ---------------------------------------------------------------------------

class UndoError(Exception):
    pass


class _Op:
    label = ""

    def undo(self):
        raise NotImplementedError

    def redo(self):
        raise NotImplementedError

    def dispose(self):
        """スタックから消える時の後始末（削除の作業ごみ箱 → OS ごみ箱）。"""
        pass


def _safe_rename(src: str, dst: str):
    if not os.path.lexists(src):
        if os.path.lexists(dst):
            return          # 既に目的の状態（部分失敗からの再実行を許す）
        raise UndoError("元のファイルが見つかりません: %s" % src)
    if os.path.lexists(dst):
        raise UndoError("移動先に同名の項目が既にあります: %s" % dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)


class RenameOp(_Op):
    def __init__(self, old_path: str, new_path: str):
        self.old = old_path
        self.new = new_path
        self.label = "名前変更: %s → %s" % (os.path.basename(old_path), os.path.basename(new_path))

    def undo(self):
        _safe_rename(self.new, self.old)

    def redo(self):
        _safe_rename(self.old, self.new)


class MoveOp(_Op):
    """pairs: [(元パス, 移動後パス), ...]"""

    def __init__(self, pairs):
        self.pairs = list(pairs)
        self.label = "移動: %d 件" % len(self.pairs)

    def undo(self):
        errs = []
        for src, dst in reversed(self.pairs):
            try:
                _safe_rename(dst, src)
            except Exception as e:
                errs.append(str(e))
        if errs:
            raise UndoError("\n".join(errs))

    def redo(self):
        errs = []
        for src, dst in self.pairs:
            try:
                _safe_rename(src, dst)
            except Exception as e:
                errs.append(str(e))
        if errs:
            raise UndoError("\n".join(errs))


class CopyOp(_Op):
    """pairs: [(コピー元, コピー先), ...]。Undo はコピー先を削除（恒久。コピー元が
    残っているため）。Redo は同じ先へ再コピー。"""

    def __init__(self, pairs):
        self.pairs = list(pairs)
        self.label = "コピー: %d 件" % len(self.pairs)

    def undo(self):
        errs = []
        for _src, dst in self.pairs:
            try:
                if os.path.isdir(dst) and not os.path.islink(dst):
                    shutil.rmtree(dst, onerror=lambda f, p, e: (_clear_readonly(p), f(p)))
                elif os.path.lexists(dst):
                    _clear_readonly(dst)
                    os.unlink(dst)
            except Exception as e:
                errs.append("%s: %s" % (dst, e))
        if errs:
            raise UndoError("\n".join(errs))

    def redo(self):
        errs = []
        for src, dst in self.pairs:
            try:
                if os.path.lexists(dst):
                    raise UndoError("既に存在します: %s" % dst)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            except Exception as e:
                errs.append(str(e))
        if errs:
            raise UndoError("\n".join(errs))


class DeleteOp(_Op):
    """pairs: [(元パス, 作業ごみ箱内パス), ...]、trash_dir: 作業ごみ箱の個別フォルダ"""

    def __init__(self, pairs, trash_dir: str):
        self.pairs = list(pairs)
        self.trash_dir = trash_dir
        self.label = "削除: %d 件" % len(self.pairs)

    def undo(self):
        errs = []
        for orig, trashed in self.pairs:
            try:
                _safe_rename(trashed, orig)
            except Exception as e:
                errs.append(str(e))
        if errs:
            raise UndoError("\n".join(errs))

    def redo(self):
        errs = []
        for orig, trashed in self.pairs:
            try:
                _safe_rename(orig, trashed)
            except Exception as e:
                errs.append(str(e))
        if errs:
            raise UndoError("\n".join(errs))

    def dispose(self):
        # 作業ごみ箱の個別フォルダごと OS のごみ箱へ（中身が無ければ消すだけ）
        try:
            if os.path.isdir(self.trash_dir):
                if os.listdir(self.trash_dir):
                    recycle([self.trash_dir])
                else:
                    os.rmdir(self.trash_dir)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 削除の実行（作業ごみ箱へ移動）
# ---------------------------------------------------------------------------

def _same_device(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def trash_root_for(path: str) -> Optional[str]:
    """path と同じボリューム上の作業ごみ箱ルート（作成を試みる）。不可なら None。
    候補: <ボリューム>/.mfm_trash → 一時フォルダ/.mfm_trash（同一デバイスの時のみ、
    ルートが書けない環境向け）。"""
    import tempfile
    src_dir = os.path.dirname(os.path.abspath(path)) or os.path.abspath(path)
    cands = [os.path.join(_volume_root(path), TRASH_DIRNAME),
             os.path.join(tempfile.gettempdir(), TRASH_DIRNAME)]
    for root in cands:
        try:
            os.makedirs(root, exist_ok=True)
        except OSError:
            continue
        if not _same_device(root, src_dir):
            continue
        if os.name == "nt":
            try:
                import ctypes
                FILE_ATTRIBUTE_HIDDEN = 0x02
                ctypes.windll.kernel32.SetFileAttributesW(root, FILE_ATTRIBUTE_HIDDEN)
            except Exception:
                pass
        return root
    return None


def delete_to_work_trash(paths: List[str], progress_cb=None, pairs: Optional[list] = None):
    """削除対象を作業ごみ箱へ移動する。
    戻り値: (DeleteOp or None, 失敗リスト["path — 理由"], 直接ごみ箱送りにしたパス)
    progress_cb(i, n): 各項目後に呼ぶ（例外で中断可）。pairs を渡すと部分完了分を
    そこへ積む（キャンセル時に Undo 記録を作るため）。"""
    paths = [p for p in paths if p and os.path.lexists(p)]
    if not paths:
        return None, [], []
    pairs = pairs if pairs is not None else []
    failed, direct = [], []
    trash_dir = None
    total = len(paths)
    if progress_cb:
        progress_cb(0, total)
    for i, p in enumerate(paths, 1):
        root = trash_root_for(p)
        moved = False
        if root:
            if trash_dir is None or not trash_dir.startswith(root):
                trash_dir = os.path.join(root, uuid.uuid4().hex[:12])
                try:
                    os.makedirs(trash_dir, exist_ok=True)
                except OSError:
                    trash_dir = None
            if trash_dir:
                dst = os.path.join(trash_dir, os.path.basename(p.rstrip("/\\")))
                n = 1
                while os.path.lexists(dst):
                    n += 1
                    dst = os.path.join(trash_dir, "%s (%d)" % (os.path.basename(p.rstrip("/\\")), n))
                try:
                    _clear_readonly(p)
                    os.rename(p, dst)          # 同一ボリューム: 高速・アトミック
                    pairs.append((p, dst))
                    moved = True
                except OSError:
                    moved = False
        if not moved:
            # 作業ごみ箱へ移せない（別ボリューム扱い/権限/使用中）→ 直接ごみ箱へ
            bad = recycle([p])
            if bad:
                failed.append("%s — 削除できません（使用中または権限なし）" % p)
            else:
                direct.append(p)
        if progress_cb:
            progress_cb(i, total)
    op = DeleteOp(pairs, trash_dir) if pairs else None
    return op, failed, direct


def make_delete_op(pairs):
    """キャンセルで中断した delete_to_work_trash の部分完了分から DeleteOp を作る。"""
    if not pairs:
        return None
    return DeleteOp(list(pairs), os.path.dirname(pairs[0][1]))


# ---------------------------------------------------------------------------
# スタック
# ---------------------------------------------------------------------------

class UndoStack:
    def __init__(self, limit: int = MAX_ENTRIES):
        self._undo: List[_Op] = []
        self._redo: List[_Op] = []
        self._limit = limit
        self._listeners = []

    def add_listener(self, fn):
        self._listeners.append(fn)

    def _notify(self):
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    def push(self, op: _Op):
        if op is None:
            return
        for o in self._redo:
            o.dispose()
        self._redo.clear()
        self._undo.append(op)
        while len(self._undo) > self._limit:
            self._undo.pop(0).dispose()
        self._notify()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def undo(self) -> str:
        """1手戻す。戻り値: 操作ラベル。失敗は UndoError。"""
        if not self._undo:
            return ""
        op = self._undo[-1]
        op.undo()
        self._undo.pop()
        self._redo.append(op)
        self._notify()
        return op.label

    def redo(self) -> str:
        if not self._redo:
            return ""
        op = self._redo[-1]
        op.redo()
        self._redo.pop()
        self._undo.append(op)
        self._notify()
        return op.label

    def dispose_all(self):
        """アプリ終了時: 作業ごみ箱の中身を OS のごみ箱へ。"""
        for o in self._undo + self._redo:
            o.dispose()
        self._undo.clear()
        self._redo.clear()


_stack = None


def get_undo_stack() -> UndoStack:
    global _stack
    if _stack is None:
        _stack = UndoStack()
    return _stack
