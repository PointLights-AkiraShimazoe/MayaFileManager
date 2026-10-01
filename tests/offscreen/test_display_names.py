# -*- coding: utf-8 -*-
"""r115: 表示名（実体名とは別にカラムへ出す名前）。

大前提: **パスに関わる処理は実体名のまま**。表示だけを差し替える。
隠しファイルが無いフォルダでは機能自体を動かさない（負荷軽減）。
"""
import os
from _common import *  # noqa: F401,F403
from _common import (app, make_panel, find_item_wait as find_item,
                     tmpdir, finish, run, Qt, QTest)
from core import display_names as dn

root = tmpdir()
REAL = ["CH002_cur", "old_bk", "zzz_wip"]
for n in REAL:
    os.makedirs(os.path.join(root, n))
open(os.path.join(root, "note.txt"), "w").close()

b = make_panel(1100, 650)
b.navigate_to(root)


def _settle(ms=400):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    # ── 1) ファイルが無い間は «素通し» ─────────────────────────────
    assert dn.load(root) is None, "ファイルが無いのにデータがある"
    assert dn.alias_map(root) is None
    cvw, _r, _i = find_item(b, "CH002_cur")
    assert cvw, "カラムに項目が出ていない"
    print("no file -> feature is off: OK")

    # ── 2) 表示名を設定するとカラムの表示だけ変わる ────────────────
    dn.save(root, {"CH002_cur": "キャラA 最新", "old_bk": ""})
    b._proxy.invalidate_display_names()
    b.navigate_to(os.path.dirname(root)); _settle(500)
    b.navigate_to(root); _settle(700)

    m = b._proxy
    names = {}
    src_root = b._fs_model.index(root)
    pidx = m.mapFromSource(src_root)
    for r in range(m.rowCount(pidx)):
        i = m.index(r, 0, pidx)
        si = m.mapToSource(i)
        names[b._fs_model.fileName(si)] = i.data(Qt.DisplayRole)
    assert names.get("CH002_cur") == "キャラA 最新", ("表示名が出ていない", names)
    assert names.get("old_bk") == "old_bk", ("空欄なのに変わった", names)
    assert names.get("zzz_wip") == "zzz_wip", names
    print("display name applied only where set: OK")

    # ── 3) 実体パスは一切変わらない（最重要） ──────────────────────
    for r in range(m.rowCount(pidx)):
        i = m.index(r, 0, pidx)
        si = m.mapToSource(i)
        p = b._fs_model.filePath(si)
        assert os.path.exists(p), ("実体パスが壊れた", p)
        assert os.path.basename(p) in REAL + ["note.txt",
                                              dn.FILE_NAME], p
    real_dir = os.path.join(root, "CH002_cur")
    assert os.path.isdir(real_dir), "実体フォルダが消えた"
    print("real paths untouched: OK")

    # ── 4) ツールチップに実体名が出る ──────────────────────────────
    tip = None
    for r in range(m.rowCount(pidx)):
        i = m.index(r, 0, pidx)
        if i.data(Qt.DisplayRole) == "キャラA 最新":
            tip = i.data(Qt.ToolTipRole)
    assert tip and "CH002_cur" in str(tip), ("ツールチップに実体名が無い", tip)
    print("tooltip reveals the real name: OK")

    # ── 5) スイッチで無効化すると実体名に戻る ──────────────────────
    dn.set_enabled(root, False)
    m.invalidate_display_names()
    assert dn.alias_map(root) is None, "無効化が効いていない"
    dn.set_enabled(root, True)
    assert dn.alias_map(root) == {"CH002_cur": "キャラA 最新"}
    print("enable switch works: OK")

    # ── 6) 削除すると機能ごと止まる ────────────────────────────────
    dn.remove(root)
    assert dn.load(root) is None and not dn.has_file(root)
    assert dn.display_for(root, "CH002_cur") == "CH002_cur"
    print("removing the file turns the feature off: OK")

    # ── 7) 右クリックメニューから起動できる ────────────────────────
    from core.compat import QMenu
    made = {}
    orig_exec = QMenu.exec if hasattr(QMenu, "exec") else QMenu.exec_
    orig_exec_ = QMenu.exec_

    def fake(self, *a, **k):
        made["menu"] = self
        return None
    if hasattr(QMenu, "exec"):
        QMenu.exec = fake
    QMenu.exec_ = fake
    try:
        b._popup_context_menu([os.path.join(root, "CH002_cur")],
                              b.mapToGlobal(b.rect().center()))
    finally:
        if hasattr(QMenu, "exec"):
            QMenu.exec = orig_exec
        QMenu.exec_ = orig_exec_
    menu = made.get("menu")
    assert menu is not None, "メニューが作られていない"
    act = [a for a in menu.actions() if "表示名" in a.text() or "Display" in a.text()]
    assert act and act[0].isEnabled(), \
        ("メニューに表示名の項目が無い", [a.text() for a in menu.actions()])
    assert b._display_name_target([os.path.join(root, "CH002_cur")]) == root, \
        "対象ディレクトリが違う"
    print("context menu entry present and targets the column dir: OK")

    # ── 8) カラム下部のスイッチは «ファイルがある時だけ» 出る ──────
    cv = b._column_view
    cv._refresh_display_names()          # 削除を画面へ反映（実経路と同じ）
    _settle(400)
    for v in cv._live_columns():
        assert getattr(v, "_mfm_dn_footer", None) is None, \
            "ファイルを消したのにフッタが残っている"
    dn.save(root, {"CH002_cur": "キャラA 最新"})
    cv._refresh_display_names()
    _settle(400)
    hit = [v for v in cv._live_columns()
           if getattr(v, "_mfm_dn_footer", None) is not None]
    assert hit, "ファイルがあるのにフッタが出ない"
    sw = getattr(hit[0], "_mfm_dn_switch", None)
    assert sw is not None and sw.isChecked(), "スイッチが有効状態でない"
    sw.setChecked(False)
    _settle(300)
    assert not dn.is_enabled(root), "スイッチで無効化されない"
    print("column footer appears only when the file exists: OK")
    finish(True)


run(s1, delay=900)
