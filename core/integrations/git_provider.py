# -*- coding: utf-8 -*-
"""Git（GitHub / TortoiseGit / git CLI）プロバイダ。

- 検出: git CLI（PATH）、TortoiseGitProc.exe、GitHub Desktop
- ワークスペース: 親方向に .git（ディレクトリ または worktree の .git ファイル）
- 状態: `git status --porcelain=v1 -z --untracked-files=all` ＋ `git ls-files -z`
        （追跡済み＝clean の判定用）。ルート単位で取得しキャッシュ。
- 操作: TortoiseGit があればそのダイアログ、無ければ git CLI（簡易）。
        GitHub Desktop があれば「GitHub Desktop で開く」。
"""

import os

from .base import (Provider, ST_CLEAN, ST_MODIFIED, ST_ADDED, ST_DELETED,
                   ST_UNTRACKED, ST_CONFLICT, ST_IGNORED, norm, which,
                   first_existing, program_files_candidates, run, spawn,
                   walk_up_for)


class GitProvider(Provider):
    key = "git"
    label = "Git"

    def __init__(self):
        super().__init__()
        self.git = None
        self.tortoise = None
        self.gh_desktop = None
        self._status_cache = {}    # norm(root) -> (ts, {norm(path): state})
        self._tracked_cache = {}   # norm(root) -> set(norm(path))

    def detect(self):
        try:
            self.git = which("git")
            self.tortoise = first_existing(
                *program_files_candidates(r"TortoiseGit\bin\TortoiseGitProc.exe"))
            self.gh_desktop = first_existing(
                *program_files_candidates(r"GitHubDesktop\GitHubDesktop.exe"))
            # git CLI が無ければ状態取得ができないため無効（Tortoise単独では
            # 状態が取れない。操作だけ提供する価値は薄い）
            self.available = bool(self.git)
            self.info = {"git": self.git, "tortoise": self.tortoise,
                         "github_desktop": self.gh_desktop}
        except Exception:
            self.available = False

    def find_root(self, directory: str):
        return walk_up_for(directory, [".git"])

    # --- 状態 -------------------------------------------------------------
    def _refresh_root(self, root: str):
        rc, out, err = run([self.git, "-C", root, "status", "--porcelain=v1",
                            "-z", "--untracked-files=all", "--ignored=no"],
                           timeout=15.0)
        if rc != 0:
            self.note_failure(err)
            return None
        states = {}
        entries = out.split("\0")
        i = 0
        while i < len(entries):
            e = entries[i]
            i += 1
            if len(e) < 4:
                continue
            xy, rel = e[:2], e[3:]
            if xy[0] == "R" or xy[0] == "C":
                # リネーム/コピーは次のエントリが元パス
                i += 1
            path = norm(os.path.join(root, rel.replace("/", os.sep)))
            if "U" in xy or xy in ("AA", "DD"):
                st = ST_CONFLICT
            elif xy == "??":
                st = ST_UNTRACKED
            elif xy == "!!":
                st = ST_IGNORED
            elif "D" in xy:
                st = ST_DELETED
            elif "A" in xy:
                st = ST_ADDED
            else:
                st = ST_MODIFIED
            states[path] = st
            # 変更を含むフォルダは「変更あり」扱い（親方向へ伝播）
            d = os.path.dirname(path)
            rn = norm(root)
            while d and len(d) >= len(rn) and d != rn:
                states.setdefault(d, ST_MODIFIED)
                nd = os.path.dirname(d)
                if nd == d:
                    break
                d = nd
        # 追跡済みファイル（clean 判定用）
        rc2, out2, _ = run([self.git, "-C", root, "ls-files", "-z"], timeout=20.0)
        tracked = set()
        if rc2 == 0:
            for rel in out2.split("\0"):
                if rel:
                    p = norm(os.path.join(root, rel.replace("/", os.sep)))
                    tracked.add(p)
                    d = os.path.dirname(p)
                    rn = norm(root)
                    while d and len(d) >= len(rn):
                        tracked.add(d)
                        nd = os.path.dirname(d)
                        if nd == d or d == rn:
                            break
                        d = nd
        self.note_success()
        import time
        self._status_cache[norm(root)] = (time.monotonic(), states)
        self._tracked_cache[norm(root)] = tracked
        return states

    def fetch_status(self, root: str, directory: str):
        rn = norm(root)
        cached = self._status_cache.get(rn)
        if cached is None:
            states = self._refresh_root(root)
            if states is None:
                return {}
        else:
            states = cached[1]
        tracked = self._tracked_cache.get(rn, set())
        out = {}
        dn = norm(directory)
        try:
            names = os.listdir(directory)
        except OSError:
            return {}
        for n in names:
            p = os.path.join(dn, n)
            if p in states:
                out[p] = states[p]
            elif p in tracked:
                out[p] = ST_CLEAN
        return out

    def invalidate_status(self, root=None):
        if root is None:
            self._status_cache.clear()
            self._tracked_cache.clear()
        else:
            self._status_cache.pop(norm(root), None)
            self._tracked_cache.pop(norm(root), None)

    # --- 操作 -------------------------------------------------------------
    def _tg(self, command, paths, extra=None):
        args = [self.tortoise, "/command:" + command,
                "/path:" + "*".join(paths)]
        if extra:
            args += extra
        return lambda: spawn(args)

    def _cli(self, args, root):
        """git CLI 実行（ワーカーで実行し結果はログのみ）。"""
        def _go():
            import threading

            def _run():
                run([self.git, "-C", root] + args, timeout=120.0)
                self.invalidate_status(root)
            threading.Thread(target=_run, daemon=True).start()
        return _go

    def actions(self, root: str, paths):
        from core.i18n import tr
        acts = []
        paths = list(paths)
        if self.tortoise:
            acts += [
                (tr("コミット...", "Commit..."), self._tg("commit", paths)),
                (tr("ログ", "Log"), self._tg("log", paths[:1])),
                (tr("差分", "Diff"), self._tg("diff", paths[:1])),
                (tr("追加", "Add"), self._tg("add", paths)),
                (tr("変更を元に戻す...", "Revert..."), self._tg("revert", paths)),
                (None, None),
                (tr("プル...", "Pull..."), self._tg("pull", [root])),
                (tr("プッシュ...", "Push..."), self._tg("push", [root])),
                (tr("同期...", "Sync..."), self._tg("sync", [root])),
            ]
        else:
            acts += [
                (tr("追加（git add）", "Add (git add)"),
                 self._cli(["add", "--"] + paths, root)),
                (tr("変更を元に戻す（git checkout --）",
                    "Revert (git checkout --)"),
                 self._cli(["checkout", "--"] + paths, root)),
                (None, None),
                (tr("プル（git pull）", "Pull (git pull)"), self._cli(["pull"], root)),
                (tr("プッシュ（git push）", "Push (git push)"), self._cli(["push"], root)),
            ]
        if self.gh_desktop:
            acts.append((None, None))
            acts.append((tr("GitHub Desktop で開く", "Open in GitHub Desktop"),
                         (lambda: spawn([self.gh_desktop, root]))))
        return acts
