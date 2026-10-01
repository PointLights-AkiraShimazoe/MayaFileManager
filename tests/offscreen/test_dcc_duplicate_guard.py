# -*- coding: utf-8 -*-
"""r119e: 同じ操作が «2 回» DCC で実行されないこと。

実機報告 2026-10-02:
  「maya のシーンを開いた時に、開き終わったら再度コマンドが実行されそうに
    なっていませんか？ これは相当まずいです。」

commandPort は受け取ったデータを «後で» 処理する。Maya がシーンを読み込んで
いる間に届いた 2 通目は、読み込みが終わってから実行される。読み込みは数十秒
かかるので、送信側の «1.2 秒の窓» では止まらない。

対策は 2 枚:
  1. 送信側: 後戻りできない操作（open / run_script）は «前の送信が片付くまで»
     一切受け付けない。
  2. DCC 側: ラップしたコードが «同じ (操作, パス) を直前に受け取っていたら»
     実行せずに警告する。1 が破れてもここで止まる。
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run

from core.dcc_log import wrap_maya


def _run_wrapped(code_expr, shared):
    """DCC 側で実行されるのと同じように評価する（_l と __main__ を差し替え）。"""
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
    saved = {k: sys.modules.get(k)
             for k in ("maya", "maya.api", "maya.api.OpenMaya", "__main__")}
    main_mod = types.ModuleType("__main__")
    main_mod.__dict__.update(shared["main"])
    sys.modules["maya"] = fake_maya
    sys.modules["maya.api"] = fake_api
    sys.modules["maya.api.OpenMaya"] = fake_om
    sys.modules["__main__"] = main_mod
    try:
        return eval(code_expr)          # noqa: S307 — DCC と同じ評価
    finally:
        shared["main"] = main_mod.__dict__
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def s1():
    # ── 1) DCC 側ガード: 2 通目は実行されない ───────────────────────
    shared = {"log": [], "main": {}}
    ran = {"n": 0}
    import builtins
    builtins._mfm_test_ran = lambda: ran.__setitem__("n", ran["n"] + 1)
    body = "__import__('builtins')._mfm_test_ran()"

    code = wrap_maya(body, "開く", "C:/proj/scene.ma", guard=True)
    _run_wrapped(code, shared)
    assert ran["n"] == 1, ran
    assert any(lv == "info" for lv, _m in shared["log"])

    # 同じ操作がもう一度届いても «実行しない»
    shared["log"] = []
    _run_wrapped(code, shared)
    assert ran["n"] == 1, ("2 回目が実行された", ran)
    assert any(lv == "warning" and "二重" in m or "Duplicate" in m
               for lv, m in shared["log"]), shared["log"]
    print("the DCC-side guard refuses an identical second command: OK")

    # 別のファイルなら通る
    code2 = wrap_maya(body, "開く", "C:/proj/other.ma", guard=True)
    _run_wrapped(code2, shared)
    assert ran["n"] == 2, ran
    # 別の操作でも通る
    code3 = wrap_maya(body, "インポート", "C:/proj/scene.ma", guard=True)
    _run_wrapped(code3, shared)
    assert ran["n"] == 3, ran
    print("a different file or a different action still runs: OK")

    # **ガードを付けない操作は何度でも通る**。
    # インポートやリファレンスは «同じファイルを 2 回入れる» のが正当な操作
    # なので、塞いだら機能を壊す（r119e で範囲を開く/スクリプト実行に限定）。
    plain = wrap_maya(body, "リファレンス", "C:/proj/scene.ma")
    before = ran["n"]
    _run_wrapped(plain, shared)
    _run_wrapped(plain, shared)
    assert ran["n"] == before + 2, ("リファレンスまで塞いでいる", ran)
    print("additive actions (import/reference) are never blocked: OK")

    del builtins._mfm_test_ran

    # ── 2) 送信側ガード: open は «前が片付くまで» 受け付けない ───────
    import ui.main_window_dcc as mwd

    class _Bar:
        def showMessage(self, *_a, **_k):
            pass

    class _Host(mwd.MainWindowDccMixin):
        def __init__(self):
            self._sm = sm

        def statusBar(self):
            return _Bar()

    h = _Host()
    a, b = os.path.abspath("a.ma"), os.path.abspath("b.ma")

    def _send():
        """実際に DCC へ渡った時に起きること（予約→実行中への昇格）。"""
        import time as _t
        arm = getattr(h, "_dcc_arm", None)
        if arm:
            h._dcc_pending = (arm[0], _t.monotonic())
            h._dcc_arm = None

    assert h._dcc_once("open", a, "maya") is True
    _send()
    # 同じファイルは当然止まる
    assert h._dcc_once("open", a, "maya") is False
    # **別のファイルでも** 前の open が終わるまで止まる（ここが r119e）
    assert h._dcc_once("open", b, "maya") is False, \
        "前の open が終わっていないのに別のシーンを送っている"
    # 応答が返れば解放される
    h._on_bridge_done(True, "開く a.ma", "ok")
    assert h._dcc_once("open", b, "maya") is True
    _send()
    print("open is refused until the previous open reports back: OK")

    # 送らなかった場合は «実行中» にならない（Maya 内実行・途中で止めた時）。
    # 昇格を _bridge_send_async に置いたのは、印が残り続けて以後 2 分間
    # «開く» が効かなくなるのを防ぐため。
    h._on_bridge_done(True, "x", "ok")
    assert h._dcc_once("open", a, "maya") is True
    h._dcc_arm = None                      # ＝送らなかった
    assert h._dcc_once("open", b, "maya") is True, \
        "送っていないのに «実行中» 扱いになっている"
    h._dcc_arm = None
    print("not sending never leaves the hold stuck: OK")

    # import / reference は «重ねられる» 操作なので従来どおり 1.2 秒窓のみ
    h._on_bridge_done(True, "x", "ok")
    assert h._dcc_once("import", a, "maya") is True
    assert h._dcc_once("import", b, "maya") is True, \
        "インポートまで塞いでいる（重ねられる操作は止めない）"
    print("import/reference are not held back (they are additive): OK")
    finish(True)


run(s1, delay=200)
