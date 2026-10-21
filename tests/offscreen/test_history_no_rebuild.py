# -*- coding: utf-8 -*-
"""r146: ↶ / ↷（履歴の戻る・進む）はカラムを作り直さない。

ユーザー報告 2026-10-21:
  「Undo Redo の挙動ですが、カラムが押す度にルートに戻っている様に見えます。
    …一つ前の結果がディレクトリを一つ下がっただけや、上がっただけなどでも
    カラムが大きくスライドするため、かなり視認性が悪いです。」

従来は _navigate（全カラムをルートから作り直し）だったので、1 階層の移動でも
左端まで戻ってから滑ってきていた。カラムのルートが同じなら、クリックと同じ
«current を動かすだけ» で移る。

固定すること:
  1) 戻る／進むの前後で、残るカラム（QListView）は **同じオブジェクト**
     （作り直されていない）
  2) 行き先が祖先なら下のカラムが閉じ、子孫ならその分だけ開く
  3) 履歴には積まない（↶↷ は履歴の中を動く操作）
  4) ルートが違う行き先（別ドライブ等）は従来どおり _navigate
"""
import os

from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, QTest, find_item, find_item_wait, columns
from core.compat import Qt, QListView

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def settle(ms=200):
    QTest.qWait(ms)
    app.processEvents()


def col_map():
    """{フォルダ: QListView} （見えているカラム）"""
    out = {}
    for v in columns(b):
        p = b._column_view._path_for_index(v.rootIndex()) or ""
        out[os.path.normcase(os.path.normpath(p))] = v
    return out


def click(name):
    cv, rect, _ = find_item_wait(b, name, 6000)
    assert cv is not None, name
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    settle(400)


d = tmpdir()
a = os.path.join(d, "a")
ab = os.path.join(a, "b")
os.makedirs(ab)
open(os.path.join(ab, "x.ma"), "w").close()
open(os.path.join(a, "y.ma"), "w").close()

b = make_panel(1400, 700)
b.navigate_to(d)
QTest.qWait(3800)            # navigate 後の作り直し（2.6 秒）を過ぎてから
app.processEvents()
N = lambda p: os.path.normcase(os.path.normpath(p))

click("a")
click("b")
settle(600)
check(N(b._current_path) == N(ab), "前提: クリックで d/a/b まで来た（%r）" % b._current_path)
hist_len = len(b._history)
hist_idx = b._history_index
before = col_map()
check(N(d) in before and N(a) in before and N(ab) in before,
      "前提: d / a / b のカラムが出ている（%r）" % sorted(before))

# ── 1)/2) 戻る: a へ。d / a のカラムは同じオブジェクト、b のカラムは閉じる ──
b._go_back()
settle(700)
after = col_map()
check(N(b._current_path) == N(a), "↶ で 1 つ上（d/a）へ（%r）" % b._current_path)
check(after.get(N(d)) is before.get(N(d)) and after.get(N(a)) is before.get(N(a)),
      "↶ でも残るカラムは作り直されない（同じオブジェクト）")
check(N(ab) not in after, "↶ で下のカラム（b）は閉じる（%r）" % sorted(after))

# もう 1 つ戻る: d へ
b._go_back()
settle(700)
after2 = col_map()
check(N(b._current_path) == N(d), "↶ ×2 で d へ（%r）" % b._current_path)
check(after2.get(N(d)) is before.get(N(d)), "d のカラムはずっと同じオブジェクト")

# ── 進む: a → b。開き直しても d / a のカラムはそのまま ─────────────
b._go_forward()
settle(700)
b._go_forward()
settle(900)
after3 = col_map()
check(N(b._current_path) == N(ab), "↷ ×2 で d/a/b へ戻る（%r）" % b._current_path)
# a / b のカラムは一度閉じたので作り直って当然。d（残っていた側）は同じまま
check(after3.get(N(d)) is before.get(N(d)), "↷ でも残っていたカラム（d）は作り直されない")
check(N(ab) in after3, "↷ で b のカラムが開く（%r）" % sorted(after3))
check(find_item_wait(b, "x.ma", 4000)[0] is not None, "b の中身が見える")

# ── 3) 履歴には積まない ─────────────────────────────────────────
check(len(b._history) == hist_len, "↶↷ で履歴が増えない（%d → %d）" % (hist_len, len(b._history)))
check(b._history_index == hist_idx, "履歴の位置は元に戻っている（%d / %d）" % (b._history_index, hist_idx))

# ── 4) ルートが違えば従来の _navigate ───────────────────────────────
called = []
orig_nav = b._navigate
b._navigate = lambda p, add_to_history=True: called.append((p, add_to_history))
try:
    b._history_move_is_light = lambda p: False
    b._history.append(os.path.join(d, "a"))
    b._history_index = len(b._history) - 2
    b._go_forward()
    check(called and called[-1][1] is False, "ルートが違う行き先は _navigate（履歴に積まない）（%r）" % (called,))
finally:
    b._navigate = orig_nav

finish(not fails)
