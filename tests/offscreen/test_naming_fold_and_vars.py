# -*- coding: utf-8 -*-
"""r146: 自動命名の «ルールごとの折りたたみ» と «変数のプルダウン＋挿入»。

ユーザー指示（持ち越し分）:
  1. 自動命名のルール毎にフレームで折りたたみ
  2. 全プリセットの変数プルダウン＋テキストフィールドへの挿入ボタン

ここで固定すること:
  1) 規則は畳める。畳んだら中身が隠れ、見出し（ルート / 対象階層）は残る
  2) 読み込んだ既存の規則は畳んだ状態で並ぶ（新規追加の分は開いたまま）
  3) 変数のプルダウンは «対象階層に書いた {...} ＋ ver» を出す
  4) «挿入» はファイル名欄の **カーソル位置** に入れる（末尾固定ではない）
  5) これはプリセット毎のエディタ（＝全プリセット）で効く
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish

from ui.settings_dialog import AutoNamingRuleRow

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


RULE = {"levels": "{Group}/{Set}/n{Item}",
        "filename": "{Group}_{Set}_{Item}_{ver}"}

row = AutoNamingRuleRow(directory="D:/Work/Proj", rule=dict(RULE))
row.resize(600, 300)
row.show()
app.processEvents()

# ── 1) 折りたたみ ───────────────────────────────────────────────
check(not row.is_collapsed(), "既定は開いている")
check(row._body.isVisible(), "開いていれば中身が見える")
row.set_collapsed(True)
app.processEvents()
check(row.is_collapsed(), "畳める")
check(not row._body.isVisible(), "畳んだら中身が隠れる")
check(row._head.isVisible(), "見出しは残る")
check("D:/Work/Proj" in row._title.text() and "{Group}" in row._title.text(),
      "見出しに ルート / 対象階層 が出る（%r）" % row._title.text())
row.set_collapsed(False)
app.processEvents()
check(not row.is_collapsed() and row._body.isVisible(), "開き直せる")

# 見出しは書き換えに追従する
row._dir_edit.setText("E:/Another")
app.processEvents()
check("E:/Another" in row._title.text(), "ルートを変えたら見出しも変わる")
row._dir_edit.setText("D:/Work/Proj")

# ── 2) 変数のプルダウン ─────────────────────────────────────────
items = [row._var_combo.itemText(i) for i in range(row._var_combo.count())]
check(items[:3] == ["{Group}", "{Set}", "{Item}"],
      "対象階層の変数が順番どおり並ぶ（%r）" % (items,))
check("{ver}" in items, "{ver} も選べる（%r）" % (items,))
row._levels_edit.setText("{Chara}/{Cut}")
app.processEvents()
items = [row._var_combo.itemText(i) for i in range(row._var_combo.count())]
check(items == ["{Chara}", "{Cut}", "{ver}"],
      "対象階層を書き替えたら追従する（%r）" % (items,))

# 同じ変数を 2 段で使っても重複して並ばない
row._levels_edit.setText("{Sec}/{Sec}/{Cut}")
app.processEvents()
items = [row._var_combo.itemText(i) for i in range(row._var_combo.count())]
check(items == ["{Sec}", "{Cut}", "{ver}"], "重複は 1 つにまとめる（%r）" % (items,))

# ── 3) 挿入はカーソル位置へ ─────────────────────────────────────
row._levels_edit.setText("{Group}/{Item}")
app.processEvents()
row._file_edit.setText("AB_CD")
row._file_edit.setCursorPosition(3)          # "AB_" と "CD" の間
row._var_combo.setCurrentIndex(
    [row._var_combo.itemText(i) for i in range(row._var_combo.count())]
    .index("{Item}"))
row._insert_var()
check(row._file_edit.text() == "AB_{Item}CD",
      "カーソル位置に入る（%r）" % row._file_edit.text())
check(row._file_edit.cursorPosition() == 3 + len("{Item}"),
      "入れた直後の位置が変数の後ろ（%d）" % row._file_edit.cursorPosition())

# 選択していたら置き換える
row._file_edit.setText("XX_YY")
row._file_edit.setSelection(0, 2)
row._insert_var()
check(row._file_edit.text() == "{Item}_YY",
      "選択していたら置き換える（%r）" % row._file_edit.text())

# ── 4) 書いた内容は今までどおり取り出せる ───────────────────────
row._file_edit.setText("{Group}_{Item}_{ver}")
d, data = row.get_data()
check(d == "D:/Work/Proj", "ルートが取れる（%r）" % (d,))
check(data["levels"] == "{Group}/{Item}", "対象階層が取れる（%r）" % (data["levels"],))
check(data["filename"] == "{Group}_{Item}_{ver}",
      "ファイル名が取れる（%r）" % (data["filename"],))

# ── 5) プリセット毎のエディタでも同じ行が使われる ───────────────
from ui.quick_nav_editor import QuickNavPresetEditor
ed = QuickNavPresetEditor(sm)
ed.resize(900, 600)
r2 = ed._add_naming_rule("D:/X", dict(RULE))
app.processEvents()
check(isinstance(r2, AutoNamingRuleRow), "プリセット毎のエディタでも同じ行")
check(r2.is_collapsed(), "読み込んだ既存の規則は畳んで並ぶ")
r3 = ed._add_naming_rule()
app.processEvents()
check(not r3.is_collapsed(), "«＋ ルールを追加» で作った分は開いたまま")
check(hasattr(r2, "_var_combo") and hasattr(r2, "_var_btn"),
      "どのプリセットでも変数の挿入が使える")
ed.close()

finish(not fails)
