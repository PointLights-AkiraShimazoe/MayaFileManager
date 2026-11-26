# -*- coding: utf-8 -*-
"""Manager の «シーンを保存» で、上書き先が読み取り専用の時の判断と処置（r150）。

r149 では、この確認を **Maya 側**（core/maya_save_watch.py の _mfm_ro_fix）で
出していた。ユーザー指摘（2026-11-18、H-15 の実機確認）:
  「MayaFileManager からの実行なのに、静かに maya 側で出ていて何が起きているのか
    気づくまでに時間がかかりました。Manager で意思決定し、Manager で保存が
    終わらないと駄目です」
→ Manager から行う保存は、**送る前に Manager が** 上書き先を調べ、Manager で聞き、
  チェックアウト（p4 edit）・読み取り専用の解除も Manager が行ってから保存を送る。
  Maya 側では何も聞かない。Maya の File > Save Scene（Maya で始めた保存）は
  従来どおり Maya 側で聞く。

ここは Qt を使わない（判断と処置だけ）。聞くのは ui/main_window_dcc.py。
"""
import json
import os
import stat
import subprocess
import time


def needs_fix(path: str) -> bool:
    """上書き先が既にあって書き込めない（読み取り専用）か。"""
    try:
        return bool(path) and os.path.isfile(path) and not os.access(path, os.W_OK)
    except OSError:
        return False


def _conn_for(provider, path: str):
    """そのファイル用の接続引数。表記の比較でワークスペースが見つからない時は、
    ジャンクションの実体どうしでも比べて -c を決める（r155: 実体側のパスで開いたシーン）。"""
    d = os.path.dirname(path)
    conn = list(provider.conn_args_for(d) or [])
    try:
        if getattr(provider, "client_for", None) and not provider.client_for(d):
            for _k, name, root in (provider._all_clients() or []):
                if root and name and map_to_root(path, str(root)):
                    out, skip = [], False
                    for a in conn:
                        if skip:
                            skip = False
                            continue
                        if a == "-c":
                            skip = True
                            continue
                        out.append(a)
                    return out + ["-c", str(name)]
    except Exception:
        pass
    return conn


def p4_edit_cmd(provider, path: str):
    """p4 edit のコマンド（そのファイルのワークスペースの -c で）。Perforce が
    使えない時は None（«チェックアウトして保存» を出さない）。"""
    try:
        exe = getattr(provider, "p4", None)
        if provider is None or not exe or not getattr(provider, "available", False):
            return None
        return [str(exe)] + _conn_for(provider, path) + ["edit", path]
    except Exception:
        return None


# ── Perforce 上の状態（r154、ユーザー指示 2026-11-24）────────────────────────────
# 「先に状態をチェックして、チェックアウトする必要があれば選択肢を出してください」
# 「他の人間にチェックアウトされてる際は UE の様に、書き込みは出来るようにする」
# 聞く前に p4 fstat で «管理下か・誰がチェックアウトしているか・排他ロックか・
# 手元が最新か» を調べ、選択肢と一言の説明を決める。下の 3 つの関数は
# Maya 側（core/maya_save_watch.py）にもそのまま埋め込むので、標準ライブラリ以外を使わない。

