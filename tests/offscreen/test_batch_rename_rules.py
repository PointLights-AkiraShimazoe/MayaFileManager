# -*- coding: utf-8 -*-
"""r146: バッチリネームの «変換ルールを複数» （ユーザー指示 2026-10-15）。

  「変換ルールを複数持てるようにしてください。
    ＋ボタンで増やし、上から順に適用されたとして結果表示を出してください。」
"""
import os
import tempfile

from _common import *  # noqa: F401,F403
from _common import app, finish

from ui.batch_rename_dialog import BatchRenameDialog, _RuleRow

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


d = tempfile.mkdtemp(prefix="mfm_bren_")
names = ["Gee_a.ma", "Gee_b.ma"]
for n in names:
    open(os.path.join(d, n), "w").close()
paths = [os.path.join(d, n) for n in names]

dlg = BatchRenameDialog(paths)
dlg.resize(900, 600)
app.processEvents()

check(len(dlg._rule_rows) == 1, "最初は 1 件（%d）" % len(dlg._rule_rows))
check(not dlg._rule_rows[0]._del_btn.isVisibleTo(dlg._rule_rows[0]),
      "1 件だけの時は «削除» を出さない")

# 1 つ目: Gee → Woo
r1 = dlg._rule_rows[0]
r1._mode_combo.setCurrentIndex(0)
r1._replace_find_edit.setText("Gee")
r1._replace_repl_edit.setText("Woo")
app.processEvents()
got = [os.path.basename(n) for _o, n, _e in dlg._current_results]
check(got == ["Woo_a.ma", "Woo_b.ma"], "1 件目が効く（%r）" % (got,))

# ＋ で 2 つ目: プレフィックス CH_
r2 = dlg._add_rule()
check(len(dlg._rule_rows) == 2, "＋ で増える（%d）" % len(dlg._rule_rows))
check(r1._del_btn.isVisibleTo(r1), "2 件以上なら消せる")
r2._mode_combo.setCurrentIndex(1)
r2._prefix_edit.setText("CH_")
app.processEvents()
got = [os.path.basename(n) for _o, n, _e in dlg._current_results]
check(got == ["CH_Woo_a.ma", "CH_Woo_b.ma"],
      "上から順に当たる（置換 → プレフィックス）（%r）" % (got,))

# 3 つ目: サフィックス _v001 → さらに重なる
r3 = dlg._add_rule()
r3._mode_combo.setCurrentIndex(2)
r3._suffix_edit.setText("_v001")
app.processEvents()
got = [os.path.basename(n) for _o, n, _e in dlg._current_results]
check(got == ["CH_Woo_a_v001.ma", "CH_Woo_b_v001.ma"],
      "3 件目も順に重なる（%r）" % (got,))

# 順番が結果を変える（上から順である証拠）
dlg._remove_rule(r2)
app.processEvents()
got = [os.path.basename(n) for _o, n, _e in dlg._current_results]
check(got == ["Woo_a_v001.ma", "Woo_b_v001.ma"],
      "消したルールは当たらない（%r）" % (got,))
check(len(dlg._rule_rows) == 2, "削除で減る（%d）" % len(dlg._rule_rows))

# 1 件まで減らしたら消せなくなる
dlg._remove_rule(r3)
app.processEvents()
check(len(dlg._rule_rows) == 1, "1 件まで減る")
dlg._remove_rule(dlg._rule_rows[0])
app.processEvents()
check(len(dlg._rule_rows) == 1, "最後の 1 件は消せない（空にならない）")

# 大文字小文字の指定は «全部当てた後» に効く
dlg._case_combo.setCurrentIndex(2)      # UPPERCASE
app.processEvents()
got = [os.path.basename(n) for _o, n, _e in dlg._current_results]
check(got == ["WOO_A.MA", "WOO_B.MA"], "大文字小文字は最後に当たる（%r）" % (got,))

check(isinstance(dlg._rule_rows[0], _RuleRow), "ルールは 1 件 1 ウィジェット")
dlg.close()
finish(not fails)
