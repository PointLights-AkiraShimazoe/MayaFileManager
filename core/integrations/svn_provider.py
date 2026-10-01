# -*- coding: utf-8 -*-
"""Subversion（TortoiseSVN / svn CLI）プロバイダ。

- 検出: TortoiseProc.exe（TortoiseSVN）、svn CLI（TortoiseSVN の
  「command line client tools」または SlikSVN 等）
- ワークスペース: 親方向に .svn
- 状態: `svn status -v --xml`（svn CLI がある時のみ。無ければ操作のみ提供）
- 操作: TortoiseProc /command:commit|update|log|diff|revert|add
"""

import os
import xml.etree.ElementTree as ET

from .base import (Provider, ST_CLEAN, ST_MODIFIED, ST_ADDED, ST_DELETED,
                   ST_UNTRACKED, ST_CONFLICT, ST_IGNORED, ST_LOCKED, norm,
                   which, first_existing, program_files_candidates, run,
                   spawn, walk_up_for)


class SvnProvider(Provider):
    key = "svn"
    label = "Subversion"

    def __init__(self):
        super().__init__()
        self.svn = None
        self.tortoise = None
        self._status_cache = {}   # norm(dir) -> {norm(path): state}

    def detect(self):
        try:
            self.tortoise = first_existing(
                *program_files_candidates(r"TortoiseSVN\bin\TortoiseProc.exe"))
            self.svn = which("svn")
            self.available = bool(self.tortoise or self.svn)
            self.info = {"svn": self.svn, "tortoise": self.tortoise}
        except Exception:
            self.available = False

    def find_root(self, directory: str):
        return walk_up_for(directory, [".svn"])

    def fetch_status(self, root: str, directory: str):
        if not self.svn:
            return {}
        rc, out, err = run([self.svn, "status", "-v", "--xml", "--depth",
                            "immediates", directory], timeout=20.0)
        if rc != 0:
            self.note_failure(err)
            return {}
        self.note_success()
        states = {}
        try:
            tree = ET.fromstring(out)
        except ET.ParseError:
            return {}
        for entry in tree.iter("entry"):
            p = norm(entry.get("path", ""))
            ws = entry.find("wc-status")
            if ws is None:
                continue
            item = ws.get("item", "")
            if ws.find("lock") is not None and item == "normal":
                st = ST_LOCKED
            elif item in ("modified", "replaced"):
                st = ST_MODIFIED
            elif item == "added":
                st = ST_ADDED
            elif item in ("deleted", "missing"):
                st = ST_DELETED
            elif item == "unversioned":
                st = ST_UNTRACKED
            elif item == "conflicted":
                st = ST_CONFLICT
            elif item == "ignored":
                st = ST_IGNORED
            elif item == "normal":
                st = ST_CLEAN
            else:
                continue
            states[p] = st
        return states

    def actions(self, root: str, paths):
        from core.i18n import tr
        if not self.tortoise:
            return []
        paths = list(paths)

        def _tp(command, ps):
            args = [self.tortoise, "/command:" + command,
                    "/path:" + "*".join(ps), "/closeonend:0"]
            return lambda: spawn(args)

        return [
            (tr("コミット...", "Commit..."), _tp("commit", paths)),
            (tr("更新（Update）", "Update"), _tp("update", paths)),
            (tr("ログ", "Log"), _tp("log", paths[:1])),
            (tr("差分", "Diff"), _tp("diff", paths[:1])),
            (tr("追加", "Add"), _tp("add", paths)),
            (tr("変更を元に戻す...", "Revert..."), _tp("revert", paths)),
        ]
