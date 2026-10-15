# -*- coding: utf-8 -*-
"""r146: D&D メニュー / 高度なコピー / バッチリネームのサブフォルダ適用 /
進捗中もマネージャーが止まらないこと / コンパクト時の最小化。

ユーザー指示（2026-10-13 まで）:
  * 「D&D時に移動/コピー/高度なコピーの依頼をしましたが、未対応ですか？」
    （前セッションからの持ち越し。UE のように落とした場所でメニューを出す）
  * 「移動中やコピー中にマネージャーが止まらない様にしてください。
      進捗バーは出してください。」
  * 「バッチリネームが選択しているフォルダが有る場合に階層下も出来るように、
      「サブフォルダにも適用」ボタンをください。」
  * 「コンパクト時に最小化が出来なくなっています。」
"""
import ast
import io
import os
import shutil
import tempfile

from _common import *  # noqa: F401,F403
from _common import app, sm, finish

from core.compat import Qt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


# ── D&D: 落とした時に選ばせる ─────────────────────────────────────
cv = _src("ui/browser_column_view.py")
check("forced = \"copy\"" in cv and "forced = \"move\"" in cv,
      "Ctrl / Shift を押していれば即実行（Explorer 流儀を残す）")
check("_call_drop_cb" in cv, "コールバックの呼び出しが一箇所にまとまっている")
from ui.browser_column_view import CappedColumnView as _CCV
import inspect as _i
_sig = _i.signature(_CCV._call_drop_cb)
check(list(_sig.parameters) == ["cb", "paths", "dest", "forced", "gpos"],
      "落とした位置まで渡す（%s）" % list(_sig.parameters))
# 旧 3 引数のコールバックでも落ちない（黙ってドロップが消えるのを防ぐ）
_old_got = []
_CCV._call_drop_cb(lambda p, d, m: _old_got.append((p, d, m)),
                   ["a.ma"], "D", "copy", None)
check(_old_got and _old_got[0][2] is False,
      "旧 3 引数へは移動/コピーの真偽値で落とす（%r）" % (_old_got,))
_new_got = []
_CCV._call_drop_cb(lambda p, d, f, g: _new_got.append((p, d, f, g)),
                   ["a.ma"], "D", "move", None)
check(_new_got and _new_got[0][2] == "move", "新しい 4 引数はそのまま届く")

bp = _src("ui/browser_panel.py")
check("_ask_drop_action" in bp, "落とした場所でメニューを出す口がある")
for label in ("ここに移動", "ここにコピー", "高度なコピー"):
    check(label in bp, "メニューに «%s» がある" % label)
check("_advanced_copy_to" in bp, "高度なコピーの実処理がある")
check("DROP_MENU_KEY" in bp and "drop_action_menu" in _src("ui/settings_dialog.py"),
      "設定で «メニューを出すか» を切り替えられる")

from ui.browser_panel import BrowserPanel
import inspect
sig = inspect.signature(BrowserPanel._on_files_dropped)
check("forced" in sig.parameters and "gpos" in sig.parameters,
      "受け口の引数が揃っている（%s）" % list(sig.parameters))

# ── 高度なコピー: 落とし先を指定した複製 ──────────────────────────
from ui.duplicate_dialog import DuplicateDialog
base = tempfile.mkdtemp(prefix="mfm_r146_drop_")
src_dir = os.path.join(base, "src")
dst_dir = os.path.join(base, "dst")
os.makedirs(src_dir)
os.makedirs(dst_dir)
a = os.path.join(src_dir, "shot_a.ma")
io.open(a, "w").write("x")

dlg = DuplicateDialog([a], dest_dir=dst_dir)
specs = dlg.specs()
check(len(specs) == 1, "1 件分の指示が出る")
check(os.path.dirname(specs[0][1]) == os.path.normpath(dst_dir),
      "落とし先へ向く（%r）" % (specs[0][1],))
check(os.path.basename(specs[0][1]) == "shot_a.ma",
      "別フォルダなら «_Copy» を足さない（%r）" % (os.path.basename(specs[0][1]),))
check("高度なコピー" in dlg.windowTitle(),
      "タイトルが «高度なコピー»（%r）" % (dlg.windowTitle(),))
dlg.deleteLater()

# 落とし先に同名がある時だけ名前を変える
io.open(os.path.join(dst_dir, "shot_a.ma"), "w").write("y")
dlg2 = DuplicateDialog([a], dest_dir=dst_dir)
got = os.path.basename(dlg2.specs()[0][1])
check(got != "shot_a.ma" and got.startswith("shot_a"),
      "衝突した時だけ名前を変える（%r）" % (got,))
dlg2.deleteLater()

# 同じフォルダ内の «複製»（従来どおり）
dlg3 = DuplicateDialog([a])
check(os.path.dirname(dlg3.specs()[0][1]) == os.path.normpath(src_dir)
      and os.path.basename(dlg3.specs()[0][1]) != "shot_a.ma",
      "落とし先を渡さなければ従来の複製のまま")
check("複製" in dlg3.windowTitle(), "タイトルも従来どおり")
dlg3.deleteLater()

# ── 進捗中もマネージャーが止まらない ─────────────────────────────
check("dlg.setWindowModality(Qt.NonModal)" in bp,
      "進捗ダイアログが非モーダル（実処理は元からワーカー）")
