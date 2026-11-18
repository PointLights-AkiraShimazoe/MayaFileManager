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
  * `file -rename <同じパス>` を 1 回通すと、素の保存が通るようになる。Manager の
    «シーンを保存» は rename してから保存するので、通っていた。
  * インポート・リファレンスでは起きない（例外にならず、保存も通る）。

そこで、開いた直後に Maya 側で «何が起きたか» と «どうするか» を聞く。
Maya 側の処理は 1 か所（SRC）に置き、外から送る時も Maya の中で動く時も同じ物を使う。
"""

RESULT_PREFIX = "OpenedWithErrors:"

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
    short = [re.sub(r"^file: .* line (\d+): ", r"line \1: ", l) for l in lines]
    body = "\n".join(short[:4])
    if len(short) > 4:
        body += "\n" + _t["more"] % (len(short) - 4)
    msg = _t["head"] % len(lines) + "\n\n" + body + "\n\n"
    if any(".ai_" in l for l in lines):
        msg += _t["arnold"] + "\n\n"
    msg += _t["why"] + "\n\n" + _t["ask"]
    choice = "kept"
    try:
        if not cmds.about(batch=True):
            ans = cmds.confirmDialog(title="Maya File Manager", message=msg,
                                     button=[_t["yes"], _t["no"]], defaultButton=_t["no"],
                                     cancelButton=_t["no"], dismissString=_t["no"])
            if ans == _t["yes"]:
                cmds.file(rename=cmds.file(q=True, sceneName=True) or _p)
                choice = "saveable"
    except Exception:
        pass
    return "OpenedWithErrors:%d:%s" % (len(lines), choice)
'''


def texts() -> dict:
    """Maya 側のダイアログの文言（Manager の表示言語で）。"""
    from core.i18n import tr
    return {
        "head": tr("シーンは開きましたが、読み込み中に Maya がエラーを出しました（%d 行）。",
                   "The scene was opened, but Maya reported errors while reading it (%d lines)."),
        "more": tr("…ほか %d 行（全文は Script Editor）",
                   "...and %d more lines (see the Script Editor)"),
        "arnold": tr("「.ai_」で始まるのは Arnold（mtoa）のアトリビュートです。"
                     "Arnold を読み込んだ Maya で開き直すと、このエラーは出ません。",
                     "Names starting with \".ai_\" are Arnold (mtoa) attributes. "
                     "Open the scene in a Maya with Arnold loaded and these errors go away."),
        "why": tr("この状態では、Maya の Save Scene は「A file must be given a name before "
                  "saving」と出て保存できません（読み込めなかった設定を、上書き保存で消して"
                  "しまわないための Maya の動きです）。",
                  "In this state Maya's Save Scene stops with \"A file must be given a name "
                  "before saving\" (Maya does this so the settings it could not read are not "
                  "lost by saving over the file)."),
        "ask": tr("上書き保存できるようにしますか？\n保存すると、読み込めなかった設定は"
                  "ファイルから消えます。",
                  "Allow saving over this file?\nOnce saved, the settings that could not "
                  "be read are gone from the file."),
        "yes": tr("保存できるようにする", "Allow saving"),
        "no": tr("そのままにする", "Leave as is"),
    }


def open_stmt(escaped_path: str, indent: str = "") -> str:
    """SRC を先頭に置いた名前空間で使う «開く» の 1 文。結果は _mfm_result へ。"""
    return "%s_mfm_result = _mfm_open('%s', %r)\n" % (indent, escaped_path, texts())


def parse(reply):
    """応答が «エラー付きで開けた» なら (行数, "saveable" / "kept")。違えば None。"""
    s = str(reply or "").strip()
    if not s.startswith(RESULT_PREFIX):
        return None
    parts = s[len(RESULT_PREFIX):].split(":")
    try:
        n = int(parts[0])
    except (ValueError, IndexError):
        n = 0
    return n, (parts[1].strip() if len(parts) > 1 else "kept")


def status_text(name: str, info) -> str:
    """ステータスバー用の 1 行。"""
    from core.i18n import tr
    n, choice = info
    if choice == "saveable":
        tail = tr("上書き保存できるようにしました", "saving over the file is allowed")
    else:
        tail = tr("このままでは Maya の Save Scene は止まります（Save Scene As か "
                  "Manager の «シーンを保存» で保存できます）",
                  "Maya's Save Scene will stop as is (use Save Scene As or "
                  "Manager's \"Save Scene\")")
    return tr("開きました: %s — 読み込み中に Maya のエラー %d 行。%s",
              "Opened: %s - %d lines of Maya errors while reading. %s") % (name, n, tail)


def open_in_this_maya(path: str):
    """Maya の中で動いている時用。戻り値は parse() と同じ形（エラー無しなら None）。
    開けなかった時は例外。"""
    ns = {}
    exec(SRC, ns)
    return parse(ns["_mfm_open"](path, texts()))
