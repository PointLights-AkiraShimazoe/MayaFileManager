# -*- coding: utf-8 -*-
"""r124: 親階層へ «戻れる» こと／複数選択のカラムを残さないこと。

ユーザー報告 2026-10-06:
  「子フォルダに入った場合、親フォルダを選択済のものを選択したら親階層に
    戻る様にしてください。親階層の別フォルダを選ばないと、上に戻れない」
  「複数選択をやめたのに、複数選択時のカラムが一部残ります」

後者の正体: 後片付け（_on_flat_request([])）が «平坦カラムが見えている時» に
しか走っておらず、共通フォルダカラム（_common_cols）が取り残されていた。
ファイルを単独クリックした時にはそもそも後片付けが無かった。
"""
import os
from _common import *  # noqa: F401,F403
from _common import make_panel, tmpdir, find_item_wait, columns, app, QTest, Qt, finish

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


root = tmpdir()
for d in ("A/B/C", "A/X", "Z/B"):
    os.makedirs(os.path.join(root, d))
open(os.path.join(root, "A", "B", "C", "leaf.ma"), "w").close()
open(os.path.join(root, "A", "note.ma"), "w").close()

b = make_panel(1500, 700)
_keep.append(b)
b.navigate_to(root)
QTest.qWait(800)
app.processEvents()


def click(name, mods=Qt.NoModifier):
    cv, rect, idx = find_item_wait(b, name)
    assert cv, "項目が見つからない: " + name
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, mods, rect.center())
    QTest.qWait(600)
    app.processEvents()


def ncase(p):
    return os.path.normcase(os.path.normpath(p))


# ── 1) 子フォルダに入ってから «選択済みの親» をクリック → 戻る ────────
click("A")
click("B")
click("C")
deep_cols = len(columns(b))
check(ncase(b._current_path) == ncase(os.path.join(root, "A", "B", "C")),
      "A/B/C まで入れる（current=%r）" % b._current_path)

click("A")
check(ncase(b._current_path) == ncase(os.path.join(root, "A")),
      "選択済みの親 A をクリックすると現在地が A に戻る（current=%r）" % b._current_path)
check(len(columns(b)) < deep_cols,
      "下の階層のカラムが閉じる（%d → %d）" % (deep_cols, len(columns(b))))

# 戻った後、もう一度下へ潜れる（戻る処理が選択を壊していない）
click("B")
check(ncase(b._current_path) == ncase(os.path.join(root, "A", "B")),
      "戻った後また下へ潜れる（current=%r）" % b._current_path)

# ── 2) 複数選択のカラムは «単独クリック» で残らない ──────────────────
click("A")
b._on_flat_request([os.path.join(root, "A"), os.path.join(root, "Z")])
QTest.qWait(500)
app.processEvents()
check(b._flat_col.isVisible() and len(b._common_cols) >= 1,
      "複数選択で平坦カラムと共通フォルダカラムが出る（flat=%s common=%d）"
      % (b._flat_col.isVisible(), len(b._common_cols)))

click("A")
check(not b._flat_col.isVisible() and len(b._common_cols) == 0,
      "フォルダの単独クリックで両方とも畳まれる（flat=%s common=%d）"
      % (b._flat_col.isVisible(), len(b._common_cols)))

# ファイルの単独クリックでも残らない（従来はここに後片付けが無かった）
b._on_flat_request([os.path.join(root, "A"), os.path.join(root, "Z")])
QTest.qWait(500)
app.processEvents()
assert b._flat_col.isVisible(), "前提: 平坦カラムが出ている"
click("note.ma")
check(not b._flat_col.isVisible() and len(b._common_cols) == 0,
      "ファイルの単独クリックでも両方とも畳まれる（flat=%s common=%d）"
      % (b._flat_col.isVisible(), len(b._common_cols)))

finish(not fails)
