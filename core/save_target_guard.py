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


def p4_edit_cmd(provider, path: str):
    """p4 edit のコマンド（そのファイルのワークスペースの -c で）。Perforce が
    使えない時は None（«チェックアウトして保存» を出さない）。"""
    try:
        exe = getattr(provider, "p4", None)
        if provider is None or not exe or not getattr(provider, "available", False):
            return None
        conn = list(provider.conn_args_for(os.path.dirname(path)) or [])
        return [str(exe)] + conn + ["edit", path]
    except Exception:
        return None


# ── Perforce 上の状態（r154、ユーザー指示 2026-11-24）────────────────────────────
# 「先に状態をチェックして、チェックアウトする必要があれば選択肢を出してください」
# 「他の人間にチェックアウトされてる際は UE の様に、書き込みは出来るようにする」
# 聞く前に p4 fstat で «管理下か・誰がチェックアウトしているか・排他ロックか・
# 手元が最新か» を調べ、選択肢と一言の説明を決める。下の 3 つの関数は
# Maya 側（core/maya_save_watch.py）にもそのまま埋め込むので、標準ライブラリ以外を使わない。

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
    """出す選択肢（"co" チェックアウトして保存 / "rw" 書き込み可能にして保存 / "no" 保存しない）。
    st が None（調べられなかった）なら、Perforce があればチェックアウトも出す（従来どおり）。
    管理下でない・排他ロック中・最新で削除済みのファイルには «チェックアウト» を出さない。
    他の人がチェックアウトしていても、書き込み可能にして保存はできる（UE の Make Writable）。"""
    if not has_p4:
        return ["rw", "no"]
    if st is None:
        return ["co", "rw", "no"]
    if not st.get("tracked") or st.get("locked") or st.get("head_deleted"):
        return ["rw", "no"]
    return ["co", "rw", "no"]


def status_note(st, T):
    """状態の一言（無ければ ""）。T は文言の表（others / locked / behind / untracked）。"""
    if not st:
        return ""
    out = []
    users = ", ".join(o.split("@", 1)[0] + ("（" + o.split("@", 1)[1] + "）" if "@" in o else "")
                      for o in st.get("others") or [])
    if st.get("locked"):
        out.append(T["locked"] % (users or "?"))
    elif users:
        out.append(T["others"] % users)
    h, H = st.get("have"), st.get("head")
    if st.get("tracked") and h is not None and H is not None and h < H:
        out.append(T["behind"] % (h, H))
    if not st.get("tracked"):
        out.append(T["untracked"])
    return "\n".join(out)


def p4_fstat_cmd(provider, path: str):
    """p4 -ztag fstat のコマンド（そのファイルのワークスペースの -c で）。無ければ None。"""
    try:
        exe = getattr(provider, "p4", None)
        if provider is None or not exe or not getattr(provider, "available", False):
            return None
        conn = list(provider.conn_args_for(os.path.dirname(path)) or [])
        return [str(exe)] + conn + ["-ztag", "fstat", path]
    except Exception:
        return None


def run_fstat(cmd, path: str, timeout: float = 8.0):
    """状態を調べる（UI スレッドから呼ばない）。調べられなければ None。"""
    if not cmd:
        return None
    try:
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000
        r = subprocess.run(cmd, cwd=os.path.dirname(path) or None,
                           capture_output=True, timeout=timeout, **kw)
        raw = (r.stdout or b"") + b"\n" + (r.stderr or b"")
        try:
            out = raw.decode("utf-8")
        except UnicodeDecodeError:
            out = raw.decode("mbcs" if os.name == "nt" else "latin-1", "replace")
        return parse_fstat(out)
    except Exception:
        return None


def note_texts() -> dict:
    from core.i18n import tr
    return {
        "others": tr("%s もチェックアウトしています。", "Also checked out by %s."),
        "locked": tr("%s が排他チェックアウト中のため、チェックアウトできません。\n"
                     "書き込み可能にすれば手元には保存できます（サブミットは相手の作業後）。",
                     "Locked by %s, so it can't be checked out.\n"
                     "Make it writable to save locally (submit after they finish)."),
        "behind": tr("手元は最新ではありません（手元 #%d / 最新 #%d）。",
                     "Not the latest revision (have #%d, head #%d)."),
        "untracked": tr("Perforce の管理外のファイルです。", "Not under Perforce."),
    }


def run_p4_edit(cmd, path: str):
    """p4 edit を実行する（UI スレッドから呼ばない）。戻り値 (書き込めるようになったか, 出力)。"""
    out = ""
    try:
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000          # 黒い窓を出さない
        r = subprocess.run(cmd, cwd=os.path.dirname(path) or None,
                           capture_output=True, timeout=30, **kw)
        raw = (r.stdout or b"") + b"\n" + (r.stderr or b"")
        try:
            out = raw.decode("utf-8")
        except UnicodeDecodeError:
            out = raw.decode("mbcs" if os.name == "nt" else "latin-1", "replace")
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
        "no": tr("保存しない", "Don't save"),
        "co_fail": tr("チェックアウトできませんでした。", "Could not check out."),
        "rw_fail": tr("読み取り専用を外せませんでした。", "Could not make the file writable."),
        "co_running": tr("チェックアウト中: %s …", "Checking out: %s …"),
        "co_done": tr("チェックアウトしました: %s", "Checked out: %s"),
        "cancelled": tr("保存しませんでした: %s", "Not saved: %s"),
    }