check("QProgressDialog" in bp, "進捗バーは出したまま")
# 説明文に WindowModal の語が出るのは正常なので «呼び出し» を見る。
_rf = None
for node in ast.walk(ast.parse(bp)):
    if isinstance(node, ast.FunctionDef) and node.name == "_run_file_op":
        _rf = node
_mod_args = []
for n in ast.walk(_rf):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
            and n.func.attr == "setWindowModality":
        for a in n.args:
            _mod_args.append(getattr(a, "attr", "?"))
check(_mod_args == ["NonModal"],
      "_run_file_op が非モーダルを指定する（%r）" % (_mod_args,))

# ── バッチリネーム: サブフォルダにも適用 ─────────────────────────
from ui.batch_rename_dialog import BatchRenameDialog
tree = os.path.join(base, "tree")
os.makedirs(os.path.join(tree, "aa_sub"))
io.open(os.path.join(tree, "aa_top.txt"), "w").write("x")
io.open(os.path.join(tree, "aa_sub", "aa_deep.txt"), "w").write("x")

d = BatchRenameDialog([tree])
check(d._recursive_cb.isVisible() or d._has_dirs,
      "フォルダを選んでいる時にチェックが出る")
check(d._targets() == [tree], "既定では選んだものだけ")
d._recursive_cb.setChecked(True)
app.processEvents()
t = d._targets()
check(len(t) == 4, "ON で階層下も対象になる（%d 件）" % len(t))
check(any(x.endswith("aa_deep.txt") for x in t), "2 段下まで拾う")
deep_first = d._deepest_first([(x, x) for x in t])
check(deep_first[0][0].count(os.sep) >= deep_first[-1][0].count(os.sep),
      "実行は深い順（親を先に変えて子を迷子にしない）")

# 実際に走らせる（prefix を付ける）
# r146: ルールは複数持てるようになったので 1 件目を取る
_r0 = d._rule_rows[0]
_r0._mode_combo.setCurrentIndex(1)        # プレフィックス追加
_r0._prefix_edit.setText("P_")
app.processEvents()
pairs = [(o, n) for o, n, e in d._current_results if e is None]
for old_p, new_p in d._deepest_first(pairs):
    if os.path.basename(old_p) != os.path.basename(new_p):
        os.rename(old_p, new_p)
check(os.path.isdir(os.path.join(base, "P_tree")),
      "親フォルダがリネームされる")
check(os.path.isfile(os.path.join(base, "P_tree", "P_aa_sub", "P_aa_deep.txt")),
      "深い順に実行すれば中身も全部付け替わる")
d.deleteLater()

# フォルダを含まない選択ではチェックを出さない
only_file = BatchRenameDialog([os.path.join(base, "P_tree", "P_aa_top.txt")])
check(not only_file._has_dirs, "ファイルだけならチェックは出ない")
only_file.deleteLater()

# ── リネーム後に «古い名前» が残らない ───────────────────────────
check("_verify_rename_applied" in bp,
      "リネーム後にモデルを確かめる（古い名前が残らない）")
check("_resync_dir" in bp, "ずれていたら読み直す口がある")
mw = _src("ui/main_window.py")
check("_on_batch_renamed" in mw and "_resync_dir" in mw,
      "バッチリネームの後もブラウザを更新する（r145 は何もしていなかった）")

# 描画を壊さないこと: 不透明度ハックを残さない
cm_chk = _src("ui/compact_mode.py")
_op = [n.func.attr for n in ast.walk(ast.parse(cm_chk))
       if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
       and n.func.attr == "setWindowOpacity"]
check(not _op,
      "setWindowOpacity を呼ばない（WS_EX_LAYERED 化で描画が乱れる）")

# ── コンパクト中の D&D ───────────────────────────────────────────
cm_dnd = _src("ui/compact_mode.py")
check("setAcceptDrops(True)" in cm_dnd,
      "パックがドロップを受け取れる（ホバーの enterEvent はドラッグ中に飛ばない）")
for name in ("def dragEnterEvent", "def dragMoveEvent", "def dropEvent"):
    check(name in cm_dnd, "パックに %s がある" % name)
check("for_drag" in cm_dnd,
      "ドラッグで開く時はフォーカスを奪わない（奪うとドラッグが切れる）")
check("MOUSE_BUSY_MAX_SEC" in cm_dnd,
      "«ボタンが押されている» を信じ続けない（外部ドラッグは離したのを見逃す）")
check("drop_on_puck" in cm_dnd,
      "パックに直接落とされた分も取りこぼさない")

import inspect as _insp
from ui.compact_mode import CompactController as _CC
check("for_drag" in _insp.signature(_CC.expand).parameters,
      "expand(for_drag=...) を受け取る")
check(not isinstance(_CC.__dict__.get("_busy"), staticmethod),
      "_busy が状態（押しっぱなしの経過時間）を見られる")

# ── コンパクト時の最小化 ─────────────────────────────────────────
cm = _src("ui/compact_mode.py")
check("self._win.isMinimized()" in cm,
      "最小化中は畳まない（最小化を最小化として扱う）")
check("RESTORE_GRACE_SEC" in cm,
      "最小化から戻した直後に畳み直さない")
check("def contextMenuEvent" in cm and "最小化" in cm,
      "パックの右クリックから最小化できる（タイトルバーが無いため）")

shutil.rmtree(base, ignore_errors=True)
finish(not fails)
