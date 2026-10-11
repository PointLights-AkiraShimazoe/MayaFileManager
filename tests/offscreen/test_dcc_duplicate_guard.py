# -*- coding: utf-8 -*-
"""r145: 1 操作 = 1 実行を «操作 ID» で保証する。

経緯:
  r119e では «同じ (操作, パス) を直前に受け取っていたら実行しない» という
  時間窓（8 秒）のガードを DCC 側に置いていた。これは二重に間違っていた。

  1. ユーザーが «わざと» 同じ操作を 2 回続けた時まで握りつぶす。しかも
     「二重実行を防止しました」と警告を出すので、ユーザーからは «勝手に
     実行されなかった» としか見えない（指摘 2026-10-08）。
  2. そもそも «2 回届くこと» を前提にした対症療法で、1 操作 = 1 実行 という
     本来の保証になっていない。

  r145 で操作 ID（_OPID）による冪等化に置き換えた。
    - マネージャーは 1 操作につき 1 個だけ ID を発行する
    - DCC 側は «実行前に» ID を記録し、同じ ID が再び届いたら黙って捨てる

ここで固定すること:
  1) 同じ ID が 2 回届いても実行は 1 回きり
  2) ID が違えば（＝ユーザーが 2 回やったなら）2 回とも実行される
  3) 同じファイルへの連続実行も、ID が違うので止まらない
  4) 捨てた時に «警告» を出さない（ユーザーの操作は 1 回しか起きていない）
  5) 操作名やパスは判定に使わない（キーは ID だけ）
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run

from core.dcc_log import wrap_maya, new_op_id


_FAKE_MAIN = None


def _run_wrapped(code_expr, shared):
    """DCC 側で実行されるのと同じように評価する（_l と __main__ を差し替え）。

    `__main__` は **呼び出しをまたいで同じもの** を使う。実機の Maya では
    当然ひとつしかなく、ID の記録はそこに置かれる。
    """
    global _FAKE_MAIN
    import sys
    import types
    fake_om = types.ModuleType("maya.api.OpenMaya")

    class _MG:
        @staticmethod
        def displayInfo(m):
            shared["log"].append(("info", m))

        @staticmethod
        def displayWarning(m):
            shared["log"].append(("warning", m))

        @staticmethod
        def displayError(m):
            shared["log"].append(("error", m))
    fake_om.MGlobal = _MG
    fake_maya = types.ModuleType("maya")
    fake_api = types.ModuleType("maya.api")
    fake_api.OpenMaya = fake_om
    fake_maya.api = fake_api
    if _FAKE_MAIN is None:
        _FAKE_MAIN = types.ModuleType("__main__")
    saved = {k: sys.modules.get(k)
             for k in ("maya", "maya.api", "maya.api.OpenMaya", "__main__")}
    sys.modules["maya"] = fake_maya
    sys.modules["maya.api"] = fake_api
    sys.modules["maya.api.OpenMaya"] = fake_om
    sys.modules["__main__"] = _FAKE_MAIN
    try:
        return eval(code_expr)          # noqa: S307 — DCC と同じ評価
    finally:
        shared["main"] = _FAKE_MAIN.__dict__
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def s1():
    global _FAKE_MAIN
    _FAKE_MAIN = None                  # 共有 __main__ を作り直す
    shared = {"log": [], "main": {}}
    ran = {"n": 0}
    import builtins
    builtins._mfm_test_ran = lambda: ran.__setitem__("n", ran["n"] + 1)
    body = "__import__('builtins')._mfm_test_ran()"

    # ── 1) 同じ ID の 2 通目は実行しない ─────────────────────────────
    op1 = new_op_id()
    code = wrap_maya(body, "開く", "C:/proj/scene.ma", op_id=op1)
    _run_wrapped(code, shared)
    assert ran["n"] == 1, ran
    assert any(lv == "info" for lv, _m in shared["log"]), shared["log"]

    shared["log"] = []
    res = _run_wrapped(code, shared)
    assert ran["n"] == 1, ("同じ ID の 2 通目が実行された", ran)
    assert res == "Duplicate", res
    # ── 4) 捨てた時に警告を出さない ─────────────────────────────────
    assert not shared["log"], ("重複配送を捨てた時に DCC へ出力している",
                               shared["log"])
    print("the same op id runs once, silently: OK")

    # ── 2) ID が違えば実行される（＝ユーザーが 2 回やった）──────────
    code2 = wrap_maya(body, "開く", "C:/proj/scene.ma", op_id=new_op_id())
    _run_wrapped(code2, shared)
    assert ran["n"] == 2, ("連続した同じ操作が止められた", ran)
    print("a second user action on the same file runs again: OK")

    # ── 3) 操作名／パスは判定に使わない ─────────────────────────────
    #     同じ ID なら操作名やパスが違っても «同じ操作の再配送» として捨てる
    dup_other = wrap_maya(body, "インポート", "C:/proj/other.ma", op_id=op1)
    _run_wrapped(dup_other, shared)
    assert ran["n"] == 2, ("ID が同じなのに実行された", ran)
    # 逆に ID が無ければ毎回実行する（ログ目的だけのラップ）
    plain = wrap_maya(body, "リファレンス", "C:/proj/scene.ma")
    _run_wrapped(plain, shared)
    _run_wrapped(plain, shared)
    assert ran["n"] == 4, ("ID 無しのラップが止められた", ran)
    print("the op id is the only key; no id means no dedupe: OK")

    # ── 5) 記録は無制限に増えない ───────────────────────────────────
    for _ in range(300):
        _run_wrapped(wrap_maya("None", "開く", "C:/x.ma", op_id=new_op_id()),
                     shared)
    seen = shared["main"].get("_mfm_seen_ops", [])
    assert len(seen) <= 256, ("ID の記録が無制限に増える", len(seen))
    print("the seen-op list stays bounded: OK")

    # ── 6) 評価の «最中» に SyntaxError が出ても本体を二度走らせない ──
    #     旧実装は eval(_CODE) を try して SyntaxError なら exec(_CODE) に
    #     落ちていた。リファレンス／インポートの本文は «中で更に exec する»
    #     形なので、内側が SyntaxError を出すと丸ごともう一度走っていた。
    hit = {"n": 0}
    builtins._mfm_test_hit = lambda: hit.__setitem__("n", hit["n"] + 1)
    bad = wrap_maya("(__import__('builtins')._mfm_test_hit(), exec('def ('))",
                    "開く", "C:/proj/bad.ma", op_id=new_op_id())
    try:
        _run_wrapped(bad, shared)
    except SyntaxError:
        pass
    except Exception:
        pass
    assert hit["n"] == 1, ("評価中の SyntaxError で本体が二度走った", hit)
    print("a SyntaxError raised during evaluation never re-runs the body: OK")

    # ── 7) 旧実装の痕跡が残っていない ───────────────────────────────
    import core.dcc_log as dl
    src = open(dl.__file__, encoding="utf-8").read()
    assert "_GUARD" not in src, "時間窓ガードの残骸がある"
    assert '"dup"' not in src, "「二重実行を防止」の文言が残っている"
    assert "except SyntaxError:\n        exec(_CODE)" not in src, \
        "eval→except SyntaxError→exec(_CODE) の二重実行経路が残っている"
    print("no leftovers from the time-window guard: OK")

    finish(True)


run(s1, delay=200)