# 配布版（PyInstaller の exe）には .py のソースが入らないので、inspect.getsource で
# 取り出してはいけない（v0.9.29 の実機障害: 取り出しに失敗して Maya へ見張りが
# 一切入らなかった）。ソースを文字列で持ち、ここでも Maya 側でも同じ文字列を実行する。
HELPERS_SRC = r'''
def parse_fstat(text):
    """p4 -ztag fstat の出力から状態を取り出す。分からない項目は既定値のまま。"""
    st = {"tracked": False, "others": [], "locked": False, "have": None, "head": None,
          "head_deleted": False}
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln.startswith("... "):
            continue
        parts = ln[4:].split(" ", 1)
        k = parts[0]
        v = parts[1].strip() if len(parts) > 1 else ""
        if k == "depotFile":
            st["tracked"] = True
        elif k.startswith("otherOpen") and k[9:].isdigit():
            st["others"].append(v)
        elif k.startswith("otherLock"):
            st["locked"] = True
        elif k == "haveRev":
            st["have"] = int(v) if v.isdigit() else None
        elif k == "headRev":
            st["head"] = int(v) if v.isdigit() else None
        elif k == "headAction" and "delete" in v:
            st["head_deleted"] = True
    return st


def choice_keys(st, has_p4):
    """出す選択肢。
      "co"   チェックアウトして保存
      "rw"   書き込み可能にして保存（Perforce のファイルを、チェックアウトせず手元だけで保存）
      "unro" 読み取り専用を解除して保存（Perforce と関係ない、ただの読み取り専用）
      "no"   保存しない
    st が None（Perforce の状態を調べられなかった）で Perforce があれば、従来どおり 3 択。"""
    if not has_p4 or (st is not None and not st.get("tracked")):
        return ["unro", "no"]
    if st is not None and st.get("head_deleted"):
        return ["rw", "no"]
    return ["co", "rw", "no"]


def disabled_keys(st):
    """見せるが押せない選択肢（r154、島添さん指示 2026-11-24）。他のユーザーが
    チェックアウトしている（排他を含む）時は «チェックアウトして保存» を押せなくする。"""
    if st and st.get("tracked") and (st.get("others") or st.get("locked")):
        return ["co"]
    return []


def status_lines(st, has_p4, T, scene=False):
    """状態を端的な 1 文で（r154、2026-11-24 島添さん指示）。戻り値 (主文, 補足)。
    補足は «手元が最新でない» 等の付け足し（無ければ ""）。T は status_texts() の表。
    st が None（Perforce に聞けなかった）で Perforce があれば、その旨を言う（r155）。"""
    if has_p4 and st is None:
        return T["unknown"], ""
    if not has_p4 or st is None or not st.get("tracked"):
        return T["ro"], ""
    if st.get("head_deleted"):
        return T["deleted"], ""
    users = ", ".join(o.split("@", 1)[0] for o in st.get("others") or [])
    if st.get("locked"):
        main = T["locked"] % (users or "?")
    elif users:
        main = T["others"] % users
    else:
        main = T["notco"]
    h, H = st.get("have"), st.get("head")
    behind = T["behind_scene" if scene else "behind"]
    extra = behind % (h, H) if (h is not None and H is not None and h < H) else ""
    return main, extra

# r155（H-20 の詰め、2026-11-26）: 実機の Perforce で確かめた «管理下なのに
# ただの読み取り専用に見える» 3 つの穴を塞ぐ。
#   1. 日本語の名前: P4CHARSET=utf8 だと、コマンド行の引数（Windows では ANSI=CP932 で
#      渡る）が «No Translation» で弾かれる → パスを UTF-8 の引数ファイル（-C utf8 -x）で渡す
#   2. ジャンクション・シンボリックリンクの先（実体）のパスで開いたシーン:
#      «is not under client's root 'X'» → 実体どうしで比べて X の下のパスに直して聞き直す
#   3. Perforce に聞けなかった（接続できない・ログイン切れ等）: «管理外» とは扱わず
#      «分からない»（None）にして、チェックアウトの選択肢を消さない
_FSTAT_UNTRACKED = ("no such file", "not in client view", "not under client", "not on client")


def fstat_state(text):
    """p4 -ztag fstat の出力 → 状態。管理外と分かる時は tracked=False、
    Perforce に聞けなかった時（接続失敗・ログイン切れ・その他のエラー）は None。"""
    st = parse_fstat(text)
    if st["tracked"]:
        return st
    low = (text or "").lower()
    if any(m in low for m in _FSTAT_UNTRACKED):
        return st
    return None


def p4_run(base, args, path, timeout=8):
    """p4 を実行して出力（標準出力＋標準エラー）を返す。base は [p4.exe, 接続引数...]、
    args は ["-ztag", "fstat"] や ["edit"]、path は対象のファイル（最後に付ける）。
    日本語の名前とジャンクション先のパスを扱う（上の 1・2）。失敗しても例外は出さない。"""
    import os, re, subprocess, tempfile

    def _call(argv):
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000            # 黒い窓を出さない
        try:
            cwd = os.path.dirname(path)
            r = subprocess.run(argv, cwd=cwd if cwd and os.path.isdir(cwd) else None,
                               capture_output=True, timeout=timeout, **kw)
        except Exception as e:
            return "p4 failed: %s" % e
        raw = (r.stdout or b"") + b"\n" + (r.stderr or b"")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("mbcs" if os.name == "nt" else "latin-1", "replace")

    def _once(p):
        text = _call(list(base) + list(args) + [p])
        if "No Translation" not in text:
            return text
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix="mfm_p4_", suffix=".txt")
            os.write(fd, (p + "\n").encode("utf-8"))
            os.close(fd)
            return _call(list(base) + ["-C", "utf8", "-x", tmp] + list(args))
        except Exception as e:
            return text + "\n" + str(e)
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    text = _once(path)
    m = re.search(r"not under client's root '([^']+)'", text)
    if m:
        mapped = map_to_root(path, m.group(1))
        if mapped and os.path.normcase(mapped) != os.path.normcase(path):
            return _once(mapped)
    return text


def map_to_root(path, root):
    """path（ジャンクション・シンボリックリンクの先の実体パスでもよい）を、root の下の
    表記に直す。実体どうしで比べて root の下でなければ ""。ドライブ直下のルート（S:\\）も扱う。"""
    import os
    try:
        rt = root if not root.endswith(":") else root + os.sep
        rr = os.path.normcase(os.path.realpath(rt))
        rr_s = rr if rr.endswith(os.sep) else rr + os.sep
        rp_raw = os.path.realpath(path)
        rp = os.path.normcase(rp_raw)
        if rp == rr.rstrip(os.sep) or rp.startswith(rr_s):
            head = rt if rt.endswith(("\\", "/")) else rt + os.sep
            return head + rp_raw[len(rr_s):] if rp.startswith(rr_s) else rt
    except Exception:
        pass
    return ""
'''
exec(HELPERS_SRC)          # parse_fstat / choice_keys / disabled_keys / status_lines / fstat_state / p4_run / map_to_root を定義する


