# -*- coding: utf-8 -*-
"""«開けたが、読み込み中に Maya がエラーを出した» を «開けなかった» と分ける
（r149、ユーザー報告 2026-11-18）。

報告 1: シーンを開くと «連携エラー» のダイアログに Maya のエラー（プラグインが
        足すアトリビュートが無い、等）が並ぶ。開けなかったように見える（実際は
        開けていて、同じファイルを開き直していた）。
報告 2: Manager から開いたシーンを Maya の File > Save Scene で保存しようとすると
        エラーで保存できない事がある。Manager の «シーンを保存» を一度通すと、
        次からは保存できる。

どちらも同じ出来事だった（本物の Maya 2027 で確認）:
  * 読み込み中にエラーが 1 行でも出ると、Maya の file コマンドは **開いた上で**
    例外を投げる（kAfterOpen は来る。シーン名も入る）。
  * その状態の `file -save` は、名前が入っているのに
    「A file must be given a name before saving. Use file -rename first」で必ず
    止まる。読み込めなかった設定を上書きで消さないための Maya の動きで、Maya の
    File > Open で開いても同じになる（Manager の開き方のせいではない）。
  * `file -rename <同じパス>` を 1 回通すと、素の保存が通るようになる。
  * インポート・リファレンスでは起きない（例外にならず、保存も通る）。

r150（ユーザー指示 2026-11-18、H-14 の実機確認）:
  「文面が長すぎます。端的にエラーが起きたことと読み取り専用を解除する事を
    伝えて意思確認をしてください。詳細はスクリプトエディターで大丈夫です。
    maya の標準のエラーログと被ってでてしまい、ボタンが見えずに maya 操作が
    できなくなったかと思いました」
  → **Maya 側ではダイアログを出さない。** 何が起きたか（エラーの全行・Arnold の
    見当）はスクリプトエディターにだけ書き、«開けた» と返す。どうするかは
    Manager が短く聞き（ui/main_window_dcc.py _ask_unlock_after_open）、
    «上書き保存できるようにする» なら同じ Maya へ UNLOCK_SRC を送る。Manager から行った操作の判断は
    Manager で受ける（H-15 と同じ決まり）。
"""

RESULT_PREFIX = "OpenedWithErrors:"
UNLOCK_PREFIX = "MfmUnlocked:"
ARNOLD_PREFIX = "MfmArnold:"

SRC = r'''
def _mfm_open(_p, _t):
    import re
    import maya.cmds as cmds
    import maya.api.OpenMaya as om2
    done = []
    cid = om2.MSceneMessage.addCallback(om2.MSceneMessage.kAfterOpen,
                                        lambda *a: done.append(1))
    try:
        try:
            cmds.file(_p, open=True, force=True, ignoreVersion=True)
            return None
        except Exception as e:
            if not done:
                raise              # 開けていない → 従来どおり «失敗»
            err = str(e)
    finally:
        try:
            om2.MMessage.removeCallback(cid)
        except Exception:
            pass
    lines = [l.strip() for l in err.splitlines()
             if l.strip() and l.strip() != "Error reading file."]
    arnold = any(".ai_" in l for l in lines)
    # r150: 詳細はスクリプトエディターへ（ダイアログは出さない）
    try:
        out = ["", "// Maya File Manager: " + _t["head"] % len(lines)]
        out += ["//   " + l for l in lines]
        if arnold:
            out.append("// " + _t["arnold"])
        out.append("// " + _t["why"])
        print("\n".join(out))
        om2.MGlobal.displayWarning("Maya File Manager: " + _t["head"] % len(lines))
    except Exception:
        pass
    # r151: Arnold（mtoa）が入っているのに読み込まれていないなら «a»。
    # Manager が «Arnold を読み込んで開き直す» を出す（入っていない PC では出さない）
    flag = "-"
    if arnold:
        try:
            loaded = bool(cmds.pluginInfo("mtoa", q=True, loaded=True))
        except Exception:
            loaded = False
        if not loaded and _mfm_mtoa_available():
            flag = "a"
    return "OpenedWithErrors:%d:pending:%s:%s" % (len(lines), flag, _p)


def _mfm_mtoa_available():
    import os
    import maya.mel as mel
    try:
        dirs = (mel.eval("getenv MAYA_PLUG_IN_PATH") or "").split(os.pathsep)
    except Exception:
        dirs = []
    for d in dirs:
        for n in ("mtoa.mll", "mtoa.so", "mtoa.bundle"):
            try:
                if d and os.path.isfile(os.path.join(d, n)):
                    return True
            except Exception:
                pass
    return False


def _mfm_arnold_reopen(_p, _t):
    """Arnold を読み込んで、同じシーンを開き直す（r151）。"""
    import os
    import maya.cmds as cmds
    cur = cmds.file(q=True, sceneName=True) or ""
    if not cur or os.path.normcase(os.path.normpath(cur)) != os.path.normcase(os.path.normpath(_p)):
        return "MfmArnold:other"
    # 開いた後に手を入れていたら捨てない。«変更あり» の印は、エラー付きで開いた直後から
    # 立っている（本物の Maya 2027 で確認）ので使えない。開くと取り消しの記録は空に
    # なるので、記録が増えていれば «手を入れた» と見る（取り消しが切ってある時は見られない）。
    try:
        if cmds.undoInfo(q=True, state=True) and not cmds.undoInfo(q=True, undoQueueEmpty=True):
            return "MfmArnold:modified"
    except Exception:
        pass
    try:
        cmds.loadPlugin("mtoa", quiet=True)
    except Exception as e:
        return "MfmArnold:loadfail:" + str(e).strip().splitlines()[0][:300]
    r = _mfm_open(_p, _t)
    return r if r else "MfmArnold:ok"
'''

