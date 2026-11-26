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

VERSION = 7          # r155: 日本語の名前・ジャンクション先・Perforce に聞けない時（r154: 他のユーザー中は押せない）


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
    # r154: 聞く前に Perforce の状態を調べる。判断の関数は Manager と同じもの
    # （core/save_target_guard.py）をそのまま埋め込む。
    from core import save_target_guard as _sg
    helpers = _sg.HELPERS_SRC      # 配布版でも取れる（inspect.getsource は使わない）
    T = _sg.status_texts(ja)       # 状態の文・選択肢の文言（Manager と同じ表）
    return helpers + "\n_MFM_T = " + repr(T) + r'''
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

    def _mfm_p4_cmd(path):
        """Manager が渡した Perforce の接続（_mfm_p4）から、このファイル用の
        コマンドの前半を組む。分からなければ None（チェックアウトのボタンを出さない）。"""
        t = _mfm_main.__dict__.get("_mfm_p4") or {}
        exe = t.get("exe")
        if not exe:
            return None
        def _n(p):
            return os.path.normcase(os.path.normpath(p)) if p else ""
        key = _n(path)
        name = ""
        best = -1
        for root, cl in (t.get("clients") or []):
            r = _n(root)
            if not r:
                continue
            rr = r if r.endswith(os.sep) else r + os.sep
            if (key == r or key.startswith(rr)) and len(r) > best:
                best, name = len(r), cl
        if not name:
            # r155: ジャンクションの実体側のパスで開いたシーンは、実体どうしで比べる
            for root, cl in (t.get("clients") or []):
                if root and map_to_root(path, root):
                    name = cl
                    break
        base = list(t.get("base") or [])
        if name:
            out, skip = [], False
            for a in base:
                if skip:
                    skip = False
                    continue
                if a == "-c":
                    skip = True
                    continue
                out.append(a)
            base = out + ["-c", name]
        return [exe] + base

    def _mfm_status(path, p4):
        """Perforce 上の状態（r154）。調べられなければ None。"""
        import subprocess
        if not p4:
            return None
        try:
            # r155: 日本語の名前・ジャンクション先のパス・聞けなかった時を扱う（p4_run / fstat_state）
            return fstat_state(p4_run(p4, ["-ztag", "fstat"], path, 8))
        except Exception:
            return None

    def _mfm_ask(message, btns, disabled=()):
        """聞く（r154）。disabled のボタンは «見せるが押せない»。confirmDialog では
        押せないボタンを作れないので、その時だけ layoutDialog で同じ形の窓を出す。"""
        disabled = [b for b in btns if b in disabled]
        _mfm_st["last_ask"] = {"message": message, "button": list(btns), "disabled": disabled}
        if not disabled:
            return _cmds.confirmDialog(
                title=%(title)r, message=message, button=list(btns),
                defaultButton=btns[0], cancelButton=btns[-1], dismissString=btns[-1])
        enabled = [b for b in btns if b not in disabled]

        def _ui():
            form = _cmds.setParent(q=True)
            _cmds.formLayout(form, e=True, width=460)
            txt = _cmds.text(label=message, align="left")
            row = _cmds.rowLayout(numberOfColumns=len(btns))
            for b in btns:
                _cmds.button(label=b, enable=(b not in disabled), height=28,
                             command=(lambda *_a, _b=b: _cmds.layoutDialog(dismiss=_b)))
            _cmds.setParent("..")
            _cmds.formLayout(form, e=True,
                             attachForm=[(txt, "top", 14), (txt, "left", 14), (txt, "right", 14),
                                         (row, "left", 14), (row, "bottom", 12)],
                             attachControl=[(row, "top", 14, txt)])
        ans = _cmds.layoutDialog(ui=_ui, title=%(title)r)
        return ans if ans in enabled else btns[-1]

    def _mfm_ro_fix(path, extra=""):
        """読み取り専用のファイルを «保存できる状態» にするかを聞いて、実行する。
        書き込めるようになったら True。断った・出来なかった時は False。
        r154: 聞く前に Perforce の状態を調べ、選択肢と一言を決める（Manager と同じ判断）。"""
        import stat, subprocess
        if not path or not os.path.isfile(path) or os.access(path, os.W_OK):
            return True
        if _cmds.about(batch=True):
            return False
        name = os.path.basename(path)
        p4 = _mfm_p4_cmd(path)
        stt = _mfm_status(path, p4)
        keys = choice_keys(stt, bool(p4))
        off = [_MFM_T[k] for k in disabled_keys(stt) if k in keys]
        main, more = status_lines(stt, bool(p4), _MFM_T, scene=True)
        _mfm_w({"ev": "ro_status", "scene": path, "status": stt, "keys": keys})
        note = ""
        while True:
            # r154（島添さん指示 2026-11-24）: 状態は端的な 1 文、選択肢は状態に合わせる
            # （Perforce のファイル: チェックアウト／書き込み可能／保存しない、
            #   ただの読み取り専用: 読み取り専用を解除／保存しない）
            btns = [_MFM_T[k] for k in keys]
            body = [name, main] + ([more] if more else [])
            if extra:
                body += ["", extra]
            if note:
                body = [note, ""] + body
            ans = _mfm_ask("\n".join(body), btns, off)
            if ans == _MFM_T["co"] and p4 and not off:
                try:
                    out = p4_run(p4, ["edit"], path, 30)      # r155: 日本語の名前・ジャンクション先
                except Exception as e:
                    out = str(e)
                ok = os.access(path, os.W_OK)
                _mfm_w({"ev": "ro_fix", "how": "p4 edit", "ok": ok, "scene": path,
                        "out": out.strip()[:400]})
                if ok:
                    return True
                note = _MFM_T["co_fail"] + "\n" + _MFM_T["p4out"] + "\n" + (out.strip()[:600] or "-")
                keys = [k for k in keys if k != "co"]      # 次はチェックアウト以外の選択肢だけ
                continue
            if ans in (_MFM_T["rw"], _MFM_T["unro"]):
                err = ""
                try:
                    os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
                except Exception as e:
                    err = str(e)
                ok = os.access(path, os.W_OK)
                _mfm_w({"ev": "ro_fix", "how": "chmod", "ok": ok, "scene": path, "out": err})
                if ok:
                    return True
                _cmds.confirmDialog(title=%(title)r, message=_MFM_T["rw_fail"] + "\n" + err,
                                    button=["OK"])
                return False
            _mfm_w({"ev": "ro_fix", "how": "declined", "ok": False, "scene": path})
            return False

    def _mfm_check():
        cur, _mfm_st["cur"] = _mfm_st["cur"], None
        if cur is None or cur.get("ok"):
            return
        rec = dict(cur)
        rec["ev"] = "save_failed"
        rec["after"] = _mfm_ctx()
        _mfm_w(rec)
        # r149: 読み取り専用が理由なら、その場で直して保存し直す。断ったら何も
        # 出さない（理由は今のダイアログで伝わっている）。直した後の保存がまた
        # 失敗した時だけ、下のいつものお知らせへ進む。
        try:
            sc = cur.get("scene") or ""
            if (cur.get("exists") and not cur.get("writable") and not cur.get("retry")
                    and not _cmds.about(batch=True)):
                if not _mfm_ro_fix(sc):
                    return
                _mfm_st["retry_next"] = True
                try:
                    _cmds.file(save=True)
                except Exception:
                    pass          # まだ保存できない時は、次の見張りがいつものお知らせを出す
                finally:
                    _mfm_st["retry_next"] = False
                return
        except Exception:
            pass
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
            c["retry"] = bool(_mfm_st.get("retry_next"))
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
            _mfm_st.pop("open_err", None)   # 保存できた → もう «エラー付きで開いた» 状態ではない
            rec = _mfm_ctx()
            rec["ev"] = "saved"
            if cur is not None and cur.get("msgs"):
                rec["msgs"] = cur["msgs"]
            _mfm_w(rec)
        except Exception:
            pass

    def _mfm_opened(*_a):
        # r154: «エラー付きで開いた» かを開いた時点で覚える（file -q -errorStatus は
        # 開いた直後なら開いた結果。後の保存の失敗などでも変わるので、保存時には使わない）
        try:
            _mfm_st["open_seen"] = True
            _mfm_st["open_err"] = ((_cmds.file(q=True, sceneName=True) or "")
                                   if _cmds.file(q=True, errorStatus=True) else None)
        except Exception:
            pass
        try:
            rec = _mfm_ctx()
            rec["ev"] = "opened"
            rec["open_err"] = bool(_mfm_st.get("open_err"))
            _mfm_w(rec)
        except Exception:
            pass

    # ── r154: 保存の «前» に確かめる（ユーザー指示 2026-11-24）──────────────────
    # 「チェックアウトしてないと一度エラーが出てから選択になるが、先に状態を
    #   チェックして選択肢を出す。キャンセルすれば保存しないので、Maya のエラーが
    #   出る事はなくなるのが筋」
    # 1) File > Save Scene と Ctrl+S（どちらも FileMenu_SaveItem）は、保存を始める前に
    #    聞く。断れば保存そのものを呼ばないので、Maya のエラーは一切出ない。
    # 2) それ以外の保存（Save Scene As で既存の読み取り専用へ上書き等）は、保存の
    #    チェック用コールバックで聞く。直せばそのまま保存される。断った時は保存を
    #    止める（Maya のエラーの窓は出さない。スクリプトエディターに 1 行だけ残る）。
    def _mfm_restore_prompt():
        try:
            _cmds.file(prompt=_mfm_st.pop("prompt_was", True))
        except Exception:
            pass

    def _mfm_precheck(*_a):
        try:
            if _cmds.about(batch=True):
                return True
            sn = _cmds.file(q=True, sceneName=True) or ""
            if not sn or not os.path.isfile(sn) or os.access(sn, os.W_OK):
                return True
            if _mfm_ro_fix(sn):
                return True
            # 断った: 保存を止める。Maya の «File Error» の窓は出さない
            try:
                _mfm_st["prompt_was"] = bool(_cmds.file(q=True, prompt=True))
                _cmds.file(prompt=False)
                import maya.utils as _mu
                _mu.executeDeferred(_mfm_restore_prompt)
            except Exception:
                pass
            _om.MGlobal.displayInfo(_MFM_T["cancelled"] + sn)
            return False
        except Exception:
            return True                 # 見張りの失敗で保存を止めない

    def _mfm_menu_ok():
        """FileMenu_SaveItem の前に呼ぶ。保存してよければ 1、断られたら 0。

        r154 修正（実機 2026-11-24）: «エラー付きで開いた» シーン（Arnold 無しで開いた
        Arnold のシーン等）では、Maya の素の保存は «A file must be given a name before
        saving» で、保存のチェック用コールバックより前に止まる。ここで先に見つけて
        聞き（読み取り専用ならその選択肢と一緒に）、保存するなら同じ名前を付け直して
        から元の処理へ渡す（Manager の «上書き保存できるようにする» と同じ処置）。
        «保存するか» の判断は元の処理と同じ（変更がある、またはファイルがまだ無い）。"""
        try:
            sn = _cmds.file(q=True, sceneName=True) or ""
            if not sn:
                return 1                # 名前が無い → いつもの «名前を付けて保存» へ
            if not (_cmds.file(q=True, modified=True) or not _cmds.file(q=True, exists=True)):
                return 1                # 変更なし → いつもの «変更はありません»
            if _mfm_st.get("open_seen"):
                err = bool(sn) and _mfm_st.get("open_err") == sn
            else:
                # 見張りを入れる前に開かれたシーン: 開いた結果は分からないので、
                # Maya の «直前のファイル操作でエラーがあったか» で判断する
                try:
                    err = bool(_cmds.file(q=True, errorStatus=True))
                except Exception:
                    err = False
            ro = os.path.isfile(sn) and not os.access(sn, os.W_OK)
            _mfm_w({"ev": "menu_save", "scene": sn, "errlock": err, "readonly": ro})
            if not ro and not err:
                return 1
            if ro:
                if not _mfm_ro_fix(sn, _MFM_T["errlock"] if err else ""):
                    _om.MGlobal.displayInfo(_MFM_T["cancelled"] + sn)
                    return 0
            else:
                ans = _cmds.confirmDialog(
                    title=%(title)r, message=os.path.basename(sn) + "\n" + _MFM_T["errlock"],
                    button=[_MFM_T["save"], _MFM_T["no"]], defaultButton=_MFM_T["save"],
                    cancelButton=_MFM_T["no"], dismissString=_MFM_T["no"])
                if ans != _MFM_T["save"]:
                    _mfm_w({"ev": "errlock", "how": "declined", "scene": sn})
                    _om.MGlobal.displayInfo(_MFM_T["cancelled"] + sn)
                    return 0
            if err:
                _cmds.file(rename=sn)   # «エラー付きで開いた» 止めを解く（同じ名前）
                _mfm_st.pop("open_err", None)
                _mfm_w({"ev": "errlock", "how": "renamed", "scene": sn})
            return 1
        except Exception as e:
            _mfm_w({"ev": "menu_save_error", "error": str(e)})
            return 1

    def _mfm_wrap_menu_save():
        """FileMenu_SaveItem の前に _mfm_menu_ok を挟む。元の手続きは Maya に入っている
        FileMenu.mel から «その版のまま» 取り出して別名で定義する（版ごとの違いに追従）。
        他のツールが既に置き換えている時は触らない（2 のコールバックで守る）。"""
        import re
        import maya.mel as _mel
        try:
            if _mel.eval('exists "mfm_orig_FileMenu_SaveItem"'):
                where = "already"
            else:
                where = _mel.eval("whatIs FileMenu_SaveItem") or ""
                src_path = where.split(":", 1)[-1].strip() if "found in" in where else ""
                if not src_path.replace("\\", "/").lower().endswith("/startup/filemenu.mel"):
                    return "menu-wrap: skipped (%%s)" %% where
                txt = open(src_path, encoding="utf-8", errors="replace").read()
                m = re.search(r"global proc FileMenu_SaveItem\s*\(\s*\)", txt)
                if not m:
                    return "menu-wrap: not found"
                i, depth, j = txt.index("{", m.end()), 0, None
                for k in range(i, len(txt)):
                    if txt[k] == "{":
                        depth += 1
                    elif txt[k] == "}":
                        depth -= 1
                        if depth == 0:
                            j = k + 1
                            break
                if j is None:
                    return "menu-wrap: parse failed"
                _mel.eval("global proc mfm_orig_FileMenu_SaveItem()" + txt[i:j])
            _mel.eval('global proc FileMenu_SaveItem() {'
                      ' if (`python "__import__(\'__main__\').__dict__[\'_mfm_save_watch\']'
                      '[\'menu_ok\']()"`) mfm_orig_FileMenu_SaveItem; }')
            return "menu-wrap: ok (%%s)" %% where
        except Exception as e:
            return "menu-wrap: error %%s" %% e

    _mfm_st["menu_ok"] = _mfm_menu_ok
    _mfm_st["ask"] = _mfm_ask
    _mfm_st["wrap_menu"] = _mfm_wrap_menu_save
    _mfm_st["precheck"] = _mfm_precheck
    _mfm_st["status"] = _mfm_status
    _mfm_st["ids"].append(_om.MSceneMessage.addCheckCallback(
        _om.MSceneMessage.kBeforeSaveCheck, _mfm_precheck))
    _mfm_st["menu_wrap"] = (_mfm_wrap_menu_save() if not _cmds.about(batch=True)
                            else "menu-wrap: batch")

    _mfm_st["check"] = _mfm_check
    _mfm_st["ro_fix"] = _mfm_ro_fix
    _mfm_st["p4_cmd"] = _mfm_p4_cmd
    _mfm_st["ids"].append(_om.MSceneMessage.addCallback(_om.MSceneMessage.kBeforeSave, _mfm_before))
    _mfm_st["ids"].append(_om.MSceneMessage.addCallback(_om.MSceneMessage.kAfterSave, _mfm_after))
    _mfm_st["ids"].append(_om.MSceneMessage.addCallback(_om.MSceneMessage.kAfterOpen, _mfm_opened))
    _mfm_st["ids"].append(_om.MCommandMessage.addCommandOutputCallback(_mfm_out))
    _mfm_w(dict(_mfm_ctx(), ev="watch_installed"))
    _mfm_r = "save-watch: installed"
''' % {"ver": VERSION, "log": log_path, "title": title, "head": head, "why": why,
       "none": none, "tip": tip, "rec": rec}


def p4_table_code(table: dict) -> str:
    """Manager が持っている Perforce の接続を Maya へ渡す «単一の式»（r149）。

    Maya 側の «チェックアウトして保存» は、この表から p4 の引数を組む。Manager と
    同じ決め方（そのファイルが入っているワークスペースの -c に差し替える）なので、
    既定以外のワークスペースのファイルでも通る。表が無い時はボタン自体を出さない。"""
    return ("(__import__('__main__').__dict__.__setitem__('_mfm_p4', %r), 'p4-table')[1]"
            % (dict(table or {}),))


def p4_table(provider) -> dict:
    """P4Provider の «今わかっている» 接続から表を作る（通信はしない）。"""
    try:
        exe = getattr(provider, "p4", None)
        if not exe or not getattr(provider, "available", False):
            return {}
        clients = getattr(provider, "_clients", None) or []
        return {"exe": str(exe), "base": list(getattr(provider, "_conn_args", []) or []),
                "clients": [[str(root), str(name)] for _k, name, root in clients]}
    except Exception:
        return {}


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
