# -*- coding: utf-8 -*-
"""r87: カラム間 D&D の移動と、同名時の «上書き / 名前を変えて / スキップ»。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, run, QTimer
from core.file_operations import (move_items, CONFLICT_OVERWRITE,
                                  CONFLICT_RENAME, CONFLICT_SKIP)

b = make_panel()
root = tmpdir()
A = os.path.join(root, "A"); B = os.path.join(root, "B")
os.makedirs(A); os.makedirs(B)


def w(path, text="x"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_plain_move():
    w(os.path.join(A, "a.ma"), "src")
    pairs = []
    res = move_items([os.path.join(A, "a.ma")], B, pairs=pairs)
    assert res == [os.path.join(B, "a.ma")], res
    assert not os.path.exists(os.path.join(A, "a.ma"))
    assert pairs == [(os.path.join(A, "a.ma"), os.path.join(B, "a.ma"))], pairs
    print("plain move + undo pairs: OK")


def test_conflicts():
    # 上書き
    w(os.path.join(A, "a.ma"), "new")
    move_items([os.path.join(A, "a.ma")], B,
               conflict_cb=lambda s, d: CONFLICT_OVERWRITE)
    assert read(os.path.join(B, "a.ma")) == "new"
    # 名前を変えて
    w(os.path.join(A, "a.ma"), "second")
    res = move_items([os.path.join(A, "a.ma")], B,
                     conflict_cb=lambda s, d: CONFLICT_RENAME)
    assert res == [os.path.join(B, "a_1.ma")], res
    assert read(os.path.join(B, "a.ma")) == "new"
    # スキップ
    w(os.path.join(A, "a.ma"), "third")
    res = move_items([os.path.join(A, "a.ma")], B,
                     conflict_cb=lambda s, d: CONFLICT_SKIP)
    assert res == [] and os.path.exists(os.path.join(A, "a.ma"))
    assert read(os.path.join(B, "a.ma")) == "new"
    os.remove(os.path.join(A, "a.ma"))
    print("overwrite / keep-both / skip: OK")


def test_folder_overwrite_merges():
    src = os.path.join(A, "shots"); dst = os.path.join(B, "shots")
    os.makedirs(os.path.join(src, "c001"), exist_ok=True)
    os.makedirs(os.path.join(dst, "c002"), exist_ok=True)
    w(os.path.join(src, "c001", "x.ma"), "fromA")
    w(os.path.join(dst, "c002", "y.ma"), "inB")
    w(os.path.join(src, "same.txt"), "A")
    w(os.path.join(dst, "same.txt"), "B")
    pairs = []
    move_items([src], B, conflict_cb=lambda s, d: CONFLICT_OVERWRITE, pairs=pairs)
    assert os.path.exists(os.path.join(dst, "c001", "x.ma")), "統合されていない"
    assert os.path.exists(os.path.join(dst, "c002", "y.ma")), "既存が消えた"
    assert read(os.path.join(dst, "same.txt")) == "A", "同名ファイルが置き換わっていない"
    assert not os.path.exists(src), "移動元が残っている"
    assert pairs, "Undo 用の対応が記録されていない"
    print("folder overwrite merges contents: OK")


def test_panel_guards():
    """自分の中へ／同じ場所へ のドロップは何もしない。"""
    calls = []
    b._run_file_op = lambda title, work, done=None, total=0: calls.append(title)
    b._on_files_dropped([A], os.path.join(A, "sub_not_exist"), True)
    assert not calls, "存在しない移動先で実行された"
    os.makedirs(os.path.join(A, "sub"), exist_ok=True)
    b._on_files_dropped([A], os.path.join(A, "sub"), True)
    assert not calls, "フォルダを自分の中へ移動しようとした"
    w(os.path.join(A, "keep.ma"))
    b._on_files_dropped([os.path.join(A, "keep.ma")], A, True)
    assert not calls, "同じフォルダへの移動が実行された"
    # 正常系は実行される（衝突なし）
    b._on_files_dropped([os.path.join(A, "keep.ma")], B, True)
    assert calls, "通常の移動が実行されない"
    print("self/same-folder drops are ignored, normal drop runs: OK")


def test_drop_event_reaches_callback():
    """カラムのビューポートに届いた Drop を自前処理が拾い、
    «移動先＝そのカラムのフォルダ / 既定は移動» でコールバックへ渡すこと。

    Qt の実機ではビューポートに載せたイベントフィルタが（後入れなので）
    QAbstractItemView より先に呼ばれる。ここではその呼び出しを直接再現する
    （オフスクリーンでは QApplication.sendEvent での D&D 合成が届かない）。"""
    from core.compat import Qt, QtCore, QtGui, QUrl, QMimeData, QListView
    got = []
    b._column_view.set_file_drop_callback(
        lambda paths, dest, move: got.append((list(paths), dest, move)))
    b.navigate_to(B)
    app.processEvents()
    QTimer.singleShot(600, lambda: _do_drop(got))


def _do_drop(got):
    from core.compat import Qt, QtCore, QtGui, QUrl, QMimeData, QListView
    views = [v for v in b._column_view.findChildren(QListView) if v.isVisible()]
    assert views, "カラムが無い"
    view = None
    for v in views:
        if os.path.normcase(b._column_view._path_for_index(v.rootIndex()) or "") \
           == os.path.normcase(B):
            view = v
    assert view is not None, "B のカラムが見つからない"
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(os.path.join(A, "keep.ma"))])
    pos = QtCore.QPointF(view.viewport().rect().center())
    ev = QtGui.QDropEvent(pos, Qt.MoveAction, mime, Qt.LeftButton,
                          Qt.NoModifier, QtCore.QEvent.Drop)
    handled = b._column_view.eventFilter(view.viewport(), ev)
    assert handled and ev.isAccepted(), "ドロップが受理されていない"
    QTimer.singleShot(150, lambda: _check_drop(got))


def _check_drop(got):
    assert got, "ドロップがコールバックまで届いていない"
    paths, dest, move = got[-1]
    assert move is True, "既定が移動になっていない"
    assert os.path.normcase(dest) == os.path.normcase(B), dest
    assert os.path.basename(paths[0]) == "keep.ma", paths
    print("drop on a column reaches the handler (move by default): OK")
    finish(True)


def step():
    test_plain_move()
    test_conflicts()
    test_folder_overwrite_merges()
    test_panel_guards()
    test_drop_event_reaches_callback()


run(step, delay=100)