UNLOCK_SRC = r'''
def _mfm_unlock(_p, _msg):
    import os
    import maya.cmds as cmds
    import maya.api.OpenMaya as om2
    cur = cmds.file(q=True, sceneName=True) or ""
    if not cur or os.path.normcase(os.path.normpath(cur)) != os.path.normcase(os.path.normpath(_p)):
        return "MfmUnlocked:other"     # 別のシーンになっている → 何もしない
    cmds.file(rename=cur)
    try:   # r154: 保存の見張りに «もう上書きしてよい» と伝える（Ctrl+S で重ねて聞かない）
        import __main__
        (__main__.__dict__.get("_mfm_save_watch") or {}).pop("open_err", None)
    except Exception:
        pass
    try:
        om2.MGlobal.displayInfo("Maya File Manager: " + _msg)
    except Exception:
        pass
    return "MfmUnlocked:ok"
'''


def texts() -> dict:
    """スクリプトエディターへ書く文言（Manager の表示言語で）。"""
    from core.i18n import tr
    return {
        "head": tr("シーンは開きましたが、読み込み中に Maya がエラーを出しました（%d 行）。",
                   "The scene was opened, but Maya reported errors while reading it (%d lines)."),
        "arnold": tr("「.ai_」で始まるのは Arnold（mtoa）のアトリビュートです。"
                     "Arnold を読み込んだ Maya で開き直すと、このエラーは出ません。",
                     "Names starting with \".ai_\" are Arnold (mtoa) attributes. "
                     "Open the scene in a Maya with Arnold loaded and these errors go away."),
        "why": tr("この状態では Maya の Save Scene は「A file must be given a name before "
                  "saving」で止まります（読み込めなかった設定を上書きで消さないための Maya の"
                  "動き）。Manager で «上書き保存できるようにする» を選ぶと上書き保存でき、読み込めなかった設定は"
                  "保存した時にファイルから消えます。",
                  "In this state Maya's Save Scene stops with \"A file must be given a name "
                  "before saving\" (Maya does this so the settings it could not read are not "
                  "lost). Choose \"Allow saving\" in the Manager to allow saving over the file; the "
                  "settings that could not be read are then gone from the file once saved."),
    }


def arnold_texts() -> dict:
    """Arnold が読み込まれていない時に Manager で聞く文言（r151）。"""
    from core.i18n import tr
    return {
        "text": tr("Arnold が読み込まれていないため、シーンの Arnold の設定を読み込めませんでした。\n"
                   "Arnold を読み込んで開き直しますか？",
                   "Arnold is not loaded, so the scene's Arnold settings could not be read.\n"
                   "Load Arnold and reopen the scene?"),
        "info": tr("詳細はスクリプトエディターをご確認ください。",
                   "See the Script Editor for details."),
        "reopen": tr("Arnold を読み込んで開き直す", "Load Arnold and reopen"),
        "done": tr("Arnold を読み込んで開き直しました: %s", "Loaded Arnold and reopened: %s"),
        "modified": tr("開いた後に変更があるため、開き直していません: %s",
                       "Not reopened because the scene has changed since it was opened: %s"),
        "loadfail": tr("Arnold を読み込めませんでした。", "Could not load Arnold."),
    }