def p4_fstat_cmd(provider, path: str):
    """p4 -ztag fstat のコマンド（そのファイルのワークスペースの -c で）。無ければ None。"""
    try:
        exe = getattr(provider, "p4", None)
        if provider is None or not exe or not getattr(provider, "available", False):
            return None
        return [str(exe)] + _conn_for(provider, path) + ["-ztag", "fstat", path]
    except Exception:
        return None


def run_fstat(cmd, path: str, timeout: float = 8.0):
    """状態を調べる（UI スレッドから呼ばない）。調べられなければ None。"""
    if not cmd:
        return None
    try:
        # cmd は p4_fstat_cmd の形（[...接続, "-ztag", "fstat", path]）
        return fstat_state(p4_run(cmd[:-3], cmd[-3:-1], path, timeout))
    except Exception:
        return None


def status_texts(ja: bool = True) -> dict:
    """状態の文と選択肢の文言（Manager でも Maya 側でも同じ表を使う）。"""
    if ja:
        return {
            "notco": "このファイルはチェックアウトされていません。",
            "others": "このファイルは他のユーザー（%s）にチェックアウトされています。",
            "locked": "このファイルは他のユーザー（%s）に排他チェックアウトされています。",
            "ro": "このファイルは読み取り専用です。",
            "unknown": "このファイルは読み取り専用です（Perforce の状態を確認できませんでした）。",
            "deleted": "このファイルは Perforce 上で削除されています。",
            "behind": "このファイルは最新版ではありません（#%d / 最新 #%d）。",
            "behind_scene": "現在のシーンは最新版ではありません（#%d / 最新 #%d）。",
            "errlock": "読み込み時にエラーが出たシーンです。保存すると、読み込めなかった設定はファイルから消えます。",
            "co": "チェックアウトして保存", "rw": "書き込み可能にして保存",
            "unro": "読み取り専用を解除して保存", "no": "保存しない", "save": "保存する",
            "co_fail": "チェックアウトできませんでした。", "rw_fail": "読み取り専用を解除できませんでした。",
            "p4out": "Perforce の出力:", "cancelled": "保存しませんでした: ",
        }
    return {
        "notco": "This file is not checked out.",
        "others": "This file is checked out by another user (%s).",
        "locked": "This file is locked (exclusive checkout) by another user (%s).",
        "ro": "This file is read-only.",
        "unknown": "This file is read-only (could not get its Perforce status).",
        "deleted": "This file is deleted in Perforce.",
        "behind": "This file is not the latest revision (#%d / head #%d).",
        "behind_scene": "The current scene is not the latest revision (#%d / head #%d).",
        "errlock": "This scene had errors when it was opened. Saving drops the settings that could not be read.",
        "co": "Check out and save", "rw": "Make writable and save",
        "unro": "Clear read-only and save", "no": "Don't save", "save": "Save",
        "co_fail": "Could not check out.", "rw_fail": "Could not clear the read-only attribute.",
        "p4out": "Perforce output:", "cancelled": "Not saved: ",
    }


