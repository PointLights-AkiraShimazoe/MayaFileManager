# -*- coding: utf-8 -*-
"""Perforce（P4V / p4vc / p4 CLI）プロバイダ。

- 検出: p4 CLI（PATH）、p4vc / p4v（PATH または Program Files\\Perforce）
- ワークスペース: 親方向に P4CONFIG ファイル（環境変数 P4CONFIG の名前、
  既定候補 .p4config / p4config.txt）。無ければ `p4 -ztag info` の
  clientRoot（起動後1回、タイムアウト付き、ワーカー）で判定。
- 状態: `p4 -ztag fstat -T depotFile,clientFile,headRev,haveRev,action,
  otherOpen,headAction <dir>/*`（表示中フォルダ単位、ネットワークI/O）
- 操作: p4vc（submit/diff/history/timelapse/revert）＋ p4 CLI
  （edit=チェックアウト / add / revert / sync）
"""
from core.diag import swallow as _swallow  # r112

import os

from .base import (Provider, ST_CLEAN, ST_MODIFIED, ST_ADDED, ST_DELETED,
                   ST_UNTRACKED, ST_OUTDATED, ST_LOCKED, ST_OTHER_OPEN,
                   norm, which, is_under,
                   first_existing, program_files_candidates, run, spawn,
                   walk_up_for)