def ask_texts() -> dict:
    """Manager で聞く時の文言（r150）。«なぜ止まっているか» と «解除すると何が
    起きるか» を 1 行ずつ。ユーザー指摘「エラーがでたから読み取り専用を解除するの
    ロジックが普通わからない」により «読み取り専用» という言い方はしない。"""
    from core.i18n import tr
    return {
        "text": tr("読み込み中にエラーが起きたため、Maya がこのシーンの上書き保存を止めています。\n"
                   "上書き保存できるようにしますか？",
                   "Errors occurred while reading this scene, so Maya is blocking saving over it.\n"
                   "Allow saving over the file?"),
        "info": tr("読み込めなかった設定は、上書き保存するとファイルから消えます。\n"
                   "詳細はスクリプトエディターをご確認ください。",
                   "Settings that could not be read are removed from the file when you save over it.\n"
                   "See the Script Editor for details."),
        "yes": tr("上書き保存できるようにする", "Allow saving"),
        "no": tr("このままにする", "Leave as is"),
        "done": tr("上書き保存できるようにしました: %s", "Saving over the file is allowed: %s"),
    }


def open_stmt(escaped_path: str, indent: str = "") -> str:
    """SRC を先頭に置いた名前空間で使う «開く» の 1 文。結果は _mfm_result へ。"""
    return "%s_mfm_result = _mfm_open('%s', %r)\n" % (indent, escaped_path, texts())


def arnold_code(path: str) -> str:
    """«Arnold を読み込んで開き直す» を同じ Maya へ送る «単一の式»（r151）。"""
    from core.maya_bridge import escape_path
    inner = SRC + "_mfm_result = _mfm_arnold_reopen('%s', %r)\n" % (escape_path(path), texts())
    return "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result'))[1])({})" % inner


def parse_arnold(reply):
    """Arnold の開き直しの応答: "ok" / "modified" / "other" / "loadfail:..."。違えば None。"""
    s = str(reply or "").strip()
    return s[len(ARNOLD_PREFIX):] if s.startswith(ARNOLD_PREFIX) else None


def arnold_in_this_maya(path: str):
    """Maya の中で動いている時用。応答の文字列をそのまま返す。"""
    return eval(arnold_code(path), {})


def unlock_code(path: str) -> str:
    """«解除する» を選んだ時に同じ Maya へ送る «単一の式»。"""
    from core.maya_bridge import escape_path
    msg = ask_texts()["done"] % path.replace("\\", "/").rsplit("/", 1)[-1]
    inner = UNLOCK_SRC + "_mfm_result = _mfm_unlock('%s', %r)\n" % (escape_path(path), msg)
    return "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result'))[1])({})" % inner


def parse(reply):
    """応答が «エラー付きで開けた» なら (行数, 状態, パス, 印)。違えば None。
    状態は "pending"（Manager で聞く）。印は "a"（Arnold を読み込めば直る見込み）か "-"。
    r149 の応答（"saveable"/"kept"）、r150 の応答（印なし）も読める。"""
    s = str(reply or "").strip()
    if not s.startswith(RESULT_PREFIX):
        return None
    parts = s[len(RESULT_PREFIX):].split(":", 3)
    try:
        n = int(parts[0])
    except (ValueError, IndexError):
        n = 0
    choice = parts[1].strip() if len(parts) > 1 else "kept"
    flag = "-"
    if len(parts) > 3 and parts[2] in ("a", "-"):
        flag, path = parts[2], parts[3].strip()
    else:
        path = ":".join(parts[2:]).strip()
    return n, choice, path, flag


def parse_unlock(reply):
    """解除の応答: "ok" / "other"。違えば None。"""
    s = str(reply or "").strip()
    return s[len(UNLOCK_PREFIX):] if s.startswith(UNLOCK_PREFIX) else None


def status_text(name: str, info) -> str:
    """ステータスバー用の 1 行。info は parse() の戻り値（先頭 2 つを使う）。"""
    from core.i18n import tr
    n, choice = info[0], info[1]
    if choice == "saveable":
        tail = tr("上書き保存できるようにしました", "saving over the file is allowed")
    elif choice == "pending":
        tail = tr("詳細は Maya のスクリプトエディター", "details in the Maya Script Editor")
    else:
        tail = tr("上書き保存は止まったまま（Save Scene As なら保存できます）",
                  "saving over the file stays blocked (Save Scene As still works)")
    return tr("開きました: %s — 読み込み中に Maya のエラー %d 行。%s",
              "Opened: %s - %d lines of Maya errors while reading. %s") % (name, n, tail)


def open_in_this_maya(path: str):
    """Maya の中で動いている時用。戻り値は parse() と同じ形（エラー無しなら None）。
    開けなかった時は例外。"""
    ns = {}
    exec(SRC, ns)
    return parse(ns["_mfm_open"](path, texts()))


def unlock_in_this_maya(path: str):
    """Maya の中で動いている時用の «解除する»。戻り値は parse_unlock() と同じ。"""
    return parse_unlock(eval(unlock_code(path), {}))
