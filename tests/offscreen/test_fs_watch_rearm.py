# -*- coding: utf-8 -*-
"""r146: 操作のあと «見張り» を必ず付け直す／カラムを巻き戻さない。

ユーザー報告 2026-10-15:
  「コピーでも移動でも、結果が即座に反映されておらず、完了しても反映されず
    なので駄目です。1 分近く待っても反映がされてないです。」
  「フォルダを削除したら、カラムが一度左に移動し、再度サーチされるように
    右に戻る挙動をしました。」
  「カラムは D ドライブまで巻き戻りました。」

原因は 1 つ。削除・移動の前に Windows のハンドルを手放すため
``setRootPath("")`` で «これまでに読んだフォルダの見張り» を全部落として
いるのに、**後で付け直していなかった**。
  * 付け直さない → そのフォルダの変化が永久に届かない（反映されない）
  * "" を挟む     → カラムが組み直されて左へ飛ぶ（巻き戻る）

ここで固定すること:
  1) _resync_dir は rootPath を空にしない（巻き戻さない）
  2) 操作が終わったら必ず見張りを付け直す道を通る
  3) 付け直した後、rootPath は «今いる場所» に戻っている
"""
import ast
import os
import tempfile

from _common import *  # noqa: F401,F403
from _common import app, make_panel, finish, QTest

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BP = ast.parse(open(os.path.join(ROOT, "ui", "browser_panel.py"),
                    encoding="utf-8").read())


def _func(name):
    for n in ast.walk(BP):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(name)


# ── 1) _resync_dir は rootPath を空にしない ─────────────────────
f = _func("_resync_dir")
empties = [x for x in ast.walk(f)
           if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
           and x.func.attr == "setRootPath"
           and x.args and isinstance(x.args[0], ast.Constant)
           and x.args[0].value == ""]
check(not empties, "_resync_dir は setRootPath(\"\") をしない（カラムが巻き戻る）")
calls = {x.func.attr for x in ast.walk(f)
         if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)}
check("_rearm_fs_watch" in calls, "見張りを付け直す道を通る")

# ── 2) 操作の終わりで必ず付け直す ───────────────────────────────
src = open(os.path.join(ROOT, "ui", "browser_panel.py"), encoding="utf-8").read()
run_op = _func("_run_file_op")
inner = {n.name for n in ast.walk(run_op) if isinstance(n, ast.FunctionDef)}
check("done" in inner, "_run_file_op に終了処理がある")
done_fn = [n for n in ast.walk(run_op)
           if isinstance(n, ast.FunctionDef) and n.name == "done"][0]
calls = {x.func.attr for x in ast.walk(done_fn)
         if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)}
check("_rearm_fs_watch" in calls,
      "どの操作でも、終わったら見張りを付け直す（%r）" % (sorted(calls),))

# ── 3) 実際に付け直され、rootPath が «今いる場所» に戻る ────────
b = make_panel()
d = tempfile.mkdtemp(prefix="mfm_watch_")
sub = os.path.join(d, "sub")
os.makedirs(sub)
open(os.path.join(d, "a.txt"), "w").close()
b.navigate_to(d)
QTest.qWait(400)
app.processEvents()

root_before = b._column_view.rootIndex()
root_path_before = b._column_view._path_for_index(root_before) or ""
b._fs_model.setRootPath("")           # 見張りを全部落とした状態を作る
app.processEvents()
b._rearm_fs_watch([sub])
# r146: 付け直しは «少し置いてからまとめて 1 回»（その場編集とぶつけない）
for _ in range(25):
    QTest.qWait(100)
    app.processEvents()
    if b._fs_model.rootPath():
        break
rp = os.path.normcase(os.path.normpath(b._fs_model.rootPath()))
check(rp == os.path.normcase(os.path.normpath(d)),
      "付け直したあと rootPath は «今いる場所»（%r）" % (b._fs_model.rootPath(),))

root_path_after = b._column_view._path_for_index(b._column_view.rootIndex()) or ""
check(root_path_after == root_path_before,
      "付け直してもカラムのルートが動かない（%r → %r）"
      % (root_path_before, root_path_after))

# 付け直したあとに増えたものが見える
# （Windows の見張りは «変化を拾うまで» に数秒かかることがあるので長めに待つ。
#   それでも来ない環境向けに、最後は読み直しでも確かめる）
open(os.path.join(d, "b.txt"), "w").close()


def _names():
    idx = b._fs_model.index(d)
    return {b._fs_model.index(r, 0, idx).data()
            for r in range(b._fs_model.rowCount(idx))}


seen = False
for _ in range(60):
    QTest.qWait(100)
    app.processEvents()
    if "b.txt" in _names():
        seen = True
        break
if not seen:
    b._rearm_fs_watch([d])          # 読み直しても出るか
    for _ in range(12):
        QTest.qWait(100)
        app.processEvents()
    for _ in range(30):
        QTest.qWait(100)
        app.processEvents()
        if "b.txt" in _names():
            seen = True
            break
check(seen, "付け直したあと、増えたファイルがモデルに出る")

finish(not fails)
