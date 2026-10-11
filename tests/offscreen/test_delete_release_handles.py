# -*- coding: utf-8 -*-
"""r144: 削除が «Manager 自身の監視ハンドル» に捕まらないこと。

ユーザー報告 2026-10-08:
  「移動の際、何かに捕まってこのエラーがでてますが、開いてないし、
    読み取り専用でも無いです。manager自体に捕まってませんか？」
  「managerを閉じてエクスプローラーで消すと消えました」

原因: リネーム／移動の前には _release_fs_handles を呼んでいたのに、
削除の前には «一切呼んでいなかった»。QFileSystemModel（QFileInfoGatherer）が
fetch 済みディレクトリを掴み続けるため、Windows では os.rename も
SHFileOperation も弾かれる。

確認すること:
  1) _release_fs_handles_for が対象フォルダ分だけ _release_fs_handles を呼ぶ
  2) ファイルだけ渡した時は «親フォルダ» を手放しにいく
  3) level=2 はモデル作り直し＝1 回だけで足りる
  4) delete_to_work_trash が失敗パスを failed_paths へ積む（再試行の材料）
"""
import os
from _common import *  # noqa: F401,F403
from _common import tmpdir, finish, make_panel

from ui.browser_panel import BrowserPanel

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


root = tmpdir()
d1 = os.path.join(root, "d1")
d2 = os.path.join(root, "d2")
os.makedirs(d1)
os.makedirs(d2)
f1 = os.path.join(root, "a.ma")
open(f1, "w").close()

b = make_panel()
_keep.append(b)

calls = []
b._release_fs_handles = lambda folder, level=1: calls.append(
    (os.path.normcase(os.path.abspath(folder)), level))


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


calls[:] = []
b._release_fs_handles_for([d1, d2], level=1)
check(sorted(calls) == sorted([(_norm(d1), 1), (_norm(d2), 1)]),
      "フォルダは全部手放す（1件目だけではない）: %r" % (calls,))

calls[:] = []
b._release_fs_handles_for([d1, d1], level=1)
check(calls == [(_norm(d1), 1)], "同じフォルダは 1 回だけ: %r" % (calls,))

calls[:] = []
b._release_fs_handles_for([f1], level=1)
check(calls == [(_norm(root), 1)], "ファイルだけなら親フォルダを手放す: %r" % (calls,))

calls[:] = []
b._release_fs_handles_for([d1, d2], level=2)
check(len(calls) == 1 and calls[0][1] == 2,
      "level=2 はモデルごと作り直すので 1 回で足りる: %r" % (calls,))

calls[:] = []
b._release_fs_handles_for([], level=1)
check(calls == [], "対象が無ければ何もしない: %r" % (calls,))

# 4) failed_paths
from core.undo_stack import delete_to_work_trash  # noqa: E402
missing = os.path.join(root, "nope.ma")
fp = []
op, failed, direct = delete_to_work_trash([missing], failed_paths=fp)
check(fp == [], "存在しないパスは最初に捨てられる（失敗扱いにしない）: %r" % (fp,))

f2 = os.path.join(root, "b.ma")
open(f2, "w").close()
fp = []
op, failed, direct = delete_to_work_trash([f2], failed_paths=fp)
check(not failed and fp == [], "普通に消せた時は失敗パスが積まれない: %r" % (fp,))
check(not os.path.exists(f2), "実際に消えている")

finish(not fails)
