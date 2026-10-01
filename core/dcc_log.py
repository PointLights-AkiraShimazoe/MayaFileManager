# -*- coding: utf-8 -*-
"""マネージャーからの操作を «DCC 側に» ログとして残す（r88）。

- Maya   : スクリプトエディタ（MGlobal.displayInfo / displayWarning / displayError）
- Blender: Info エディタ＋ステータスバー（ブリッジの mfm_log。古いブリッジでは
           システムコンソールへ print にフォールバック）

仕組み
------
送るコード（«単一の式»）を、次を行う式で包む:

    1) 開始行を出す   [MayaFileManager] リファレンス: D:/proj/chr_A.ma
    2) 元の式を評価する（戻り値はそのまま返す＝マネージャー側の挙動は不変）
    3) 結果に応じて 完了 / 失敗 / キャンセル / 確認待ち の行を出す
       - 例外 → 失敗（例外はそのまま再送出）
       - 戻り値が "Error:" / "Failed:" で始まる → 失敗
       - "Cancelled" → キャンセル / "confirm" → 確認待ち（Blender の未保存確認）

commandPort（Maya）も Blender ブリッジも «式» の値を返すので、exec を式に包み
名前空間から結果を取り出す（core/dcc_save.py と同じ形）。
"""
from core.diag import swallow as _swallow  # r112

PREFIX = "[MayaFileManager]"


def _messages(action: str, path: str):
    from core.i18n import tr
    name = (path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    shown = (path or "").replace("\\", "/")
    return {
        "start": "%s %s: %s" % (PREFIX, action, shown),
        "done": "%s %s: %s %s" % (PREFIX, tr("完了", "Done"), action, name),
        "fail": "%s %s: %s %s — " % (PREFIX, tr("失敗", "Failed"), action, name),
        "cancel": "%s %s: %s %s" % (PREFIX, tr("キャンセル", "Cancelled"), action, name),
        "confirm": "%s %s: %s %s（%s）" % (
            PREFIX, tr("確認待ち", "Waiting"), action, name,
            tr("未保存の変更があるため DCC 側で確認中",
               "unsaved changes — confirm in the DCC")),
    }


# 結果判定と出力（DCC 側で exec される本体。_l(msg, level) は DCC ごとに差し替え）
_BODY = r'''
_l(_M["start"], "info")
try:
    try:
        _mfm_r = eval(_CODE)
    except SyntaxError:
        exec(_CODE)
        _mfm_r = None
except Exception as _e:
    _l(_M["fail"] + str(_e), "error")
    raise
_s = "" if _mfm_r is None else str(_mfm_r)
_low = _s.strip().lower()
if _low.startswith("error:") or _low.startswith("failed:"):
    _l(_M["fail"] + _s.split(":", 1)[1].strip(), "error")
elif _low.startswith("cancelled"):
    _l(_M["cancel"], "warning")
elif _low == "confirm":
    _l(_M["confirm"], "info")
else:
    _l(_M["done"], "info")
'''

_MAYA_LOGGER = r'''
import maya.api.OpenMaya as _om
def _l(m, lv):
    try:
        if lv == "error":
            _om.MGlobal.displayError(m)
        elif lv == "warning":
            _om.MGlobal.displayWarning(m)
        else:
            _om.MGlobal.displayInfo(m)
    except Exception:
        print(m)
'''

# Blender: 新しいブリッジは mfm_log（Info エディタ）。古いブリッジには無いので print。
_BLENDER_LOGGER = r'''
_mfm_logf = globals().get("mfm_log")
def _l(m, lv):
    if _mfm_logf is not None:
        try:
            _mfm_logf(m, lv)
            return
        except Exception as _e:
            _swallow(_e, "core/dcc_log.py:88 _l")
    print(m)
'''


def _wrap(code: str, action: str, path: str, logger: str, ns_expr: str) -> str:
    src = (logger
           + "_M = %r\n" % _messages(action, path)
           + "_CODE = %r\n" % code
           + _BODY)
    return "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_r'))[1])(%s)" % (src, ns_expr)


def wrap_maya(code: str, action: str, path: str) -> str:
    """Maya の commandPort へ送る式を、スクリプトエディタへのログ付きに包む。"""
    return _wrap(code, action, path, _MAYA_LOGGER, "{}")


def wrap_blender(code: str, action: str, path: str) -> str:
    """Blender ブリッジへ送る式を、Info エディタへのログ付きに包む。
    ブリッジの名前空間（bpy / mfm_* ヘルパ）を引き継ぐため dict(globals())。"""
    return _wrap(code, action, path, _BLENDER_LOGGER, "dict(globals())")


def maya_local(message: str, level: str = "info"):
    """Maya 内起動（プロセス内実行）のときに直接スクリプトエディタへ出す。"""
    try:
        import maya.api.OpenMaya as om
        if level == "error":
            om.MGlobal.displayError(message)
        elif level == "warning":
            om.MGlobal.displayWarning(message)
        else:
            om.MGlobal.displayInfo(message)
    except Exception:
        print(message)


def maya_local_messages(action: str, path: str):
    return _messages(action, path)
