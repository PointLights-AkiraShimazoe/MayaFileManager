# -*- coding: utf-8 -*-
"""r62: Undo/Redo（名前変更・移動・コピー・削除）と、空フォルダでも子カラムが出ること。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, columns, tmpdir, finish, run, QTimer, Qt, QTest
from core.undo_stack import (get_undo_stack, RenameOp, MoveOp, CopyOp,
                             delete_to_work_trash, UndoError)

d = tmpdir()
os.makedirs(os.path.join(d, "empty_dir"))
os.makedirs(os.path.join(d, "dst"))
for n in ("a.ma", "b.ma"):
    open(os.path.join(d, n), "w").write(n)
b = make_panel()
b.navigate_to(d)
st = get_undo_stack()


def s1():
    # ── 空フォルダをクリック → ヘッダ付きの空カラムが出る ──────────────
    n0 = len(columns(b))
    cv, rect, _ = find_item(b, "empty_dir"); assert cv
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTimer.singleShot(700, lambda: s2(n0))


def s2(n0):
    cols = columns(b)
    assert len(cols) == n0 + 1, (n0, len(cols))
    last = cols[-1]
    assert b._column_view._path_for_index(last.rootIndex()).replace("\\", "/").endswith("/empty_dir")
    assert last.model().rowCount(last.rootIndex()) == 0
    assert getattr(last, "_mfm_header", None) is not None, "header missing on empty column"
    print("empty folder shows an empty column with header: OK")
    s3()


def s3():
    a, b_ = os.path.join(d, "a.ma"), os.path.join(d, "b.ma")
    # 名前変更
    os.rename(a, os.path.join(d, "a2.ma")); st.push(RenameOp(a, os.path.join(d, "a2.ma")))
    st.undo(); assert os.path.exists(a) and not os.path.exists(os.path.join(d, "a2.ma"))
    st.redo(); assert os.path.exists(os.path.join(d, "a2.ma"))
    st.undo()
    # 移動
    from core.file_operations import move_items, copy_items
    res = move_items([a], os.path.join(d, "dst")); st.push(MoveOp(list(zip([a], res))))
    assert not os.path.exists(a)
    st.undo(); assert os.path.exists(a) and not os.path.exists(res[0])
    st.redo(); assert os.path.exists(res[0]); st.undo()
    # コピー
    res = copy_items([b_], os.path.join(d, "dst")); st.push(CopyOp(list(zip([b_], res))))
    st.undo(); assert not os.path.exists(res[0]) and os.path.exists(b_)
    st.redo(); assert os.path.exists(res[0]) and open(res[0]).read() == "b.ma"; st.undo()
    # 削除（作業ごみ箱経由）
    op, failed, direct = delete_to_work_trash([a, b_])
    assert not failed, failed
    if op is None:
        print("  (work trash unavailable on this volume: delete undo skipped)")
    else:
        assert not os.path.exists(a) and not os.path.exists(b_)
        st.push(op)
        assert st.can_undo() and "削除" in st.undo_label()
        st.undo(); assert os.path.exists(a) and os.path.exists(b_)
        st.redo(); assert not os.path.exists(a)
        st.undo(); assert os.path.exists(a)
        # 元の場所に同名が既にある → 失敗を UndoError で返す（黙って上書きしない）
        st.redo(); open(a, "w").close()
        try:
            st.undo(); raise AssertionError("should fail")
        except UndoError:
            pass
        os.unlink(a); st.undo(); assert os.path.exists(a)
    # パネル経由（ショートカットの先）: 空スタックでも落ちない
    while st.can_undo():
        st.undo()
    b._undo_op(); b._redo_op()
    # dispose_all で作業ごみ箱の個別フォルダが消える（OS ごみ箱送り or rmdir）
    op2, _f, _d = delete_to_work_trash([os.path.join(d, "b.ma")])
    if op2 is not None:
        st.push(op2); tdir = op2.trash_dir
        st.dispose_all()
        assert not os.path.exists(tdir) or not os.listdir(tdir), "trash dir not disposed"
    print("undo/redo: rename / move / copy / delete / conflicts / dispose: OK")
    finish()


run(s1, delay=2500)
