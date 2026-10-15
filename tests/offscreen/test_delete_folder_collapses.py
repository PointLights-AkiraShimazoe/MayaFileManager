# -*- coding: utf-8 -*-
"""r146: 表示中のフォルダが消えたら、そのカラムを閉じて親に戻るだけ。

ユーザー報告 2026-10-15:
  「フォルダを削除したら、カラムが一度左に移動し、再度サーチされるように
    右に戻る挙動をしました。」

消えた現在地の移動先が «すぐ上の親» なら navigate_to（全カラム作り直し）
ではなく _collapse_to（下のカラムを閉じるだけ）で戻る。親のカラム群は
同じウィジェットのまま残る（＝左へ飛んで再サーチ、が起きない）。
"""
import os
import shutil
from _common import *  # noqa: F401,F403
from _common import make_panel, tmpdir, columns, app, QTest, finish

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


root = tmpdir()
A = os.path.join(root, "A")
B = os.path.join(A, "B")
os.makedirs(B)
open(os.path.join(B, "leaf.ma"), "w").close()
open(os.path.join(A, "other.ma"), "w").close()

b = make_panel(1300, 700)
_keep.append(b)
b.navigate_to(B)
for _ in range(30):
    QTest.qWait(100)
    app.processEvents()
    if any(os.path.normcase(b._column_view._path_for_index(v.rootIndex()) or "")
           == os.path.normcase(B) for v in columns(b)):
        break
# ナビゲーション直後の «見た目の自己修復»（_force_column_rebuild、最長 2.6 秒後）
# が過ぎてから数える。これが途中で走ると «作り直し» と区別が付かない。
QTest.qWait(3200)
app.processEvents()
before = columns(b)
roots_before = [os.path.normcase(b._column_view._path_for_index(v.rootIndex()) or "")
                for v in before]
check(os.path.normcase(B) in roots_before, "前提: B のカラムが出ている（%r）" % roots_before)
check(os.path.normcase(A) in roots_before, "前提: A のカラムが出ている")

# B を消す（実際の削除と同じく、モデルの外で消える）
shutil.rmtree(B)
for _ in range(40):
    QTest.qWait(100)
    app.processEvents()
    if not os.path.exists(B) and not b._fs_model.index(B).isValid():
        break
# 現在地消失の判定結果を UI スレッドへ（ワーカーが出すのと同じ呼び方）
orig_nav = b.navigate_to
nav_calls = []
b.navigate_to = lambda *a, **k: (nav_calls.append(a), orig_nav(*a, **k))
b._on_gone_result(B, A)
for _ in range(30):
    QTest.qWait(100)
    app.processEvents()
    roots_now = [os.path.normcase(b._column_view._path_for_index(v.rootIndex()) or "")
                 for v in columns(b)]
    if os.path.normcase(A) in roots_now and os.path.normcase(B) not in roots_now:
        break

after = columns(b)
roots_after = [os.path.normcase(b._column_view._path_for_index(v.rootIndex()) or "")
               for v in after]
check(os.path.normcase(b._current_path) == os.path.normcase(A),
      "現在地は親 A（%r）" % b._current_path)
check(os.path.normcase(B) not in roots_after, "B のカラムは閉じた（%r）" % roots_after)
check(os.path.normcase(A) in roots_after, "A のカラムは出ている")
# A より左（A の祖先のカラム）は同じウィジェットのまま＝左へ飛んで
# 再サーチ、が起きていない。A の中身のカラムは Qt（QColumnView）が
# 作り直すことがあるので、ここでは問わない。
na = os.path.normcase(A)
left_before = [v for v, r in zip(before, roots_before) if r != na and na.startswith(r)]
left_after = [v for v, r in zip(after, roots_after) if r != na and na.startswith(r)]
check(left_before and left_before == left_after,
      "A より左のカラムは同じまま（%d 本）" % len(left_before))
check(not nav_calls, "navigate_to（全カラム作り直し）は呼ばれない（%r）" % (nav_calls,))
check(len(after) == len(before) - 1,
      "減ったのは B のカラム 1 本だけ（%d → %d）" % (len(before), len(after)))

finish(not fails)
