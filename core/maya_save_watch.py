# -*- coding: utf-8 -*-
"""Maya の «保存» を見張る（r146、ユーザー報告 2026-11-07）。

「Maya の File メニューの Save Scene でエラーが出た。右クリックの
『シーンを保存』で事なきを得たが、これが頻発するなら解消法にたどり着ける
ユーザーがいないだろう」（エラーの文面は残っていなかった）

Manager からは Maya の File メニューの保存は見えない。そこで、接続できた
Maya に一度だけ «保存の見張り» を入れる（Maya 側の Python。シーンにも
ユーザーの userSetup にも何も書かない。Maya を閉じれば消える）。

  * 保存の直前（kBeforeSave）に、シーンの場所・プロジェクト（ワークスペース）・
    書き込めるか・空き容量を控え、Maya の出力（エラー・警告）を拾い始める
  * 保存の直後（kAfterSave）に «保存できた» と記録する
  * 保存の処理が終わっても kAfterSave が来なければ «保存できなかった» と
    記録し、その場で理由（拾った Maya の出力）と逃げ道を出す
  * シーンを開いた時（kAfterOpen）にも、シーンとプロジェクトを記録する
    （«Manager から開くとプロジェクトがずれるのでは» を後から確かめられる）

記録は ~/.maya_file_manager/logs/mfm_maya_save.log（1 行 1 件の JSON）。
"""
import json

VERSION = 1


