# -*- coding: utf-8 -*-
"""r90: DCC のウィンドウへの D&D は «Manager がブリッジ経由で» 処理し、
Windows の同期ドロップ（落とし先の処理が終わるまでドラッグ元が止まる）で
Manager が固まらないこと。install.py は Maya と同じ作法で実行されること。"""
import os
import sys
import types
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, run, sm
import ui.browser_panel as bp
import ui.browser_column_view as bcv   # r118: 分割後の実装先（patch はこちらへ）

b = make_panel()
cv = b._column_view
d = tmpdir()
PY = os.path.join(d, "install.py")
MA = os.path.join(d, "chr_A.ma")
PNG = os.path.join(d, "tex.png")
with open(PY, "w", encoding="utf-8") as f:
    f.write("import os\nRAN = []\n"
            "def onMayaDroppedPythonFile(*a):\n"
            "    import __main__\n"
            "    __main__.__dict__['_mfm_test_installed'] = os.path.basename(__file__)\n")
for p in (MA, PNG):
    open(p, "w").close()


def test_mime_serves_by_target():
    cur = {"app": None, "take": []}
    m = bp._DccAwareMime([MA], lambda: (cur["app"], cur["take"]))
    assert m.hasUrls() and len(m.urls()) == 1, "自アプリ／Explorer には実ファイル"
    cur.update(app="maya", take=[MA])
    assert m.urls() == [], "Manager が引き受ける時は DCC に渡さない"
    assert m.takeover_app == "maya"
    m2 = bp._DccAwareMime([MA], lambda: ("maya", []))
    assert len(m2.urls()) == 1 and m2.served_real_to_dcc, "引き受けない時は DCC に任せる"
    m3 = bp._DccAwareMime([PY, PNG], lambda: ("maya", [PY]))
    got = [u.toLocalFile() for u in m3.urls()]
    assert [os.path.basename(x) for x in got] == ["tex.png"] and m3.handled == [PY], \
        "引き受けた分だけ隠し、残りは DCC に渡す"
    print("mime hides files from a DCC only when the manager takes over: OK")


class _W:
    from ui.main_window import MainWindow as _M
    _can_take_over_drop = _M._can_take_over_drop
    _maya_run_script = _M._maya_run_script
    _dcc_once = _M._dcc_once
    _dcc_accepts = _M._dcc_accepts

    def __init__(self, connected=True, action="open"):
        self._inside_maya = False
        self._sm = types.SimpleNamespace(get=lambda k, dflt=None: action if k == "dnd_action" else dflt)
        br = types.SimpleNamespace(is_connected=lambda timeout=0: connected)
        self._bridge = br; self._bl_bridge = br
        self.sent = []

    def statusBar(self):
        return types.SimpleNamespace(showMessage=lambda *a, **k: None)

    def _maya_send_or_prompt(self, code, label, log=None):
        self.sent.append(code); return True

    def _focus_connected_maya(self):
        pass


def test_takeover_decision():
    """r91: 戻り値は «Manager が引き受けるファイル» の一覧。各ファイルは
    実行するコマンドの対象かで判定（.py に «開く» は送らない）。"""
    w = _W(connected=True, action="open")
    assert w._can_take_over_drop([PY], "maya") == [PY], ".py はスクリプト実行として引き受ける"
    assert w._can_take_over_drop([MA], "maya") == [MA], "開くの対象 .ma は引き受ける"
    assert w._can_take_over_drop([PNG], "maya") == [], "対象外は DCC に任せる"
    assert w._can_take_over_drop([PY, MA, PNG], "maya") == [PY, MA], "混在は対象分だけ"
    assert _W(connected=False)._can_take_over_drop([PY], "maya") == [], \
        "未接続なら DCC に任せる"
    assert _W(action="none")._can_take_over_drop([PY], "maya") == [PY]
    assert _W(action="none")._can_take_over_drop([MA], "maya") == []
    assert _W(action="open")._can_take_over_drop([PY], "blender") == [], \
        "Blender は .py を «開く» 対象にしない"
    print("takeover decision is per file and per command: OK")


def test_run_script_like_maya():
    w = _W()
    w._maya_run_script(PY)
    assert w.sent, "送信されていない"
    # 偽 maya（executeDroppedPythonFile が無い版 → 同等処理にフォールバック）
    for k in [m for m in sys.modules if m == "maya" or m.startswith("maya.")]:
        sys.modules.pop(k)
    import __main__
    __main__.__dict__.pop("_mfm_test_installed", None)
    res = eval(w.sent[-1], {})
    assert res is None, ("失敗扱いになった", res)
    assert __main__.__dict__.get("_mfm_test_installed") == "install.py", \
        "onMayaDroppedPythonFile が呼ばれていない"
    print("dropped install.py runs onMayaDroppedPythonFile (like Maya): OK")


def test_routing_and_notify():
    calls = []
    b._dnd_callback = lambda action, paths, app: calls.append((action, [os.path.basename(p) for p in paths], app))
    sm.set("dnd_action", "open")
    b._handle_maya_drop([PY, MA], "maya")
    assert ("run_script", ["install.py"], "maya") in calls and ("open", ["chr_A.ma"], "maya") in calls, calls
    # ネイティブに処理させた（実ファイルを渡した）場合は Manager から送らない
    fired = []
    cv._maya_drop_cb = lambda paths, app: fired.append(app)
    # r118: _notify_drag_finished は ui.browser_column_view にある。
    # bp 側だけ差し替えても効かない（r110 の分割で実装が移った）。
    orig_w, orig_c = bcv.QApplication.widgetAt, bcv._cursor_over_dcc_window
    bcv.QApplication.widgetAt = staticmethod(lambda *a: None)
    bcv._cursor_over_dcc_window = lambda: "maya"
    try:
        m = bp._DccAwareMime([MA], lambda: ("maya", [])); m.urls()
        cv._notify_drag_finished([MA], m)
        assert not fired, "DCC がネイティブ処理したのに Manager からも送った（二重）"
        m2 = bp._DccAwareMime([PY], lambda: ("maya", [PY])); m2.urls()
        m2._mfm_decide = lambda: ("maya", [PY])
        cv._notify_drag_finished([PY], m2)
        assert fired == ["maya"], "引き受けた落下が実行されない"
    finally:
        bcv.QApplication.widgetAt = orig_w
        bcv._cursor_over_dcc_window = orig_c
    print("routing (.py -> run script) and no double execution: OK")


def step():
    test_mime_serves_by_target()
    test_takeover_decision()
    test_run_script_like_maya()
    test_routing_and_notify()
    finish(True)


run(step, delay=300)
