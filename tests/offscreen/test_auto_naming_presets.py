# -*- coding: utf-8 -*-
"""r119: 自動命名を «プリセット毎» に持てること。

ユーザー指示: 「自動命名はプリセット設定にタブ分けでおいてください。
プリセット毎に自動命名は変えたいです。」

設計の約束:
* 既存の «共通» 設定（設定ダイアログの自動命名）はそのまま残し、
  プリセットが自前の設定を持たない時のフォールバックにする（移行不要）。
* プリセットを行き来しても編集中の内容が消えない（下書きに退避する）。
* 改名・複製・削除に自動命名の設定が追従する。
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, Qt

from ui.quick_nav_editor import QuickNavPresetEditor as QuickNavEditor


def s1():
    # ── 1) SettingsManager: プリセット毎／共通のフォールバック ────────
    sm.set("auto_naming_enabled", True, save=False)
    sm.save_auto_naming_rules({"/common": {"template": "{seq:04d}",
                                           "seq_start": 1}})
    assert not sm.has_auto_naming_for_preset("P1")
    # 自前の設定が無い → 共通に従う
    assert sm.get_auto_naming_rules("P1") == sm.get_auto_naming_rules()
    assert sm.get_auto_naming_enabled("P1") is True

    sm.save_auto_naming_for_preset(
        "P1", {"/p1": {"template": "shot_{seq:03d}", "seq_start": 10}},
        enabled=False)
    assert sm.has_auto_naming_for_preset("P1")
    assert list(sm.get_auto_naming_rules("P1")) == ["/p1"]
    assert sm.get_auto_naming_enabled("P1") is False
    # 共通は汚されていない
    assert list(sm.get_auto_naming_rules()) == ["/common"]
    assert sm.get_auto_naming_enabled() is True
    # 別プリセットは共通のまま
    assert list(sm.get_auto_naming_rules("P2")) == ["/common"]
    print("per-preset rules override the shared ones, per preset: OK")

    # 選択中プリセットの設定が引ける
    sm.set("quick_nav_preset", "P1", save=False)
    enabled, rules = sm.get_active_auto_naming()
    assert enabled is False and list(rules) == ["/p1"], (enabled, rules)
    sm.set("quick_nav_preset", "P2", save=False)
    enabled, rules = sm.get_active_auto_naming()
    assert enabled is True and list(rules) == ["/common"]
    print("active preset decides which auto-naming applies: OK")

    # 改名・解除
    sm.rename_auto_naming_preset("P1", "P1b")
    assert sm.has_auto_naming_for_preset("P1b") and \
        not sm.has_auto_naming_for_preset("P1")
    sm.clear_auto_naming_for_preset("P1b")
    assert not sm.has_auto_naming_for_preset("P1b")
    assert list(sm.get_auto_naming_rules("P1b")) == ["/common"], "共通へ戻らない"
    print("rename / clear keep the shared fallback intact: OK")

    # ── 2) エディタ: タブと «プリセット毎» の編集 ────────────────────
    sm.save_quick_nav_presets({"A": [], "B": []})
    sm.set("quick_nav_preset", "A", save=False)
    sm.clear_auto_naming_for_preset("A")
    sm.clear_auto_naming_for_preset("B")

    ed = QuickNavEditor(sm)
    titles = [ed._tabs.tabText(i) for i in range(ed._tabs.count())]
    assert len(titles) == 2, titles
    assert any("自動命名" in t or "Auto" in t for t in titles), titles
    print("preset editor has an Auto Naming tab: OK")

    # A に専用設定を入れる
    ed._preset_list.setCurrentRow(
        [ed._preset_list.item(i).text()
         for i in range(ed._preset_list.count())].index("A"))
    app.processEvents()
    assert not ed._naming_own_cb.isChecked(), "既定で専用設定が入っている"
    assert not ed._naming_enabled_cb.isEnabled(), "OFF なのに編集できる"
    ed._naming_own_cb.setChecked(True)
    assert ed._naming_enabled_cb.isEnabled(), "ON にしても編集できない"
    row = ed._add_naming_rule("/a", {"template": "a_{seq:02d}", "seq_start": 5})
    assert row in ed._naming_rows

    # B へ切り替えて戻しても、A の編集が残っている（下書き）
    ed._preset_list.setCurrentRow(
        [ed._preset_list.item(i).text()
         for i in range(ed._preset_list.count())].index("B"))
    app.processEvents()
    assert not ed._naming_own_cb.isChecked(), "B に A の設定が漏れている"
    assert not ed._naming_rows, "B に A のルールが漏れている"
    ed._preset_list.setCurrentRow(
        [ed._preset_list.item(i).text()
         for i in range(ed._preset_list.count())].index("A"))
    app.processEvents()
    assert ed._naming_own_cb.isChecked(), "切り替えで A の編集が消えた"
    assert len(ed._naming_rows) == 1, ed._naming_rows
    d, rule = ed._naming_rows[0].get_data()
    assert d == "/a" and rule["seq_start"] == 5, (d, rule)
    print("edits survive switching presets back and forth: OK")

    ed._save_and_close()
    app.processEvents()
    assert sm.has_auto_naming_for_preset("A"), "保存されていない"
    assert list(sm.get_auto_naming_rules("A")) == ["/a"]
    assert not sm.has_auto_naming_for_preset("B"), "触っていない B に作られた"
    assert list(sm.get_auto_naming_rules("B")) == ["/common"]
    print("saving writes only the presets that opted in: OK")

    # ── 3) 改名すると設定も付いてくる ─────────────────────────────
    ed2 = QuickNavEditor(sm)
    ed2._preset_list.setCurrentRow(
        [ed2._preset_list.item(i).text()
         for i in range(ed2._preset_list.count())].index("A"))
    app.processEvents()
    assert ed2._naming_own_cb.isChecked(), "保存した設定が読み込まれない"
    ed2._name_edit.setText("A2")
    ed2._apply_rename()
    ed2._save_and_close()
    app.processEvents()
    assert sm.has_auto_naming_for_preset("A2"), "改名に設定が付いてこない"
    assert list(sm.get_auto_naming_rules("A2")) == ["/a"]
    assert not sm.has_auto_naming_for_preset("A")
    print("renaming a preset carries its auto-naming with it: OK")
    finish(True)


run(s1, delay=300)
