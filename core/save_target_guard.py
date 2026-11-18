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
        "rw": tr("読み取り専用を外して保存", "Make writable and save"),
        "no": tr("保存しない", "Don't save"),
        "co_fail": tr("チェックアウトできませんでした。", "Could not check out."),
        "rw_fail": tr("読み取り専用を外せませんでした。", "Could not make the file writable."),
        "co_running": tr("チェックアウト中: %s …", "Checking out: %s …"),
        "co_done": tr("チェックアウトしました: %s", "Checked out: %s"),
        "cancelled": tr("保存しませんでした: %s", "Not saved: %s"),
    }