def _maya_source(log_path: str, lang: str = "ja") -> str:
    ja = (lang or "ja").startswith("ja")
    title = "Maya File Manager"
    head = ("シーンを保存できませんでした。" if ja else "The scene could not be saved.")
    why = ("Maya の出力:" if ja else "Maya output:")
    none = ("（Maya の出力はありませんでした）" if ja else "(no output from Maya)")
    tip = ("逃げ道: File > Save Scene As で保存し直すか、Maya File Manager で保存先の"
           "フォルダを右クリック →「シーンを保存」で保存できます。" if ja else
           "Workaround: use File > Save Scene As, or right-click the folder in "
           "Maya File Manager and choose \"Save Scene\".")
    rec = ("記録: " if ja else "Log: ")
    return r'''
import __main__ as _mfm_main
_mfm_st = _mfm_main.__dict__.get("_mfm_save_watch")
if _mfm_st and _mfm_st.get("ver") == %(ver)d:
    _mfm_r = "save-watch: already"
else:
    import os, time, json, shutil
    from collections import deque
    import maya.api.OpenMaya as _om
    import maya.cmds as _cmds
    if _mfm_st:
        for _i in _mfm_st.get("ids", []):
            try:
                _om.MMessage.removeCallback(_i)
            except Exception:
                pass
    _mfm_st = {"ver": %(ver)d, "ids": [], "msgs": deque(maxlen=60), "cur": None,
               "log": %(log)r}
    _mfm_main.__dict__["_mfm_save_watch"] = _mfm_st

    def _mfm_w(rec):
        try:
            rec["time"] = time.strftime("%%m-%%d %%H:%%M:%%S")
            rec["maya_pid"] = os.getpid()
            p = _mfm_st["log"]
            try:
                if os.path.getsize(p) > 1024 * 1024:
                    os.replace(p, p + ".1")
            except OSError:
                pass
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _mfm_ctx():
        c = {}
        try:
            sn = _cmds.file(q=True, sceneName=True) or ""
            c["scene"] = sn
            c["workspace"] = _cmds.workspace(q=True, rootDirectory=True) or ""
            d = os.path.dirname(sn) if sn else ""
            c["exists"] = bool(sn) and os.path.isfile(sn)
            c["writable"] = (os.access(sn, os.W_OK) if c["exists"]
                             else (bool(d) and os.access(d, os.W_OK)))
            c["dir_writable"] = bool(d) and os.access(d, os.W_OK)
            try:
                c["free_mb"] = int(shutil.disk_usage(d).free / 1048576) if d else None
            except Exception:
                c["free_mb"] = None
            try:
                ws = os.path.normcase(os.path.realpath(c["workspace"] or "x")).rstrip("\\/")
                rd = os.path.normcase(os.path.realpath(d or "y"))
                c["scene_in_workspace"] = rd.startswith(ws + os.sep) or rd == ws
            except Exception:
                pass
        except Exception as e:
            c["ctx_error"] = str(e)
        return c

    def _mfm_out(msg, mtype, *_a):
        try:
            if _mfm_st["cur"] is None:
                return
            if mtype in (_om.MCommandMessage.kError, _om.MCommandMessage.kWarning,
                         _om.MCommandMessage.kStackTrace):
                t = (msg or "").strip()
                if t:
                    _mfm_st["cur"]["msgs"].append(t[:500])
        except Exception:
            pass

    def _mfm_check():
        cur, _mfm_st["cur"] = _mfm_st["cur"], None
        if cur is None or cur.get("ok"):
            return
        rec = dict(cur)
        rec["ev"] = "save_failed"
        rec["after"] = _mfm_ctx()
        _mfm_w(rec)
        try:
            if not _cmds.about(batch=True):
                lines = cur.get("msgs") or []
                body = "\n".join(lines[-4:]) if lines else %(none)r
                _cmds.confirmDialog(
                    title=%(title)r,
                    message=%(head)r + "\n\n" + (cur.get("scene") or "") + "\n\n"
                    + %(why)r + "\n" + body + "\n\n" + %(tip)r + "\n\n"
                    + %(rec)r + _mfm_st["log"],
                    button=["OK"])
        except Exception:
            pass

    def _mfm_before(*_a):
        try:
            c = _mfm_ctx()
            c["ev"] = "save_begin"
            c["msgs"] = []
            c["ok"] = False
            _mfm_st["cur"] = c
            if not _cmds.about(batch=True):
                import maya.utils as _mu
                _mu.executeDeferred(_mfm_check)
        except Exception:
            pass

    def _mfm_after(*_a):
        try:
            cur = _mfm_st["cur"]
            if cur is not None:
                cur["ok"] = True
            rec = _mfm_ctx()
            rec["ev"] = "saved"
            if cur is not None and cur.get("msgs"):
                rec["msgs"] = cur["msgs"]
            _mfm_w(rec)
        except Exception:
            pass

    def _mfm_opened(*_a):
        try:
            rec = _mfm_ctx()
            rec["ev"] = "opened"
            _mfm_w(rec)
        except Exception:
            pass

    _mfm_st["check"] = _mfm_check
    _mfm_st["ids"].append(_om.MSceneMessage.addCallback(_om.MSceneMessage.kBeforeSave, _mfm_before))
    _mfm_st["ids"].append(_om.MSceneMessage.addCallback(_om.MSceneMessage.kAfterSave, _mfm_after))
    _mfm_st["ids"].append(_om.MSceneMessage.addCallback(_om.MSceneMessage.kAfterOpen, _mfm_opened))
    _mfm_st["ids"].append(_om.MCommandMessage.addCommandOutputCallback(_mfm_out))
    _mfm_w(dict(_mfm_ctx(), ev="watch_installed"))
    _mfm_r = "save-watch: installed"
''' % {"ver": VERSION, "log": log_path, "title": title, "head": head, "why": why,
       "none": none, "tip": tip, "rec": rec}


def log_path() -> str:
    """記録の置き場所（Manager のログと同じフォルダ）。"""
    import os
    try:
        from ui.browser_util import _MFM_LOG_DIR as d
    except Exception:
        d = os.path.join(os.path.expanduser("~"), ".maya_file_manager", "logs")
    return os.path.join(d, "mfm_maya_save.log").replace("\\", "/")


def install_code(log_path: str, lang: str = "ja") -> str:
    """commandPort（Python）へ送る «単一の式»。返り値は状態の文字列。"""
    src = _maya_source(log_path, lang)
    return "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_r'))[1])({})" % src


def read_log(log_path: str, n: int = 50):
    """記録の最後の n 件（新しい順ではなく古い順）。壊れた行は飛ばす。"""
    out = []
    try:
        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except OSError:
        return out
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out
