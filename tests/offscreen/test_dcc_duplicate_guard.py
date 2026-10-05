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


_FAKE_MAIN = None


def _run_wrapped(code_expr, shared):
    """DCC 側で実行されるのと同じように評価する（_l と __main__ を差し替え）。

    r119i: `__main__` は **呼び出しをまたいで同じもの** を使う。
    実機の Maya では当然ひとつしかなく、ガードはそこに «実行中» の印を
    置く。呼び出しごとに作り直すと、入れ子（確認ダイアログの最中に
    2 通目が届く）を再現できない。
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
    # ── 1) DCC 側ガード: 2 通目は実行されない ───────────────────────
    global _FAKE_MAIN
    _FAKE_MAIN = None                  # 共有 __main__ を作り直す
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

    # ── 1b) **実行中** に届いた分も止める（r119i）───────────────────
    # confirmDialog は入れ子のイベントループを回すので、ダイアログを出して
    # いる最中に届いた 2 通目が «ダイアログの中で» 実行される。シーンを開く
    # 処理が «開く確認の最中» に走ると Maya ごと固まる（実機 2026-10-02）。
    shared2 = {"log": [], "main": {}}
    _FAKE_MAIN.__dict__.pop("_mfm_recent_ops", None)   # 直前の記録は捨てる
    inner_ran = {"n": 0}
    nested = wrap_maya("__import__('builtins')._mfm_nested()", "開く",
                       "C:/proj/busy.ma", guard=True)

    def _simulate_dialog():
        """«確認ダイアログの最中に 2 通目が届く» を再現する。"""
        inner_ran["n"] += 1
        _run_wrapped(nested, shared2)      # 入れ子で同じコマンドが走る
    builtins._mfm_nested = _simulate_dialog
    try:
        _run_wrapped(nested, shared2)
    finally:
        del builtins._mfm_nested
    assert inner_ran["n"] == 1, ("入れ子で本体が 2 回走った", inner_ran)
    assert any(lv == "warning" for lv, _m in shared2["log"]), shared2["log"]
    print("a command arriving while one is running is refused: OK")

    # 終わったら «実行中» の印は残らない（残ると以後ずっと塞がれる）
    assert not _FAKE_MAIN.__dict__.get("_mfm_running_ops"), \
        ("実行が終わったのに «実行中» の印が残っている",
         _FAKE_MAIN.__dict__.get("_mfm_running_ops"))
    print("the running mark is cleared when the command finishes: OK")

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
    # **応答なし（reply=None）は «終わった» ではない**（r119i）。
    # DCC が読み込み中／確認ダイアログ待ちのまま次を送れてしまうと、
    # ダイアログの入れ子ループの中でシーン読み込みが走って Maya が固まる。
    h._on_bridge_done(True, "開く a.ma", None)
    assert h._dcc_once("open", b, "maya") is False, \
        "応答が無い（まだ処理中）のに次の open を通している"
    # 実際に応答が返れば解放される
    h._on_bridge_done(True, "開く a.ma", "ok")
    assert h._dcc_once("open", b, "maya") is True
    _send()
    # 送信自体に失敗した時は実行されていないので解放する
    # （_Host は QWidget ではないのでエラーダイアログだけ抑える）
    from core.compat import QMessageBox as _QMB
    _orig_crit = _QMB.critical
    _QMB.critical = staticmethod(lambda *a, **k: None)
    try:
        h._on_bridge_done(False, "開く b.ma", "Mayaに接続できません")
    finally:
        _QMB.critical = _orig_crit
    assert h._dcc_once("open", a, "maya") is True
    _send()
    h._on_bridge_done(True, "x", "ok")
    print("open is refused until the previous open really reports back: OK")

    # 送らなかった場合は «実行中» にならない（Maya 内実行・途中で止めた時）。
    # 昇格を _bridge_send_async に置いたのは、印が残り続けて以後 2 分間
    # «開く» が効かなくなるのを防ぐため。
    h._on_bridge_done(True, "x", "ok")
    # 直前に使ったパスは «1.2 秒窓» に掛かるので別のファイルで確かめる
    c, d = os.path.abspath("c.ma"), os.path.abspath("d.ma")
    assert h._dcc_once("open", c, "maya") is True
    h._dcc_arm = None                      # ＝送らなかった
    assert h._dcc_once("open", d, "maya") is True, \
        "送っていないのに «実行中» 扱いになっている"
    h._dcc_arm = None
    print("not sending never leaves the hold stuck: OK")

    # import / reference は «重ねられる» 操作なので従来どおり 1.2 秒窓のみ
    h._on_bridge_done(True, "x", "ok")
    assert h._dcc_once("import", a, "maya") is True
    assert h._dcc_once("import", b, "maya") is True, \
        "インポートまで塞いでいる（重ねられる操作は止めない）"
    print("import/reference are not held back (they are additive): OK")

    # ── 3) r120: 応答が返らない «開く» の後、印が永久に残らないこと ────
    # 実機報告 2026-10-02:
    #   「一度 maya で開くと、それ以降 maya への命令が飛んでいないようです。
    #     マネージャーでは繋がっている様な表示なので、たちが悪いです。」
    # 「開く」は読み込みに数十秒かかるので応答を待たない（reply=None）。
    # r119i で «reply=None では解除しない» としたため、解除する機会が
    # 二度と来ず、以後ずっと «前の〜がまだ終わっていない» になっていた。
    # DCC が «また応答するようになった» ことを見張って解除する。
    h._on_bridge_done(True, "x", "ok")
    e, f = os.path.abspath("e.ma"), os.path.abspath("f.ma")
    assert h._dcc_once("open", e, "maya") is True
    _send()
    h._dcc_pending_port = 20261
    h._on_bridge_done(True, "開く e.ma", None)      # 応答なし＝まだ処理中
    assert h._dcc_once("open", f, "maya") is False, "処理中なのに通している"

    # 見張りが «まだビジー» と答えている間は塞がれたまま
    h._on_dcc_idle(20261, False)
    assert h._dcc_once("open", f, "maya") is False, \
        "ビジーの報告で解除してしまっている"
    # 別のポートが応答しても解除しない（別の Maya の話）
    h._on_dcc_idle(20262, True)
    assert h._dcc_once("open", f, "maya") is False, \
        "関係ないポートの応答で解除してしまっている"
    # 送った先の DCC が応答を返したら＝前の操作は終わっている → 解除
    h._on_dcc_idle(20261, True)
    assert h._dcc_once("open", f, "maya") is True, \
        "DCC が応答を返しても «実行中» の印が落ちない（永久に送れなくなる）"
    _send()
    h._on_bridge_done(True, "x", "ok")
    print("the hold is released once the DCC answers again: OK")

    # 最後の歯止め: 保持時間を超えたら見張りが落とす
    g = os.path.abspath("g.ma")
    assert h._dcc_once("open", g, "maya") is True
    _send()
    import time as _t2
    h._dcc_pending = (h._dcc_pending[0],
                      _t2.monotonic() - mwd.DESTRUCTIVE_HOLD_SEC - 1)
    h._dcc_pending_port = 20261
    h._poll_dcc_idle()
    assert h._dcc_pending[0] is None, "保持時間を超えても印が残っている"
    print("the hold always expires at the cap: OK")
    finish(True)


run(s1, delay=200)
