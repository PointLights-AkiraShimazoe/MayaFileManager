# -*- coding: utf-8 -*-
"""プリセットのエリア独立/🔗リンク、Git連携（リンク経由）、非ワークスペースの不干渉。"""
import os, subprocess, time
from _common import *  # noqa: F401,F403
from _common import app, sm, tmpdir, finish, run, QTimer
from core.compat import Qt
from ui.main_window import MainWindow, QuickNavBar
from core.integrations import get_manager

sm.save_quick_nav_presets({"default": [{"label": "A", "path": "/tmp"}],
                           "MM": [{"label": "B", "path": "/tmp"}]})
w = MainWindow(sm); w.resize(1400, 900); w.show()
while len(w._areas) < 2:
    w._add_area()

repo = tmpdir()
def g(*a): subprocess.run(["git", "-C", repo] + list(a), check=True, capture_output=True)
g("init", "-q"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
os.makedirs(os.path.join(repo, "sub"))
for n in ("clean.ma", "mod.ma", "sub/inner.ma"):
    open(os.path.join(repo, n), "w").write("x")
g("add", "-A"); g("commit", "-q", "-m", "init")
open(os.path.join(repo, "mod.ma"), "a").write("y"); open(os.path.join(repo, "new.ma"), "w").write("n")
link = os.path.join(tmpdir(), "repo_link"); os.symlink(repo, link)
w._areas[0].browser.navigate_to(link)


def s1():
    # r68: ヘッダ行（メニュー＋DCCブロック＋動作ブロック）が生きている
    # （setMenuWidget 後の menuBar() 呼び出しでヘッダごと削除された回帰）
    assert w.menuWidget() is w._header_widget, "header widget replaced"
    assert w._dcc_hdr.isVisible() and w._action_blk.isVisible(), "header blocks hidden"
    assert w._menubar.parent() is w._header_widget and w._menubar.actions(), "menubar lost"
    w._on_conn_list([], "maya"); w._on_conn_list([], "blender")     # コンボが生存
    assert w._conn_combo.count() == 1 and w._bl_conn_combo.count() == 1
    assert not w._conn_combo.isEnabled(), "接続ゼロなのにコンボが有効"

    # r119: «コンボに実際に入ったか» まで見る。
    # 以前は emit された items しか検証しておらず、_on_conn_list の中で
    # 分岐を壊した（if/elif/else の付け替え）回帰を素通りさせた。
    # 症状は «Maya が一切接続リストに出ない» という全損。
    for dcc, combo in (("maya", w._conn_combo), ("blender", w._bl_conn_combo)):
        w._on_conn_list([(20261, "%s A (:20261)" % dcc, True, 111, dcc),
                         (20262, "%s B (:20262)" % dcc, True, 222, dcc)], dcc)
        assert combo.count() == 2, (dcc, combo.count())
        assert combo.isEnabled(), "%s: 接続があるのにコンボが無効" % dcc
        assert combo.itemData(0) == 20261 and combo.itemData(1) == 20262, dcc
        assert "A (:20261)" in combo.itemText(0), combo.itemText(0)
        if dcc == "maya":   # 案内は Maya 側だけ（Blender は元から別の説明を持つ）
            assert not combo.toolTip(), "接続があるのに案内が残っている"
        # 空に戻すと «なし» 1 件・無効・案内あり
        w._on_conn_list([], dcc)
        assert combo.count() == 1 and not combo.isEnabled(), dcc
    assert w._conn_combo.toolTip(), "接続ゼロの時の案内が出ていない"
    print("connection combo is actually populated / emptied: OK")
    # r78: 識別済みの接続は無応答（ビジー）でも一覧に残り、ポートが閉じたら消える
    import core.maya_bridge as _mb
    import ui.main_window as _mw
    w._known_conns["maya"] = {}
    seq = {"n": 0}
    def fake_send(self_, code, timeout=1.0):
        seq["n"] += 1
        return (True, "MFMID<2027><C:/x/scene.ma><123>MFMID") if seq["n"] == 1 else (True, None)
    # r118: _refresh_maya_connections の実装は r114 の分割で
    # ui.main_window_dcc へ移った。_mw 側だけ差し替えても効かない。
    import ui.main_window_dcc as _mwd
    orig_scan, orig_send = _mwd.scan_open_ports, _mb.MayaBridge.send_python
    _mwd.scan_open_ports = lambda *a, **k: [20261]
    _mb.MayaBridge.send_python = fake_send
    got = []
    w._bridge_notify.conn_list.connect(lambda items, dcc: got.append((list(items), dcc)))
    # r118: 固定 sleep だとワーカーの完了に間に合わず環境次第で落ちていた。
    # 期待件数が届くまで待つ（上限 6 秒）。
    def _wait(n):
        for _ in range(300):
            app.processEvents()
            if len([1 for _it, d in got if d == "maya"]) >= n:
                return
            time.sleep(0.02)
        app.processEvents()

    try:
        w._refresh_maya_connections(); _wait(1)
        w._refresh_maya_connections(); _wait(2)
        _mwd.scan_open_ports = lambda *a, **k: []
        w._refresh_maya_connections(); _wait(3)
    finally:
        _mwd.scan_open_ports, _mb.MayaBridge.send_python = orig_scan, orig_send
    maya_lists = [it for it, d in got if d == "maya"]
    assert len(maya_lists) >= 3, got
    first, second, third = maya_lists[0], maya_lists[1], maya_lists[2]
    assert first and first[0][2] is True and "scene.ma" in first[0][1], first
    assert second and second[0][2] is True and second[0][1].endswith("…") and second[0][3] == 123, second
    assert third == [], third
    print("busy Maya stays in the connection list until its port closes: OK")
    # r80: Store 版と通常インストール版（同じ版）が同居しても区別できるラベル
    from pathlib import Path as _P
    from core.blender_version import BlenderInstallation as _BI
    saved = w._blender_installs
    w._blender_installs = [
        _BI("5.2", _P(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")),
        _BI("5.2", _P(r"C:\Program Files\WindowsApps\BlenderFoundation.Blender_5.2.1.0_x64__x\Blender\blender.exe")),
    ]
    w._populate_version_combos()
    labels = [w._blender_combo.itemText(i) for i in range(w._blender_combo.count())]
    assert sorted(labels) == ["Blender 5.2", "Blender 5.2 (Store)"], labels
    assert all(w._blender_combo.itemData(i, Qt.ToolTipRole) for i in range(2))
    w._blender_installs = saved; w._populate_version_combos()
    print("Store and installer Blender of the same version are distinguishable: OK")
    # r69: 起動ボタンがヘッダ行（＝ウィンドウ幅）の中央に来る
    hw = w._header_widget
    lb = w._dcc_hdr.launch_btn
    cx = w._dcc_hdr.x() + lb.x() + lb.width() // 2
    assert abs(cx - hw.width() // 2) <= 3, (cx, hw.width())
    print("header row alive after build / launch centered: OK")
    qn1, qn2 = w._areas[0].quick_nav, w._areas[1].quick_nav
    sm.set("quick_nav_link_areas", False, save=False)
    qn1._link_btn.setChecked(False); qn2.set_active_preset("default")
    qn1._preset_combo.setCurrentText("MM")
    assert qn1.active_preset() == "MM" and qn2.active_preset() == "default"
    qn1._link_btn.setChecked(True)
    assert qn2._link_btn.isChecked() and "ON" in qn1._link_btn.text()
    qn2._preset_combo.setCurrentText("MM"); assert qn1.active_preset() == "MM"
    qn1._link_btn.setChecked(False); qn2.set_active_preset("default")
    QuickNavBar.refresh_all()
    assert qn1.active_preset() == "MM" and qn2.active_preset() == "default"
    assert [x.text() for x in qn1._buttons] == ["B"]
    print("presets independent / link / refresh_all: OK")
    mgr = get_manager()
    def st(n): return mgr.status_for(os.path.join(link, n))
    assert st("clean.ma") == ("git", "clean") and st("mod.ma") == ("git", "modified")
    assert st("new.ma") == ("git", "untracked") and st("sub") == ("git", "clean")
    groups = mgr.actions_for([os.path.join(link, "mod.ma")])
    assert groups and groups[0][0] == "Git"
    # r72: 複数フォルダにまたがる選択（平坦ビュー相当）でも、各ファイルは
    # «自分のフォルダ» の実体パスでプロバイダへ渡る（従来は paths[0] の
    # フォルダに basename を結合していて他フォルダのファイルが壊れていた）
    gitp = [p for p in mgr.providers if p.key == "git"][0]
    seen = {}
    orig_actions = gitp.actions
    gitp.actions = lambda root, ps: seen.setdefault("ps", list(ps)) or orig_actions(root, ps)
    try:
        mgr.request_status(os.path.join(link, "sub")); time.sleep(0.6); app.processEvents()
        groups = mgr.actions_for([os.path.join(link, "mod.ma"), os.path.join(link, "sub", "inner.ma")])
    finally:
        gitp.actions = orig_actions
    assert len(groups) == 1 and groups[0][0] == "Git", groups
    got = [p.replace("\\", "/") for p in seen["ps"]]
    assert len(got) == 2 and got[0].endswith("/mod.ma") and got[1].endswith("/sub/inner.ma"), got
    assert all(os.path.exists(p) for p in got), got
    print("actions_for across multiple folders keeps per-file real paths: OK")
    other = tmpdir(); open(os.path.join(other, "a.txt"), "w").close()
    mgr.request_status(other); time.sleep(0.6); app.processEvents()
    assert mgr.actions_for([os.path.join(other, "a.txt")]) == []
    # 未インストールのプロバイダが available になっていないこと（環境依存:
    # ユーザーPCには p4/svn が入っている場合があるので、検出結果と実体の整合だけ見る）
    s = mgr.summary()
    for key, prov in (("svn", "svn_provider"), ("p4", "p4_provider")):
        avail, info = s[key]
        assert isinstance(avail, bool), (key, avail)
        if avail:
            assert any(v for v in (info or {}).values()), ("available なのに検出情報が空", key, info)
    print("git integration via symlink / non-workspace / provider detection: OK")
    finish()


run(s1, delay=4500)
