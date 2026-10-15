# -*- coding: utf-8 -*-
"""r146: 進捗は «中身の数» で刻む／キャンセルが本当に効く。

ユーザー報告 2026-10-14:
  「複製や移動時に、フォルダ移動の場合でも、中身の数で進捗を出さないと、
    0%が急に100%になって終わるだけで、進捗バーの意味をなしていません。」
  「複製している時に進捗バーのキャンセルを押しても何度もウィンドウが
    出てきてキャンセル出来ません」

ここで固定すること:
  1) フォルダ 1 つでも、分母は «中に入っている数»
  2) 進捗は 1 件ずつ進む（0 → 100 の 2 コマで終わらない）
  3) 途中でキャンセルしたら «失敗» ではなく «キャンセル» として伝わる
     （FileOperationError に包まない）。写せた分は残る
  4) 進捗ダイアログの見張りは、キャンセル後に出し直さない
"""
import ast
import os
import tempfile

from _common import *  # noqa: F401,F403
from _common import finish

from core.file_operations import (
    count_entries, copy_items, move_items, duplicate_items,
    FileOperationError, OperationCancelled,
)

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def make_tree(root, name, n_files=5, n_sub=2):
    """フォルダ 1 つ（中に n_sub 個のサブフォルダ × n_files ファイル）。"""
    top = os.path.join(root, name)
    os.makedirs(top)
    for i in range(n_files):
        open(os.path.join(top, "f%d.txt" % i), "w").close()
    for s in range(n_sub):
        sub = os.path.join(top, "sub%d" % s)
        os.makedirs(sub)
        for i in range(n_files):
            open(os.path.join(sub, "g%d.txt" % i), "w").close()
    return top


base = tempfile.mkdtemp(prefix="mfm_prog_")
src_root = os.path.join(base, "src")
os.makedirs(src_root)
tree = make_tree(src_root, "big")          # 1 + 5 + 2 + 10 = 18 件
dst = os.path.join(base, "dst")
os.makedirs(dst)

# ── 1) 数え上げ ─────────────────────────────────────────────────
n = count_entries([tree])
check(n == 18, "フォルダの中身まで数える（%d、期待 18）" % n)
check(count_entries([os.path.join(tree, "f0.txt")]) == 1, "ファイルは 1 件")
check(count_entries([]) >= 1, "空でも 0 で割らない")

# ── 2) コピーは 1 件ずつ刻む ─────────────────────────────────────
seen = []


def cb(i, total):
    seen.append((i, total))


copy_items([tree], dst, progress_cb=cb)
check(len(seen) > 3, "コピーの進捗が刻まれる（%d 回）" % len(seen))
check(seen[0][1] == 18, "分母は中身の数（%d）" % seen[0][1])
check(all(b == seen[0][1] for _a, b in seen), "分母が途中で変わらない")
check([a for a, _b in seen] == sorted(a for a, _b in seen), "進捗が逆戻りしない")
check(seen[-1][0] == seen[-1][1], "最後は満タン（%r）" % (seen[-1],))
check(os.path.isfile(os.path.join(dst, "big", "sub1", "g4.txt")),
      "中身までコピーされている")

# ── 3) 途中でキャンセル ─────────────────────────────────────────
dst2 = os.path.join(base, "dst2")
os.makedirs(dst2)
hits = {"n": 0}


def cancel_cb(i, total):
    hits["n"] += 1
    if hits["n"] >= 4:
        raise OperationCancelled()


err = None
try:
    copy_items([tree], dst2, progress_cb=cancel_cb)
except OperationCancelled:
    err = "cancel"
except FileOperationError as e:
    err = "wrapped: %r" % (e,)
check(err == "cancel",
      "キャンセルは «失敗» に化けない（%r）" % (err,))
check(os.path.isdir(os.path.join(dst2, "big")), "途中まで写ったものは残る")

# ── 4) 複製も同じ ───────────────────────────────────────────────
seen2 = []
duplicate_items([(tree, os.path.join(src_root, "big_Copy"))],
                progress_cb=lambda i, t: seen2.append((i, t)))
check(len(seen2) > 3, "複製の進捗も刻まれる（%d 回）" % len(seen2))
check(seen2[0][1] == 18, "複製の分母も中身の数（%d）" % seen2[0][1])

# ── 5) 移動の分母も中身の数 ─────────────────────────────────────
seen3 = []
dst3 = os.path.join(base, "dst3")
os.makedirs(dst3)
move_items([tree], dst3, progress_cb=lambda i, t: seen3.append((i, t)))
check(seen3[0][1] == 18, "移動の分母も中身の数（%d）" % seen3[0][1])
check(seen3[-1][0] == seen3[-1][1], "移動も最後は満タン（%r）" % (seen3[-1],))
check(os.path.isdir(os.path.join(dst3, "big")), "移動できている")

# ── 6) 進捗ダイアログの見張りはキャンセル後に出し直さない ───────
from _common import panel_ast
tree_ast = panel_ast()
on_tick = None
for node in ast.walk(tree_ast):
    if isinstance(node, ast.FunctionDef) and node.name == "on_tick":
        on_tick = node
check(on_tick is not None, "進捗ダイアログの見張りが見つかる")
if on_tick is not None:
    names = {n.value for n in ast.walk(on_tick)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    check("cancel" in names,
          "見張りがキャンセル状態を見ている（押した直後に出し直さない）")
    stops = [n for n in ast.walk(on_tick)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "stop"]
    check(len(stops) >= 2, "出したら／キャンセルされたら見張りを止める（%d）"
          % len(stops))

finish(not fails)
