# -*- coding: utf-8 -*-
"""r146: 自動命名ルールは一覧で持つ／複製ボタン／畳んだ高さ／全体 OFF の注意。

ユーザー報告・指示 2026-10-16:
  「プリセットを保存しても増やした分が保存されていなかった」
    → ルートをキーにした辞書だったので、同じルートの 2 本目が 1 本目を
      上書きしていた。一覧 [(root, rule)] で持つ（古い辞書形式も読める）
  「ルール通りの場所で保存しようとしてもルールが適用されず」
    → 実機では設定の全体スイッチが OFF だった（同じ文言のスイッチが
      2 か所）。全体スイッチは廃止し、プリセット側だけにする
  「畳んだ時の高さがデカすぎる。内容に合わせて」
  「ルールを複製するボタンも ✕ の左隣に」
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest, Qt
from ui.quick_nav_editor import QuickNavPresetEditor as QuickNavEditor
from ui.settings_dialog import AutoNamingRuleRow

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


R1 = {"levels": "Seq{Seq}/{Cut}", "filename": "{Seq}_{Cut}", "ver": {}}
R2 = {"levels": "{Section}/{Asset}/Models", "filename": "SK_{Asset}", "ver": {}}

# ── 1) 同じルートの規則を 2 本保存して、2 本とも残る ─────────────
sm.save_auto_naming_for_preset("T", [("/proj", R1), ("/proj", R2)], enabled=True)
lst = sm.get_auto_naming_rule_list("T")
check(len(lst) == 2 and lst[0][1]["levels"] == R1["levels"] and lst[1][1]["levels"] == R2["levels"],
      "同じルートの 2 本がそのまま残る（%d 本）" % len(lst))
# 古い辞書形式も読める
sm._global_state["auto_naming_by_preset"]["OLD"] = {"enabled": True, "rules": {"/old": R1}}
check(sm.get_auto_naming_rule_list("OLD") == [("/old", R1)], "旧い辞書形式も一覧で読める")
check(sm.get_auto_naming_rules("OLD") == {"/old": R1}, "互換用の辞書も返る")
# 解決は両方の規則を見る
sm.set("auto_naming_enabled", True, save=False)
sm.set("quick_nav_preset", "T", save=False)
rule, vals, src = sm.resolve_auto_name_rule("/proj/PRP/Shovel/Models")
check(rule is not None and rule["levels"] == R2["levels"] and vals.get("Asset") == "Shovel",
      "2 本目の規則で解決できる（%r）" % (vals,))
rule, vals, src = sm.resolve_auto_name_rule("/proj/Seq01/Layout")
check(rule is not None and rule["levels"] == R1["levels"], "1 本目の規則でも解決できる")

# ── 2) エディタ: 読み込み → 行が 2 本、複製ボタン、畳んだ高さ ──────
ed = QuickNavEditor(sm)
ed.resize(900, 700)
ed.show()
QTest.qWait(300)
app.processEvents()
items = ed._preset_list.findItems("T", Qt.MatchExactly)
if not items:
    ed._presets["T"] = []
    ed._preset_list.addItem("T")
    items = ed._preset_list.findItems("T", Qt.MatchExactly)
ed._preset_list.setCurrentItem(items[0])
for i in range(ed._tabs.count()):
    if "自動命名" in ed._tabs.tabText(i) or "Auto" in ed._tabs.tabText(i):
        ed._tabs.setCurrentIndex(i)
QTest.qWait(300)
app.processEvents()
rows = ed._naming_rows
check(len(rows) == 2, "エディタに 2 本とも出る（%d）" % len(rows))
check(all(r.is_collapsed() for r in rows), "既存の規則は畳まれて出る")
h = rows[0].height()
check(h <= rows[0]._head.height() + 16,
      "畳んだ規則は見出しの高さだけ（%d / 見出し %d）" % (h, rows[0]._head.height()))
check(hasattr(rows[0], "_dup_btn"), "✕ の左隣に複製ボタンがある")
head_lay = rows[0]._head.layout()
order = [head_lay.itemAt(i).widget() for i in range(head_lay.count())]
check(order.index(rows[0]._dup_btn) == len(order) - 2, "複製は ✕ の左隣")
rows[0]._dup_btn.click()
QTest.qWait(100)
app.processEvents()
check(len(ed._naming_rows) == 3 and ed._naming_rows[1].get_data()[1]["levels"] == R1["levels"]
      and not ed._naming_rows[1].is_collapsed(),
      "複製はすぐ下に開いた状態で並ぶ")
saved = ed._collect_naming()["rules"]
check(len(saved) == 3, "複製した分も保存対象に入る（%d）" % len(saved))

# ── 3) 全体スイッチは廃止（ユーザー指示 2026-10-16）─────────────
sm.set("auto_naming_enabled", False, save=False)      # 設定ファイルの残骸
check(sm.resolve_auto_name_rule("/proj/Seq01/Layout")[0] is not None,
      "設定の残骸 auto_naming_enabled=False があっても規則は効く")
check(not hasattr(ed, "_naming_global_note"), "エディタに全体スイッチの注意は無い")
from ui.settings_dialog import SettingsDialog
sd = SettingsDialog(sm)
check(not hasattr(sd, "_auto_naming_enabled_cb"), "設定ダイアログに全体スイッチが無い")
sd.close()
ed.close()
finish(not fails)