def note_texts() -> dict:
    from core.i18n import tr
    return status_texts(tr("ja", "en") == "ja")


def run_p4_edit(cmd, path: str):
    """p4 edit を実行する（UI スレッドから呼ばない）。戻り値 (書き込めるようになったか, 出力)。"""
    try:
        # cmd は p4_edit_cmd の形（[...接続, "edit", path]）
        out = p4_run(cmd[:-2], cmd[-2:-1], path, 30)
    except Exception as e:
        out = str(e)
    ok = os.access(path, os.W_OK)
    log("p4 edit", ok, path, out.strip()[:400])
    return ok, out.strip()


def make_writable(path: str):
    """読み取り専用を外す。戻り値 (書き込めるようになったか, エラー文)。"""
    err = ""
    try:
        os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    except Exception as e:
        err = str(e)
    ok = os.access(path, os.W_OK)
    log("chmod", ok, path, err)
    return ok, err


def log(how: str, ok: bool, path: str, out: str = ""):
    """保存の記録（mfm_maya_save.log）へ «ro_fix» を 1 行。Maya 側の記録と同じ形で、
    どこで判断したかを "by": "manager" で区別する。"""
    try:
        from core.maya_save_watch import log_path
        p = log_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        rec = {"ev": "ro_fix", "by": "manager",
               "how": how, "ok": bool(ok), "scene": path.replace("\\", "/"), "out": out,
               "time": time.strftime("%m-%d %H:%M:%S")}
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def texts() -> dict:
    from core.i18n import tr
    return {
        "text": tr("上書き先が読み取り専用です。\n%s",
                   "The file to overwrite is read-only.\n%s"),
        "co": tr("チェックアウトして保存", "Check out and save"),
        "rw": tr("書き込み可能にして保存", "Make writable and save"),
        "unro": tr("読み取り専用を解除して保存", "Clear read-only and save"),
        "no": tr("保存しない", "Don't save"),
        "co_fail": tr("チェックアウトできませんでした。", "Could not check out."),
        "rw_fail": tr("読み取り専用を解除できませんでした。", "Could not clear the read-only attribute."),
        "co_running": tr("チェックアウト中: %s …", "Checking out: %s …"),
        "co_done": tr("チェックアウトしました: %s", "Checked out: %s"),
        "cancelled": tr("保存しませんでした: %s", "Not saved: %s"),
    }
