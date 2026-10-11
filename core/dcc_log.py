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


# ---------------------------------------------------------------------------
# DCC 側で実行される本体
# ---------------------------------------------------------------------------
# r145: **時間窓による «二重実行ガード» を全廃した。**
#
# 旧実装は «直前に同じ操作が来たら実行しない» という時間窓（8 秒）だった。
# これは二重に間違っている:
#   1. ユーザーが «わざと» 同じ操作を 2 回続けた時まで握りつぶす。
#      しかも「二重実行を防止しました」という警告を出すので、
#      ユーザーには «勝手に実行されなかった» としか見えない（指摘 2026-10-08）。
#   2. そもそも «2 回届くこと» を前提にした対症療法で、
#      1 操作 = 1 実行 という本来の保証になっていない。
#
# 代わりに «操作 ID（_OPID）» で冪等にする。
#   - マネージャー側で 1 操作につき 1 個だけ ID を作る
#   - DCC 側は «実行前に» ID を記録し、同じ ID が再び届いたら黙って捨てる
# これで「配送が重複しても絶対に 2 回実行されない」かつ
# 「ユーザーが 2 回やったら 2 回実行される（ID が別だから）」が両立する。
# 警告も要らない（ユーザーの操作は 1 回しか起きていないのだから、
# 捨てたことは報告するような出来事ではない）。
_BODY = r"""
import __main__ as _mfm_main
_mfm_ops = _mfm_main.__dict__.setdefault("_mfm_seen_ops", [])
if _OPID and _OPID in _mfm_ops:
    _mfm_r = "Duplicate"
else:
    if _OPID:
        _mfm_ops.append(_OPID)
        del _mfm_ops[:-256]
    _l(_M["start"], "info")
    try:
        # «式なら eval / 文なら exec» はコンパイルで判定する。
        # eval を try して SyntaxError で exec にフォールバックすると、
        # «評価の最中に出た SyntaxError» でも本体をもう一度走らせてしまう。
        try:
            _mfm_c = compile(_CODE, "<mfm>", "eval")
            _mfm_eval = True
        except SyntaxError:
            _mfm_c = compile(_CODE, "<mfm>", "exec")
            _mfm_eval = False
        if _mfm_eval:
            _mfm_r = eval(_mfm_c)
        else:
            exec(_mfm_c)
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
"""


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
        except Exception:
            pass
    print(m)
'''


def new_op_id() -> str:
    """1 操作に 1 個だけ発行する ID（r145）。"""
    import uuid
    return uuid.uuid4().hex


def _wrap(code: str, action: str, path: str, logger: str, ns_expr: str,
          op_id: str = "") -> str:
    src = (logger
           + "_M = %r\n" % _messages(action, path)
           + "_CODE = %r\n" % code
           + "_OPID = %r\n" % (op_id or "")
           + _BODY)
    return "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_r'))[1])(%s)" % (src, ns_expr)


def wrap_maya(code: str, action: str, path: str, op_id: str = "") -> str:
    """Maya の commandPort へ送る式を、ログ＋冪等化で包む。

    op_id を渡すと «同じ ID の 2 通目» を Maya 側で捨てる（r145）。
    ユーザーが続けて 2 回実行した時は ID が別なので、両方とも実行される。"""
    return _wrap(code, action, path, _MAYA_LOGGER, "{}", op_id=op_id)


def wrap_blender(code: str, action: str, path: str, op_id: str = "") -> str:
    """Blender ブリッジへ送る式を、ログ＋冪等化で包む。
    ブリッジの名前空間（bpy / mfm_* ヘルパ）を引き継ぐため dict(globals())。"""
    return _wrap(code, action, path, _BLENDER_LOGGER, "dict(globals())",
                 op_id=op_id)


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
