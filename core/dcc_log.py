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
        # r119e: 二重実行ガード用。«同じ操作か» の判定キーと、止めた時の文言。
        "key": "%s|%s" % (action, shown),
        "dup": "%s %s: %s %s（%s）" % (
            PREFIX, tr("二重実行を防止", "Duplicate blocked"), action, name,
            tr("直前に同じ操作を受け取っているため実行しませんでした",
               "the same action arrived moments ago, so it was not run")),
    }


# 結果判定と出力（DCC 側で exec される本体。_l(msg, level) は DCC ごとに差し替え）
# r119e: **DCC 側の二重実行ガード**。
# commandPort は受け取ったデータを «後で» 処理するため、Maya がシーンを
# 読み込んでいる間に届いた 2 通目は «読み込みが終わってから» 実行される。
# 送信側のガードをすり抜けた分をここで止める（実機報告 2026-10-02:
# シーンを開き終わった直後にもう一度コマンドが走りかけた）。
#
# 判定は «前回の完了からの経過» で行う。開始時刻で測ると、読み込みに 60 秒
# かかった場合に «直後に走る 2 通目» が窓から外れて素通りしてしまう。
# 溜まっていた 2 通目は «完了の直後» に走るので、窓は短くてよい。
# 短いことが重要で、«わざと同じシーンを開き直す» のは塞がない。
_GUARD_SEC = 8.0

_GUARD = r"""
import time as _mfm_time, __main__ as _mfm_main
_mfm_seen = _mfm_main.__dict__.setdefault("_mfm_recent_ops", {})
_mfm_key = _M.get("key") or _M["start"]
_mfm_now = _mfm_time.monotonic()
for _k in [k for k, t in _mfm_seen.items() if _mfm_now - t > 600.0]:
    _mfm_seen.pop(_k, None)
_mfm_last = _mfm_seen.get(_mfm_key)
_mfm_dup = _mfm_last is not None and (_mfm_now - _mfm_last) < %r
""" % _GUARD_SEC

# 結果判定と出力（DCC 側で exec される本体。_l(msg, level) は DCC ごとに差し替え）
_BODY = r"""
if _mfm_dup:
    _l(_M["dup"], "warning")
    _mfm_r = "Cancelled: duplicate"
else:
 _l(_M["start"], "info")
 try:
    try:
        _mfm_r = eval(_CODE)
    except SyntaxError:
        exec(_CODE)
        _mfm_r = None
 except Exception as _e:
    _mfm_seen[_mfm_key] = _mfm_time.monotonic()
    _l(_M["fail"] + str(_e), "error")
    raise
 _mfm_seen[_mfm_key] = _mfm_time.monotonic()   # «完了» 時刻を記録する
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
        except Exception as _e:
            _swallow(_e, "core/dcc_log.py:88 _l")
    print(m)
'''


# ガードを付けない時も、本体が «完了時刻の記録» で参照する名前は必要。
# 記録先をその場限りの dict にして、何も残らないようにする。
_NO_GUARD = ("import time as _mfm_time\n"
             "_mfm_dup = False\n"
             "_mfm_seen = {}\n"
             "_mfm_key = None\n")


def _wrap(code: str, action: str, path: str, logger: str, ns_expr: str,
          guard: bool = False) -> str:
    src = (logger
           + "_M = %r\n" % _messages(action, path)
           + "_CODE = %r\n" % code
           + (_GUARD if guard else _NO_GUARD)
           + _BODY)
    return "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_r'))[1])(%s)" % (src, ns_expr)


def wrap_maya(code: str, action: str, path: str, guard: bool = False) -> str:
    """Maya の commandPort へ送る式を、スクリプトエディタへのログ付きに包む。

    guard=True で «直前に同じ操作が走っていたら実行しない» 栓を付ける（r119e）。
    **「開く」「スクリプト実行」だけに付けること。** インポートやリファレンスは
    同じファイルを続けて 2 回入れるのが正当な操作なので、塞いではいけない。
    """
    return _wrap(code, action, path, _MAYA_LOGGER, "{}", guard=guard)


def wrap_blender(code: str, action: str, path: str, guard: bool = False) -> str:
    """Blender ブリッジへ送る式を、Info エディタへのログ付きに包む。
    ブリッジの名前空間（bpy / mfm_* ヘルパ）を引き継ぐため dict(globals())。"""
    return _wrap(code, action, path, _BLENDER_LOGGER, "dict(globals())",
                 guard=guard)


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
