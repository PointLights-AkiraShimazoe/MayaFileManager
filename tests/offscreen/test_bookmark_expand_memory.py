# -*- coding: utf-8 -*-
"""r133: ブックマークのフォルダの開閉を覚える（ユーザー指示 2026-10-08）。

従来は _populate() のたびに expandAll() していたので、閉じてもすぐ開き直り、
再起動では当然もとに戻っていた。«閉じたフォルダの id» を設定へ持つ。
開いている側ではなく閉じている側を持つのは、新しく作ったフォルダが
«たたまれた状態» で現れないようにするため（従来の既定＝全開を保つ）。
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest

from core.bookmark_manager import BookmarkManager
from ui.bookmark_panel import BookmarkPanel, ROLE_ID

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _settle(ms=120):
    QTest.qWait(ms)
    app.processEvents()


def _item_by_id(tree, bid):
    def walk(it):
        if str(it.data(0, ROLE_ID)) == bid:
            return it
        for i in range(it.childCount()):
            got = walk(it.child(i))
            if got is not None:
                return got
        return None
    for i in range(tree.topLevelItemCount()):
        got = walk(tree.topLevelItem(i))
        if got is not None:
            return got
    return None


def s1():
    sm.set("bookmarks", None, save=False)
    sm.set(BookmarkPanel.EXPAND_KEY, [], save=False)

    bm = BookmarkManager(sm)
    outer = bm.add_folder("Outer")
    inner = bm.add_folder("Inner", parent_folder_id=outer["id"])
    bm.add_directory("/tmp", name="tmp-a", parent_folder_id=outer["id"])
    bm.add_directory("/tmp", name="tmp-b", parent_folder_id=inner["id"])

    p = BookmarkPanel(bm, settings_manager=sm)
    p.resize(300, 400)
    p.show()
    _settle()

    oi = _item_by_id(p._tree, outer["id"])
    ii = _item_by_id(p._tree, inner["id"])
    check(oi is not None and ii is not None, "前提: フォルダが 2 つ並んでいる")
    check(oi.isExpanded() and ii.isExpanded(), "既定は従来どおり全部開いている")

    # ── 閉じると覚える ───────────────────────────────────────────────
    ii.setExpanded(False)
    _settle()
    check(inner["id"] in p._collapsed,
          "閉じたフォルダが記録される（%s）" % (sorted(p._collapsed),))
    check(inner["id"] in (sm.get(BookmarkPanel.EXPAND_KEY, []) or []),
          "設定にも書かれる（%s）" % (sm.get(BookmarkPanel.EXPAND_KEY, []),))

    # ── 作り直し（＝ブックマークの変更）で開き直されない ───────────────
    bm.add_directory("/tmp", name="tmp-c")
    _settle(200)
    oi = _item_by_id(p._tree, outer["id"])
    ii = _item_by_id(p._tree, inner["id"])
    check(ii is not None and not ii.isExpanded(),
          "ツリーを作り直しても閉じたままでいる")
    check(oi is not None and oi.isExpanded(), "閉じていないフォルダは開いたまま")

    # ── 再起動相当: 同じ設定から作り直すと復元される ───────────────────
    p2 = BookmarkPanel(bm, settings_manager=sm)
    p2.resize(300, 400)
    p2.show()
    _settle()
    ii2 = _item_by_id(p2._tree, inner["id"])
    check(ii2 is not None and not ii2.isExpanded(),
          "作り直した（＝再起動相当）パネルでも閉じたまま")

    # ── 絞り込み中は全部開くが、その開閉は «意思» ではないので覚えない ──
    before = set(p2._collapsed)
    p2._on_filter_changed("tmp")
    _settle(200)
    check(p2._collapsed == before,
          "絞り込み中の開閉は記録しない（%s → %s）"
          % (sorted(before), sorted(p2._collapsed)))
    p2._on_filter_changed("")
    _settle(200)
    ii2 = _item_by_id(p2._tree, inner["id"])
    check(ii2 is not None and not ii2.isExpanded(),
          "絞り込みを解除すると、覚えていた開閉へ戻る")

    sm.set(BookmarkPanel.EXPAND_KEY, [], save=False)
    finish(not fails)


run(s1, delay=900)