class P4Provider(Provider):
    key = "p4"
    label = "Perforce"
    MAX_FAILURES = 3   # ネットワーク系は早めに諦める

    def __init__(self):
        super().__init__()
        self.p4 = None
        self.p4vc = None
        self.p4v = None
        self._client_root = None
        self._info_tried = False
        self._conn_args = []          # 既定の接続（-p/-u/-c）
        # r122: **ワークスペースは 1 つとは限らない。**
        # [(norm(ルート), クライアント名, 元のルート表記), ...] を長い順に持ち、
        # パスからワークスペースを引く。従来は p4 info の clientRoot ただ 1 つで
        # 判定していたため、«今つないでいるワークスペース以外は状態が出ない»
        # 状態だった（ユーザー報告 2026-10-06）。
        self._clients = None          # None=未取得 / [] = 取得したが0件
        self._client_for_dir = {}     # norm(ディレクトリ) -> クライアント名 or ""
        # norm(clientFile) -> {"state", "others": [user@ws...], "locks": [user@ws...]}
        # 直近の fstat 結果（右クリック時の事前チェック用。I/Oなしで参照）
        self._file_info = {}

    def detect(self):
        try:
            self.p4 = which("p4") or first_existing(
                *program_files_candidates(r"Perforce\p4.exe"))
            self.p4vc = which("p4vc") or first_existing(
                *program_files_candidates(r"Perforce\p4vc.exe"))
            self.p4v = which("p4v") or first_existing(
                *program_files_candidates(r"Perforce\p4v.exe"))
            self.available = bool(self.p4)
            # r142: メニューのアイコンに P4V の正式アイコンを使う。
            # Maya / Blender と同じ «exe に埋め込まれたアイコン» の仕組み。
            try:
                from ui.dcc_header import set_integration_exe
                set_integration_exe("p4", self.p4v or self.p4vc or self.p4)
            except Exception as _e:
                _swallow(_e, "core/integrations/p4_provider.py detect(icon)")
            self._conn_args = []
            self._clients = None
            self._client_for_dir = {}
            self.info = {"p4": self.p4, "p4vc": self.p4vc, "p4v": self.p4v}
            # r146（ユーザー報告 2026-10-21「P4V ですが、ワークスペースが MM の
            # ものしか反応していません」）: ここで info を空に戻すのに
            # _info_tried を立てたままにしていた。detect() は BrowserPanel が
            # 作られるたび（エリアを足すたび）に走るので、2 つ目のエリアを
            # 作った瞬間から user / host が空のまま二度と読まれず、
            # `p4 clients` が -u 無し（サーバーの全クライアント 413 件、
            # Host 付き＝このユーザーの分は全部除外）になり、既定の
            # ワークスペース以外が «管理外» に戻っていた。info を捨てるなら
            # 読み直しも仕切り直す。
            self._info_tried = False
            self._client_root = None
            try:
                self.invalidate_roots()        # 古い «管理外» の答えも捨てる
            except Exception as _e:
                _swallow(_e, "core/integrations/p4_provider.py detect(invalidate_roots)")
        except Exception:
            self.available = False

    def _p4v_recent_connection(self):
        """P4V が保存している最近の接続（port/user/client）を読む。
        p4 CLI 側に P4PORT 等が無くても Explorer の Helix Core と同じ接続で
        info/fstat を実行できるようにする。"""
        import xml.etree.ElementTree as ET
        cand = [os.path.join(os.path.expanduser("~"), ".p4qt",
                             "ApplicationSettings.xml")]
        for path in cand:
            try:
                tree = ET.parse(path)
            except Exception:
                continue
            # <PropertyList varName="RecentConnections"> の先頭要素
            for pl in tree.iter("PropertyList"):
                if pl.get("varName") != "RecentConnections":
                    continue
                for s in pl.iter("String"):
                    txt = (s.text or "").strip()
                    # 形式: "port, user, client"
                    parts = [t.strip() for t in txt.split(",")]
                    if len(parts) >= 3 and parts[0]:
                        return parts[0], parts[1], parts[2]
        return None

    def _client_root_via_info(self):
        if self._info_tried:
            return self._client_root
        self._info_tried = True

        def _try(args):
            rc, out, err = run([self.p4] + args + ["-ztag", "info"], timeout=6.0)
            if rc != 0:
                return None
            root = None
            for line in out.splitlines():
                # r122: ワークスペース一覧を引くのに user / host が要る
                if line.startswith("... clientRoot "):
                    root = line[len("... clientRoot "):].strip()
                elif line.startswith("... userName "):
                    self.info["user"] = line[len("... userName "):].strip()
                elif line.startswith("... clientHost "):
                    self.info["host"] = line[len("... clientHost "):].strip()
            return root

        # 1) p4 の既定設定（P4PORT/P4CONFIG/レジストリ `p4 set`）
        root = _try([])
        if not root:
            # 2) P4V の最近の接続（Explorer の Helix Core と同じ設定）
            conn = self._p4v_recent_connection()
            if conn:
                port, user, client = conn
                args = ["-p", port]
                if user:
                    args += ["-u", user]
                if client:
                    args += ["-c", client]
                root = _try(args)
                if root:
                    self._conn_args = args
                    if user and not self.info.get("user"):
                        self.info["user"] = user
        self._client_root = root
        self.info["client_root"] = root
        self.info["conn_args"] = list(getattr(self, "_conn_args", []))
        return self._client_root

    # ── r122: このユーザーの «全ワークスペース» を引く ──────────────────
    def _all_clients(self):
        """[(norm(ルート), クライアント名, ルート表記), ...] を «長いルート順» で返す。

        p4 info が返すのは «今つないでいる» ワークスペースただ 1 つなので、
        それだけで判定すると他のワークスペースのフォルダが «Perforce 管理外»
        になってしまう（ユーザー報告 2026-10-06）。`p4 clients -u <user>` で
        このユーザーのワークスペースを全部取り、ホストが一致するものだけ残す。
        """
        if self._clients is not None:
            return self._clients
        self._clients = []
        if not self.p4:
            return self._clients
        self._client_root_via_info()          # 既定接続（_conn_args）を確定させる
        user = self.info.get("user") or ""
        host = (self.info.get("host") or "").lower()
        if not user:
            # r146: info が古い（detect で捨てられた等）なら読み直す。
            self._info_tried = False
            self._client_root_via_info()
            user = self.info.get("user") or ""
            host = (self.info.get("host") or "").lower()
        if not user:
            # ユーザーが分からないのに -u 無しで引くと **サーバーの全
            # クライアント** が返り（実機 413 件）、Host 付きのこのユーザーの
            # ワークスペースが全部除外されて «既定以外は管理外» になる。
            # 引かない方がまし（既定の clientRoot での判定は残る）。
            self._log("clients: userName が取れないので一覧を引かない")
            return self._clients
        args = ["-ztag", "clients", "-u", user]
        rc, out, err = run([self.p4] + self._conn_args + args, timeout=10.0)
        if rc != 0:
            self._log("clients 取得失敗 rc=%s %s"
                      % (rc, (err or out).strip().splitlines()[:1]))
            return self._clients
        cur = {}
        rows = []

        def flush():
            name, root = cur.get("client"), cur.get("Root")
            chost = (cur.get("Host") or "").lower()
            # Host 指定があり、この PC と違うワークスペースは使えない
            if name and root and (not chost or chost == host):
                rows.append((norm(root), name, root))
            cur.clear()

        for line in out.splitlines():
            if not line.startswith("... "):
                if not line.strip():
                    flush()
                continue
            body = line[4:]
            k, _sp, v = body.partition(" ")
            if k == "client" and cur.get("client"):
                flush()
            cur[k] = v.strip()
        flush()
        # 長いルートが先（入れ子のワークスペースで深い方を選ぶ）
        rows.sort(key=lambda r: len(r[0]), reverse=True)
        self._clients = rows
        self.info["clients"] = [(n, r) for _k, n, r in rows]
        # r137: **打ち切らない。** 従来は先頭 8 件だけ出していたため、
        # 実機の調査で «13 件と言いながら 8 件しか見えない» 状態になり、
        # どのワークスペースが関係しているのか判別できなかった。
        for _k, n, r in rows:
            self._log("  ws %s -> %s" % (n, r))
        self._log("ワークスペース %d 件" % len(rows))
        return self._clients

    def client_for(self, directory: str):
        """そのフォルダが属するワークスペース名。分からなければ ""。"""
        key = norm(directory)
        if key in self._client_for_dir:
            return self._client_for_dir[key]
        found = ""
        for root_key, name, _root in self._all_clients():
            if is_under(key, root_key):          # r137: ドライブ直下のルート対応
                found = name
                break
        self._client_for_dir[key] = found
        return found

    def conn_args_for(self, directory: str):
        """そのフォルダ用の接続引数。該当ワークスペースがあれば -c を差し替える。"""
        base = list(self._conn_args)
        name = self.client_for(directory) if directory else ""
        if not name:
            return base
        out = []
        skip = False
        for a in base:
            if skip:
                skip = False
                continue
            if a == "-c":
                skip = True
                continue
            out.append(a)
        return out + ["-c", name]

    def _log(self, msg):
        fn = getattr(self, "log", None)
        if callable(fn):
            try:
                fn("p4: " + msg)
            except Exception as _e:
                _swallow(_e, "core/integrations/p4_provider.py:118 _log")

    def _in_client_view(self, directory: str) -> bool:
        """`p4 where <dir>/...` でクライアントビュー内かをサーバーに直接照会する。
        クライアントルートの文字列一致（subst/ジャンクション/大文字小文字/
        別ドライブ表記で食い違う）に頼らない。"""
        pattern = os.path.join(directory, "...")
        rc, out, err = run([self.p4] + self.conn_args_for(directory)
                           + ["-ztag", "where", pattern],
                           cwd=directory, timeout=8.0)
        text = (out + err).lower()
        ok = rc == 0 and "... clientfile" in text and "not in client view" not in text \
            and "not under client" not in text
        self._log("where %r rc=%s ok=%s %s" % (directory, rc, ok,
                                               (err or out).strip().splitlines()[:1]))
        return ok

    def find_root(self, directory: str):
        names = [os.environ.get("P4CONFIG") or "", ".p4config", "p4config.txt"]
        names = [n for n in names if n]
        r = walk_up_for(directory, names)
        if r:
            self._log("root via P4CONFIG: %r" % r)
            return r
        # r122: **このユーザーの全ワークスペースから引く。**
        # 従来は p4 info の clientRoot（＝今つないでいる 1 つ）だけを見ていたため、
        # 他のワークスペースのフォルダが «Perforce 管理外» 扱いになっていた。
        d = norm(directory)
        for root_key, name, root in self._all_clients():
            if is_under(d, root_key):            # r137: ドライブ直下のルート対応
                self._log("root via clients: %r (ws=%s)" % (root, name))
                return root
        cr = self._client_root_via_info()
        self._log("info clientRoot=%r conn_args=%r" % (cr, self._conn_args))
        if cr:
            c = norm(cr)
            if is_under(d, c):                   # r137: 同上
                return cr
            # 文字列一致しない（subst/マッピング/別表記）場合はサーバーに照会
            if self._in_client_view(directory):
                return cr
        elif self._conn_args or self.p4:
            # info が取れなくても where が通るなら（P4CONFIG等）ワークスペース扱い
            if self._in_client_view(directory):
                return directory
        return None

    # r125: «サーバーに繋がらない／認証が切れた» だけを失敗に数える。
    # それ以外（ビュー外・未マップ・権限なし・ファイルが無い）は、
    # そのフォルダに P4 の情報が無いというだけで、連携の不調ではない。
    _CONN_ERRORS = (
        "perforce client error",            # 接続・認証はこの見出しで出る
        "connect to server failed",
        "tcp connect to",
        "session has expired",
        "your session was logged out",
        "password (p4passwd) invalid or unset",
        "operation took too long",
        "timed out",
    )

    @classmethod
    def _is_connection_error(cls, text: str) -> bool:
        t = (text or "").lower()
        return any(m in t for m in cls._CONN_ERRORS)

    def fetch_status(self, root: str, directory: str):
        # fstat はワイルドカード指定。表示中フォルダ直下のみ。
        # r137: «どのワークスペースで聞いたか» を残す。違う ws で聞いていたために
        # «管理外» に見えていた、をログだけで追えるようにする。
        self._log("fstat ws=%r dir=%r" % (self.client_for(directory) or "(既定)",
                                          directory))
        pattern = os.path.join(directory, "*")
        rc, out, err = run([self.p4] + self.conn_args_for(directory)
                           + ["-ztag", "fstat", "-T",
                            "clientFile,headRev,haveRev,action,otherOpen,otherLock,"
                            "ourLock,headAction",
                            pattern], cwd=directory, timeout=12.0)
        if rc != 0:
            text = (err + out)
            # r125: **rc != 0 を何でも «失敗» に数えていた。**
            # p4 fstat はクライアントビュー外・未マップ・権限なしのフォルダでも
            # rc != 0 を返す。普通に社内のフォルダを 3 つ開いただけで
            # MAX_FAILURES に達し、Perforce の連携が丸ごと止まっていた
            # （ユーザー報告 2026-10-06「連携状態が途中で切れる」）。
            # «そのフォルダに P4 の情報が無い» と «サーバーに繋がらない» は別物。
            if self._is_connection_error(text):
                self.note_failure(err or out)
                self._log("fstat 接続エラー dir=%r rc=%s: %s"
                          % (directory, rc, text.strip()[:200]))
            else:
                self._log("fstat 対象外 dir=%r rc=%s: %s"
                          % (directory, rc, text.strip()[:200]))
            return {}
        self.note_success()
        states = {}
        infos = {}
        cur = {}

        def flush():
            cf = cur.get("clientFile")
            if not cf:
                return
            p = norm(cf)
            action = cur.get("action")
            head, have = cur.get("headRev"), cur.get("haveRev")
            head_action = cur.get("headAction", "")
            others = cur.get("otherOpenUsers", [])
            locks = cur.get("otherLockUsers", [])
            # P4V 準拠: 自分の open 状態 > 他者のロック > 他者のチェックアウト
            # > デポ側の削除 > 未同期 > 最新
            if action in ("edit", "integrate", "branch", "move/add"):
                st = ST_MODIFIED
            elif action == "add":
                st = ST_ADDED
            elif action in ("delete", "move/delete"):
                st = ST_DELETED
            elif "otherLock" in cur:
                st = ST_LOCKED
            elif "otherOpen" in cur:
                st = ST_OTHER_OPEN
            elif head_action in ("delete", "move/delete"):
                st = ST_DELETED
            elif head and have and head != have:
                st = ST_OUTDATED
            elif head and not have:
                st = ST_OUTDATED
            else:
                st = ST_CLEAN
            states[p] = st
            infos[p] = {"state": st, "action": action or "",
                        "others": list(others), "locks": list(locks),
                        "locked": "otherLock" in cur}

        for line in out.splitlines():
            if not line.startswith("... "):
                continue
            body = line[4:]
            key, _, val = body.partition(" ")
            if key == "clientFile" and cur:
                flush()
                cur = {}
            if key == "otherOpen":
                cur["otherOpen"] = val              # 件数
            elif key.startswith("otherOpen"):       # otherOpen0 user@ws ...
                cur.setdefault("otherOpenUsers", []).append(val.strip())
            elif key == "otherLock":
                cur["otherLock"] = val              # フラグ（値なし）
            elif key.startswith("otherLock"):       # otherLock0 user@ws
                cur.setdefault("otherLockUsers", []).append(val.strip())
                cur.setdefault("otherLock", "")
            else:
                cur[key] = val
        if cur:
            flush()
        # デポに無いローカルファイル（未追加）
        try:
            for n in os.listdir(directory):
                p = norm(os.path.join(directory, n))
                if p not in states:
                    states[p] = ST_UNTRACKED
                    infos[p] = {"state": ST_UNTRACKED, "action": "",
                                "others": [], "locks": [], "locked": False}
        except OSError:
            pass
        with self._lock:
            self._file_info.update(infos)
        return states

    def invalidate_status(self, root: str):
        """手動更新時: 事前チェック用キャッシュも捨てる（manager.refresh から）。

        r122: ワークスペース一覧も捨てる。P4V で新しいワークスペースを
        作った直後でも、⟳ を押せば拾えるようにするため。"""
        with self._lock:
            self._file_info.clear()
            self._clients = None
            self._client_for_dir = {}

    def file_info(self, path: str):
        with self._lock:
            return self._file_info.get(norm(path))

    # --- 操作 -------------------------------------------------------------
    # p4 は失敗しても rc=0 で stdout にメッセージを出すことがある
    # （"already locked by", "can't edit exclusive file", "not on client" 等）。
    # 注意: ファイル名に含まれ得る一般語（"error" 等）は入れない（誤検知防止）。
    _ERR_MARKERS = ("already locked by", "can't edit exclusive", "can't add",
                    "not on client", "no such file(s)", "no permission",
                    "must resolve", "not in client view", "connect to server failed",
                    "p4passwd", "access for user", "not opened on this client",
                    "file(s) not on client")

    # r121: «やることが無かった» だけの行。フォルダ一括では必ず混ざる。
    # 例: 既に追加済みのファイルが含まれる / 既にチェックアウト済み。
    # これを失敗扱いにしていたため、フォルダへの操作が常に
    # 「失敗しました」で終わっていた（ユーザー報告 2026-10-05）。
    _SKIP_MARKERS = ("can't add existing file", "currently opened for add",
                     "already opened for edit", "can't edit (already opened",
                     "file(s) up-to-date", "file(s) not opened on this client",
                     "no file(s) to reconcile", "can't add (already opened")
    # 実際に何かが行われた行
    _OK_MARKERS = ("opened for ", "reverted", "added as ", "refreshing ",
                   "updating ", "deleted as ", "- opened", "submitted")

    def cli_output_is_error(self, text: str) -> bool:
        t = (text or "").lower()
        return any(m in t for m in self._ERR_MARKERS)

    def classify_cli_result(self, rc, out, err):
        """p4 の «行ごと» の結果を見て成否を決める（r121）。

        p4 はファイル単位で成否を返し、1 つでも対象外があると rc が 0 以外に
        なる。フォルダへの一括操作では「もう追加済み」等が必ず混ざるので、
        rc と «エラー語がどこかにあるか» だけで判定すると **成功しているのに
        失敗と出る**。行ごとに «実行された / 対象外だった / 本当に失敗» を
        数え、本当の失敗が無ければ成功として扱う。"""
        from core.i18n import tr
        lines = [ln.strip() for ln in
                 ((out or "") + "\n" + (err or "")).splitlines() if ln.strip()]
        done = skipped = 0
        hard = []
        for ln in lines:
            low = ln.lower()
            if any(m in low for m in self._SKIP_MARKERS):
                skipped += 1
            elif any(m in low for m in self._ERR_MARKERS):
                hard.append(ln)
            elif any(m in low for m in self._OK_MARKERS):
                done += 1
        if hard:
            return False, "\n".join(hard[:20])
        if done == 0 and skipped == 0:
            # 判定材料が無い。従来どおり rc で決める。
            if rc == 0:
                return True, self.summarize_output(out)
            return False, (("\n".join(lines[:20])) or
                           "（p4 からの出力なし / rc=%s）" % rc)
        if done <= 1 and skipped == 0:
            # 単一ファイルの操作は従来どおり p4 の出力をそのまま見せる
            # （何が起きたかが一番分かりやすい）。
            return True, self.summarize_output(out)
        msg = tr("%d 件", "%d file(s)") % done
        if skipped:
            msg += tr("（対象外 %d 件はそのまま）",
                      " (%d already done, left as is)") % skipped
        return True, msg

    @staticmethod
    def recurse(paths):
        """フォルダには «/...» を付ける（r121）。

        P4V でフォルダに対して 追加 / チェックアウト / 元に戻す を行うと、
        **その下の全ファイル** が対象になる。p4 CLI では素のフォルダパスは
        «ファイル» として扱われてエラーになるので、再帰指定に直す必要がある。
        従来は sync だけが付けていて、add / edit / revert はフォルダを渡すと
        必ず失敗していた（ユーザー報告 2026-10-05）。"""
        import os as _os
        out = []
        for p in paths:
            try:
                is_dir = _os.path.isdir(p)
            except OSError:
                is_dir = False
            out.append(p.rstrip("\\/") + "/..." if is_dir else p)
        return out

    def _cli(self, args, cwd, what, file_from: int = None):
        """actions() 用: 実行結果を report() で UI へ返す（r58）。
        r122: 接続引数は «そのフォルダのワークスペース» で組む。
        r146: file_from に «args の中でファイル群が始まる位置» を渡すと、
        コマンドラインが長すぎる時に分割して実行する（平坦ビューで数百件を
        選ぶと上限 32767 文字を超え、一部しか実行されなかった）。"""
        conn = self.conn_args_for(cwd)
        cmd = [self.p4] + conn + args
        fixed = None if file_from is None else (1 + len(conn) + file_from)
        return lambda: self.run_cli_async(cmd, cwd, what, fixed_len=fixed)

    def _vc(self, command, paths):
        return lambda: spawn([self.p4vc, command] + list(paths))

    def _checkout_precheck(self, paths):
        """直近の fstat（キャッシュ・I/Oなし）から、チェックアウトの可否と
        確認文を決める。返り値: (extras dict or None)。
        - 誰かが排他ロック中 → 実行不可（灰色＋理由）
        - 誰かがチェックアウト中 → 同時編集の確認"""
        from core.i18n import tr
        locked, opened = [], []
        for p in paths:
            info = self.file_info(p)
            if not info:
                continue
            if info.get("locked"):
                locked.append((os.path.basename(p), ", ".join(info.get("locks") or ["?"])))
            elif info.get("others"):
                opened.append((os.path.basename(p), ", ".join(info["others"])))
        if locked:
            who = "\n".join("  %s — %s" % x for x in locked[:8])
            return {"enabled": False,
                    "tooltip": tr("他者がロック中のためチェックアウトできません:\n",
                                  "Locked by another user; cannot checkout:\n") + who}
        if opened:
            who = "\n".join("  %s — %s" % x for x in opened[:8])
            return {"confirm": tr(
                "次のファイルは他のユーザーがチェックアウト中です:\n%s\n\n"
                "続行すると同時編集になり、サブミット時に resolve（マージ）が"
                "必要になります。チェックアウトしますか？" % who,
                "The following files are checked out by other users:\n%s\n\n"
                "Continuing means concurrent editing; you will need to resolve "
                "(merge) at submit time. Checkout anyway?" % who)}
        return None

    def actions(self, root: str, paths):
        from core.i18n import tr
        paths = list(paths)
        cwd = os.path.dirname(paths[0]) if paths else root
        rpaths = self.recurse(paths)      # r121: フォルダは «その下すべて»
        co = (tr("チェックアウト（p4 edit）", "Checkout (p4 edit)"),
              self._cli(["edit"] + rpaths, cwd, tr("チェックアウト", "Checkout"),
                        file_from=1))
        extra = self._checkout_precheck(paths)
        if extra:
            co = co + (extra,)
        acts = [
            co,
            (tr("追加（p4 add）", "Add (p4 add)"),
             self._cli(["add"] + rpaths, cwd, tr("追加", "Add"), file_from=1)),
            (tr("最新を取得（p4 sync）", "Get latest (p4 sync)"),
             self._cli(["sync"] + rpaths, cwd, tr("最新を取得", "Sync"), file_from=1)),
            (tr("変更を元に戻す（p4 revert）", "Revert (p4 revert)"),
             self._cli(["revert"] + rpaths, cwd, tr("変更を元に戻す", "Revert"),
                        file_from=1),
             {"confirm": tr("%d 件のローカル変更を破棄して元に戻します。よろしいですか？"
                            % len(paths),
                            "Discard local changes of %d file(s) and revert?"
                            % len(paths))}),
        ]
        if self.p4vc:
            acts += [
                (None, None),
                (tr("サブミット...（P4V）", "Submit... (P4V)"), self._vc("submit", paths)),
                (tr("差分（P4V）", "Diff (P4V)"), self._vc("diff", paths[:1])),
                (tr("履歴（P4V）", "History (P4V)"), self._vc("history", paths[:1])),
                (tr("タイムラプス（P4V）", "Time-lapse (P4V)"),
                 self._vc("timelapse", paths[:1])),
            ]
        elif self.p4v:
            acts += [(None, None),
                     (tr("P4V を開く", "Open P4V"),
                      (lambda: spawn([self.p4v, "-s", paths[0]] if paths else [self.p4v])))]
        return acts
