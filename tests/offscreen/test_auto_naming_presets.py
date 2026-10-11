# -*- coding: utf-8 -*-
"""自動命名は «プリセット毎»（r119）。規則は «フォルダに付く»（r139）。

r119 のユーザー指示:
  「自動命名はプリセット設定にタブ分けでおいてください。
    プリセット毎に自動命名は変えたいです。」

r139 のユーザー指示 2026-10-08:
  「各プリセットで設定した命名規則ですが、他のプリセットにセットされて
    いる場合でも、規定のディレクトリで保存する際は反応するようにして
    ください。」
  「（設定ダイアログの自動命名／「このプリセット専用の自動命名を使う」は）
    不要だと思いますが、何か意図がありますか？」

r139 で変えた約束:
* 規則は **フォルダに付く**。どのプリセットに入れてあっても、そのフォルダへ
  保存するなら効く。選択中のプリセットで «効く／効かない» が変わらない。
* 「このプリセット専用の自動命名を使う」は廃止。共通へのフォールバックは
  «プリセット毎設定を後から足した時の後方互換» で、ユーザー向けの意図は
  無かった。各プリセットは常に自前の規則を持つ。
* 旧「共通ルール」は、自前を持たないプリセットを開いた時に下書きとして
  引き継ぎ、保存で確定する（見えないまま消えない）。

変わらない約束:
* プリセットを行き来しても編集中の内容が消えない（下書きに退避する）。
* 改名・複製・削除に自動命名の設定が追従する。
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run

from ui.quick_nav_editor import QuickNavPresetEditor as QuickNavEditor

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _row(name):
    return [name] if name else []


def _select(ed, name):
    names = [ed._preset_list.item(i).text()
             for i in range(ed._preset_list.count())]
    ed._preset_list.setCurrentRow(names.index(name))
    app.processEvents()


def s1():
    # ── 1) 保存・読み出しの素の動き ──────────────────────────────────
    sm.set("auto_naming_enabled", True, save=False)
    sm.save_auto_naming_rules({})
    sm.clear_auto_naming_for_preset("P1")
    sm.clear_auto_naming_for_preset("P2")

    P1R = {"levels": "{Ep}/c{Cut}", "filename": "p1_{Ep}_{Cut}_{ver}.ma",
           "ver": {"latest_label": "cur", "prefix": "v", "digits": 3}}
    P2R = {"levels": "{Ep}", "filename": "p2_{Ep}_{ver}.ma",
           "ver": {"latest_label": "cur", "prefix": "v", "digits": 3}}
    sm.save_auto_naming_for_preset("P1", {"/root1": P1R}, enabled=True)
    check(sm.has_auto_naming_for_preset("P1"), "プリセットに規則を保存できる")
    check(list(sm.get_auto_naming_rules("P1")) == ["/root1"],
          "そのプリセットの規則が引ける")

    # ── 2) r139: 規則はフォルダに付く（選択中のプリセットで変わらない）──
    sm.save_auto_naming_for_preset("P2", {"/root2": P2R}, enabled=True)
    for cur in ("P1", "P2", "default"):
        sm.set("quick_nav_preset", cur, save=False)
        r1, v1, src1 = sm.resolve_auto_name_rule("/root1/S17/c008")
        r2, v2, src2 = sm.resolve_auto_name_rule("/root2/S17")
        check(r1 is not None and src1 == "P1" and v1.get("Cut") == "008",
              "%s を選んでいても /root1 は P1 の規則で効く（%r %r）"
              % (cur, src1, v1))
        check(r2 is not None and src2 == "P2",
              "%s を選んでいても /root2 は P2 の規則で効く（%r）"
              % (cur, src2))
    check(sm.resolve_auto_name_rule("/elsewhere/a/b")[0] is None,
          "関係ないフォルダには口を出さない")
    # r140: 対象階層と深さが違えば効かない
    check(sm.resolve_auto_name_rule("/root1/S17")[0] is None,
          "ルート以下ならどこでも、では無い")

    # 無効にしたプリセットの規則は効かない
    sm.save_auto_naming_for_preset("P2", {"/root2": P2R}, enabled=False)
    check(sm.resolve_auto_name_rule("/root2/S17")[0] is None,
          "無効にしたプリセットの規則は効かない")
    sm.save_auto_naming_for_preset("P2", {"/root2": P2R}, enabled=True)

    # より深い（具体的な）ルートが勝つ
    deep_rule = {"levels": "c{Cut}", "filename": "deep_{Cut}_{ver}.ma"}
    sm.save_auto_naming_for_preset("P2", {"/root2": P2R,
                                          "/root1/S17": deep_rule},
                                   enabled=True)
    rule, _v, src = sm.resolve_auto_name_rule("/root1/S17/c008")
    check(rule is not None and rule.get("filename") == "deep_{Cut}_{ver}.ma",
          "より深いルートが勝つ（%r / %r）" % (rule, src))
    sm.save_auto_naming_for_preset("P2", {"/root2": P2R}, enabled=True)

    # 全体のキルスイッチ
    sm.set("auto_naming_enabled", False, save=False)
    check(sm.resolve_auto_name_rule("/root1/S17/c008")[0] is None,
          "全体を無効にすれば何も効かない")
    sm.set("auto_naming_enabled", True, save=False)
    print("rules belong to the folder, not to the selected preset: OK",
          flush=True)

    # 改名・解除
    sm.rename_auto_naming_preset("P1", "P1b")
    check(sm.has_auto_naming_for_preset("P1b")
          and not sm.has_auto_naming_for_preset("P1"), "改名に設定が付いてくる")
    sm.clear_auto_naming_for_preset("P1b")
    sm.clear_auto_naming_for_preset("P2")
    check(not sm.has_auto_naming_for_preset("P1b"), "解除できる")

    # ── 3) エディタ: 「専用を使う」は無く、常に自前を持つ ──────────────
    sm.save_quick_nav_presets({"A": [], "B": []})
    sm.set("quick_nav_preset", "A", save=False)
    sm.clear_auto_naming_for_preset("A")
    sm.clear_auto_naming_for_preset("B")
    # 旧「共通ルール」が残っている状態から始める（引き継ぎを見るため）
    sm.save_auto_naming_rules(
        {"/legacy": {"levels": "{Ep}", "filename": "L_{Ep}_{ver}.ma"}})

    ed = QuickNavEditor(sm)
    titles = [ed._tabs.tabText(i) for i in range(ed._tabs.count())]
    check(any("自動命名" in t or "Auto" in t for t in titles),
          "自動命名タブがある（%s）" % titles)
    check(not hasattr(ed, "_naming_own_cb"),
          "「このプリセット専用の自動命名を使う」は廃止されている")

    _select(ed, "A")
    check(ed._naming_enabled_cb.isEnabled(), "いつでも編集できる")
    # r139: 旧共通ルールが下書きとして引き継がれている
    got = sorted(r.get_data()[0] for r in ed._naming_rows)
    check(got == ["/legacy"],
          "旧共通ルールが下書きとして引き継がれる（%s）" % (got,))

    ed._add_naming_rule("/a", {"levels": "{Ep}",
                               "filename": "a_{Ep}_{ver}.ma"})

    # B へ行って戻っても A の編集が残る
    _select(ed, "B")
    check(all(r.get_data()[0] != "/a" for r in ed._naming_rows),
          "B に A のルールが漏れている")
    _select(ed, "A")
    got = sorted(r.get_data()[0] for r in ed._naming_rows)
    check(got == ["/a", "/legacy"], "切り替えで A の編集が消えた（%s）" % (got,))
    print("edits survive switching presets back and forth: OK", flush=True)

    ed._save_and_close()
    app.processEvents()
    check(sm.has_auto_naming_for_preset("A"), "保存されていない")
    check(sorted(sm.get_auto_naming_rules("A")) == ["/a", "/legacy"],
          "A の規則（%s）" % (sorted(sm.get_auto_naming_rules("A")),))
    check(not sm.get_auto_naming_rules(None),
          "引き継いだ旧共通ルールは空にする（見えないまま効き続けさせない）")

    # 保存後も «フォルダに付く» こと
    sm.set("quick_nav_preset", "B", save=False)
    rule, _v, src = sm.resolve_auto_name_rule("/a/S17")
    check(rule is not None and src == "A",
          "B を選んでいても A の規則が /a で効く（%r）" % (src,))

    # ── 4) 改名すると設定も付いてくる ─────────────────────────────
    ed2 = QuickNavEditor(sm)
    _select(ed2, "A")
    got = sorted(r.get_data()[0] for r in ed2._naming_rows)
    check(got == ["/a", "/legacy"], "保存した設定が読み込まれない（%s）" % (got,))
    ed2._name_edit.setText("A2")
    ed2._apply_rename()
    ed2._save_and_close()
    app.processEvents()
    check(sm.has_auto_naming_for_preset("A2"), "改名に設定が付いてこない")
    check(not sm.has_auto_naming_for_preset("A"), "古い名前が残っている")
    print("renaming a preset carries its auto-naming with it: OK", flush=True)

    sm.save_auto_naming_rules({})
    finish(not fails)


run(s1, delay=300)
