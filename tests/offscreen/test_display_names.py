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
    assert tip and str(tip).strip() == "CH002_cur", \
        ("ツールチップは実体名だけにする（見出し・余分な行を付けない）", repr(tip))
    assert "\n" not in str(tip), ("ツールチップが複数行になっている", repr(tip))
    print("tooltip shows just the real name, single line: OK")

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

    # ── 7b) «空白の右クリック» メニューにも入っている（r116） ──────
    made.clear()
    if hasattr(QMenu, "exec"):
        QMenu.exec = fake
    QMenu.exec_ = fake
    try:
        b._popup_folder_context_menu(root, b.mapToGlobal(b.rect().center()))
    finally:
        if hasattr(QMenu, "exec"):
            QMenu.exec = orig_exec
        QMenu.exec_ = orig_exec_
    menu2 = made.get("menu")
    assert menu2 is not None, "空白メニューが作られていない"
    act2 = [a for a in menu2.actions()
            if "表示名" in a.text() or "Display" in a.text()]
    assert act2 and act2[0].isEnabled(), \
        ("空白メニューに表示名が無い", [a.text() for a in menu2.actions()])
    print("empty-space menu also has the entry: OK")

    # ── 7c) 共通入口は «失敗を黙らせない» ──────────────────────────
    from core.compat import QMessageBox, QDialog
    shown = {}
    oi = QMessageBox.information
    QMessageBox.information = staticmethod(
        lambda *a, **k: shown.setdefault("info", a[2] if len(a) > 2 else ""))
    try:
        b._open_display_names("")                 # 対象不明
    finally:
        QMessageBox.information = oi
    assert shown.get("info"), "対象不明でも無言だった"
    print("bad target reports instead of doing nothing: OK")

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
    # r119: «実際にクリックする»。以前は setChecked でしか検証しておらず、
    # クリック経路（保存の失敗で元に戻る）を取りこぼしていた。
    QTest.mouseClick(sw, Qt.LeftButton)
    _settle(300)
    assert not sw.isChecked(), "クリックでスイッチが切り替わらない"
    assert not dn.is_enabled(root), "スイッチで無効化されない（保存が効いていない）"
    QTest.mouseClick(sw, Qt.LeftButton)
    _settle(300)
    assert sw.isChecked() and dn.is_enabled(root), "クリックで戻せない"
    print("column footer appears only when the file exists, and its switch "
          "survives a real click: OK")

    # ── 6) 既存ファイルへの «2 回目以降» の保存（r119 の本丸）───────
    # Windows では隠し属性の付いた既存ファイルを open(..., "w") できず
    # （WinError 5）、2 回目以降の保存が黙って失敗していた。
    # その結果スイッチが毎回元に戻り «押せない» ように見えた。
    for i in range(3):
        assert dn.save(root, {"CH002_cur": "改訂%d" % i}), "%d 回目の保存が失敗" % i
        assert dn.load(root)["names"]["CH002_cur"] == "改訂%d" % i
    assert dn.set_enabled(root, False) and not dn.is_enabled(root)
    assert dn.set_enabled(root, True) and dn.is_enabled(root)
    print("saving over an existing (hidden) file keeps working: OK")

    # 保存は «隠し属性を外してから書き、書いたら付け直す»
    calls = []
    orig = dn._set_hidden
    dn._set_hidden = lambda path, on: calls.append(bool(on))
    try:
        dn.save(root, {"CH002_cur": "属性確認"})
    finally:
        dn._set_hidden = orig
    assert calls and calls[0] is False and calls[-1] is True, calls
    print("save unhides before writing and re-hides after: OK")

    # ── 7) カラムタイトル ──────────────────────────────────────────
    assert dn.title(root) == "", dn.title(root)
    assert dn.save(root, {"CH002_cur": "キャラA"}, title="衣装バリエーション")
    assert dn.title(root) == "衣装バリエーション"
    # 見出しだけでもファイルは残る（表示名が空でも消さない）
    assert dn.save(root, {}, title="見出しのみ")
    assert dn.has_file(root) and dn.title(root) == "見出しのみ"
    # 見出しも表示名も空なら削除
    assert dn.save(root, {}, title="")
    assert not dn.has_file(root), "両方空なのにファイルが残っている"
    print("column title round-trips and keeps the file alive on its own: OK")

    # ── 8) フッタの並び: 見出しは左、操作ボタンは右 ────────────────
    dn.save(root, {"CH002_cur": "キャラA"}, title="衣装バリエーション")
    cv._refresh_display_names()
    _settle(400)
    hit = [v for v in cv._live_columns()
           if getattr(v, "_mfm_dn_footer", None) is not None]
    assert hit, "フッタが出ない"
    v = hit[0]
    lb = getattr(v, "_mfm_dn_title", None)
    sw = getattr(v, "_mfm_dn_switch", None)
    assert lb is not None and lb.text() == "衣装バリエーション"
    assert sw is not None
    ft = v._mfm_dn_footer
    cfgs = [w for w in ft.findChildren(type(sw)) if w is not sw]
    assert cfgs, "歯車ボタンが無い"
    cfg = cfgs[0]
    assert lb.x() < sw.x() and sw.x() < cfg.x(), (lb.x(), sw.x(), cfg.x())
    # 右詰め: 歯車の右端がフッタ右端の近く、見出しは左端の近く
    assert ft.width() - (cfg.x() + cfg.width()) <= 8, "歯車が右詰めになっていない"
    assert lb.x() <= 8, "見出しが左詰めになっていない"
    # r119h: 見出しは «「表示名」ボタンの直前まで» 使うこと。
    # 以前は見出しとは別に addStretch を入れており、空きの半分を
    # スペーサーに取られて «アニメーショ» のように切れていた。
    gap = sw.x() - (lb.x() + lb.width())
    assert 0 <= gap <= 10, ("見出しがボタンの手前まで伸びていない",
                            lb.x(), lb.width(), sw.x())
    # 長い見出しは «切り捨て» ではなく «末尾を省略» して全文をツールチップへ
    long_title = "アニメーション用ジェネラルリグ一式（最新）"
    dn.save(root, {"CH002_cur": "キャラA"}, title=long_title)
    cv._refresh_display_names()
    _settle(400)
    v2 = [w for w in cv._live_columns()
          if getattr(w, "_mfm_dn_title", None) is not None][0]
    lb2 = v2._mfm_dn_title
    assert lb2.toolTip() == long_title, "全文がツールチップに出ていない"
    assert lb2.text() == long_title or lb2.text().endswith("…"), lb2.text()
    sw2 = v2._mfm_dn_switch
    assert lb2.x() + lb2.width() <= sw2.x() + 1, "見出しがボタンに重なっている"
    # 見出しが無ければラベルも作らない
    dn.save(root, {"CH002_cur": "キャラA"}, title="")
    cv._refresh_display_names()
    _settle(400)
    hit = [w for w in cv._live_columns()
           if getattr(w, "_mfm_dn_footer", None) is not None]
    assert hit and getattr(hit[0], "_mfm_dn_title", None) is None, \
        "見出しが空なのにラベルが出ている"
    print("footer lays out title left / buttons right, and hides an empty "
          "title: OK")
    finish(True)


run(s1, delay=900)
