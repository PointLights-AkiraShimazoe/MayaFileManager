# -*- coding: utf-8 -*-
"""r101: 右クリックからバッチリネームを起動でき、対象は «その時の選択» になる。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, tmpdir, finish, run, QTest
from core.compat import Qt
from ui.main_window import MainWindow

root = tmpdir()
NAMES = ["cut010_A.ma", "cut010_B.ma", "cut020_C.ma", "other.txt"]
for n in NAMES:
    open(os.path.join(root, n), "w").close()

w = MainWindow(sm); w.resize(1300, 800); w.show()
area = w._areas[0]
b = area.browser
b.navigate_to(root)
GOT = []
area.batch_rename_requested.connect(lambda ps: GOT.append(list(ps)))


def _settle(ms=350):
    QTest.qWait(ms)
    app.processEvents()


def _menu_action(paths, label_part):
    """_popup_context_menu が作るメニューから該当アクションを取り出す。"""
    from core.compat import QMenu
    made = {}
    orig = QMenu.exec if hasattr(QMenu, "exec") else QMenu.exec_

    def fake_exec(self, *a, **k):
        made["menu"] = self
        return None
    if hasattr(QMenu, "exec"):
        QMenu.exec = fake_exec
    QMenu.exec_ = fake_exec
    try:
        b._popup_context_menu(paths, b.mapToGlobal(b.rect().center()))
    finally:
        if hasattr(QMenu, "exec"):
            QMenu.exec = orig
        QMenu.exec_ = orig
    menu = made.get("menu")
    assert menu is not None, "コンテキストメニューが作られていない"
    for act in menu.actions():
        if label_part in act.text():
            return act
    raise AssertionError("メニューに %r が無い: %s"
                         % (label_part, [a.text() for a in menu.actions()]))


def s1():
    # 2件を選んだ状態で右クリック → その2件が対象として渡る
    sel = [os.path.join(root, "cut010_A.ma"), os.path.join(root, "cut010_B.ma")]
    act = _menu_action(sel, "バッチリネーム")
    assert act.isEnabled(), "バッチリネームが無効になっている"
    act.trigger()
    _settle(200)
    assert GOT, "batch_rename_requested が飛ばない"
    assert sorted(GOT[-1]) == sorted(sel), ("対象が選択と違う", GOT[-1])
    print("context menu passes exactly the right-clicked selection: OK")

    # 1件でも起動できる
    GOT.clear()
    one = [os.path.join(root, "other.txt")]
    _menu_action(one, "バッチリネーム").trigger()
    _settle(200)
    assert GOT and GOT[-1] == one, ("単一選択の対象が違う", GOT)
    print("works with a single selection too: OK")

    # MainWindow 側: 渡された対象を «拾い直さない»
    seen = {}
    import ui.main_window as mw
    orig = mw.BatchRenameDialog

    class _Sig:               # renamed.connect を持つだけのダミー
        def connect(self, *_a, **_k):
            pass

    class _Fake:
        # r119: バッチリネームは非モーダルになった（Manager を止めない）。
        # ダイアログに求められる最低限だけ生やす。
        renamed = _Sig()
        destroyed = _Sig()

        def __init__(self, paths, parent=None):
            seen["paths"] = list(paths)

        def __getattr__(self, _name):      # setModal/show/raise_ などを素通し
            return lambda *a, **k: None

        def exec_(self):
            return 0
    mw.BatchRenameDialog = _Fake
    try:
        w._open_batch_rename(sel)
    finally:
        mw.BatchRenameDialog = orig
    assert sorted(seen.get("paths", [])) == sorted(sel), \
        ("ダイアログへ渡る対象が違う（選択を拾い直している）", seen)
    print("dialog receives the passed selection verbatim: OK")
    finish(True)


run(s1, delay=900)
