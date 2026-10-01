# -*- coding: utf-8 -*-
"""r119d: 自動命名が «実際に効く» こと。

ユーザー指摘 2026-10-02:「指定方法がいまいちわかりません」
調べたところ apply_auto_name() に **呼び出し元が 1 つも無く**、設定しても
何も起きない «見かけだけの機能» だった。さらに中身も壊れていた:
  * カウンタの既定ファイル名が UI（.mfm_seq）と実装（.seq_counter）で不一致
  * 連番を一度も進めない
  * {desc} を展開しない
  * startswith 判定なので /projects/CHR が /projects/CHRX にも効く
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, tmpdir, finish, run

from core import file_operations as fo


def s1():
    root = tmpdir()
    chr_dir = os.path.join(root, "CHR")
    chrx_dir = os.path.join(root, "CHRX")
    deep_dir = os.path.join(chr_dir, "A", "wip")
    for d in (chr_dir, chrx_dir, deep_dir):
        os.makedirs(d, exist_ok=True)

    rules = {chr_dir: {"template": "CHR_{seq:04d}", "seq_start": 1},
             deep_dir: {"template": "WIP_{seq}", "seq_start": 7}}

    # ── 1) 一致の境界 ────────────────────────────────────────────────
    d, r = fo.find_auto_name_rule(chr_dir, rules)
    assert d == chr_dir, d
    assert fo.find_auto_name_rule(chrx_dir, rules) == (None, None), \
        "/CHR のルールが /CHRX にも効いている"
    assert fo.find_auto_name_rule(os.path.join(chr_dir, "B"), rules)[0] == chr_dir
    # より深いルールが勝つ
    assert fo.find_auto_name_rule(deep_dir, rules)[0] == deep_dir
    assert fo.find_auto_name_rule(root, rules) == (None, None)
    print("rule matching respects folder boundaries, deepest wins: OK")

    # ── 2) テンプレートの展開 ───────────────────────────────────────
    assert fo.expand_auto_name("CHR_{seq:04d}", 1) == "CHR_0001"
    assert fo.expand_auto_name("CHR_{seq:03d}", 42) == "CHR_042"
    assert fo.expand_auto_name("{seq}", 7) == "7"
    assert fo.expand_auto_name("{name}_{seq:02d}", 3, name="head") == "head_03"
    assert fo.expand_auto_name("{desc}_{seq}", 1, desc="rough") == "rough_1"
    # 空トークンで区切りが残らない
    assert fo.expand_auto_name("{desc}_{seq:02d}", 5) == "05", \
        fo.expand_auto_name("{desc}_{seq:02d}", 5)
    assert fo.expand_auto_name("{folder}_{seq}", 2, directory=chr_dir) == "CHR_2"
    import datetime
    today = datetime.date.today().strftime("%Y%m%d")
    assert fo.expand_auto_name("{date}", 1) == today
    # 未知のトークンは壊さずに残す
    assert "{nope}" in fo.expand_auto_name("x{nope}", 1)
    print("template tokens expand (and unknown ones survive): OK")

    # ── 3) 連番は «保存した時だけ» 進む ─────────────────────────────
    assert fo.apply_auto_name(chr_dir, rules) == "CHR_0001"
    assert fo.apply_auto_name(chr_dir, rules) == "CHR_0001", \
        "提案しただけで番号が進んでいる"
    counter = os.path.join(chr_dir, fo.AUTO_NAME_COUNTER_FILE)
    assert not os.path.exists(counter), "提案だけでカウンタが作られている"
    fo.commit_auto_seq(chr_dir, rules[chr_dir], 1)
    assert os.path.isfile(counter)
    assert fo.apply_auto_name(chr_dir, rules) == "CHR_0002"
    fo.commit_auto_seq(chr_dir, rules[chr_dir], 2)
    assert fo.apply_auto_name(chr_dir, rules) == "CHR_0003"
    # 開始番号はカウンタが無い時だけ使う
    assert fo.apply_auto_name(deep_dir, rules) == "WIP_7"
    print("the counter advances only on an actual save: OK")

    # ── 4) 保存ダイアログが «実際に» 提案する（ここが欠けていた）────
    sm.set("auto_naming_enabled", True, save=False)
    sm.set("quick_nav_preset", "default", save=False)
    sm.clear_auto_naming_for_preset("default")
    sm.save_auto_naming_rules(rules)
    from ui.save_dialog import SaveDialog
    dlg = SaveDialog("maya", "save", chr_dir, "", sm=sm)
    app.processEvents()
    assert dlg._name_edit.text().startswith("CHR_0003"), dlg._name_edit.text()
    dlg.deleteLater()

    # ルールの効かないフォルダでは口を出さない
    dlg2 = SaveDialog("maya", "save", chrx_dir, "scene.ma", sm=sm)
    app.processEvents()
    assert dlg2._name_edit.text().startswith("scene"), dlg2._name_edit.text()
    dlg2.deleteLater()

    # 無効にしたら提案しない
    sm.set("auto_naming_enabled", False, save=False)
    dlg3 = SaveDialog("maya", "save", chr_dir, "scene.ma", sm=sm)
    app.processEvents()
    assert dlg3._name_edit.text().startswith("scene"), dlg3._name_edit.text()
    dlg3.deleteLater()
    sm.set("auto_naming_enabled", True, save=False)
    print("the save dialog actually uses the rule (this was missing): OK")

    # ── 5) プリセット別の設定がここにも効く ─────────────────────────
    # 連番は «フォルダの» カウンタなので、開始番号の違いを見るには
    # まだカウンタの無いフォルダで確かめる。
    fresh = os.path.join(root, "PRESET")
    os.makedirs(fresh, exist_ok=True)
    sm.save_auto_naming_for_preset(
        "default", {fresh: {"template": "P_{seq:02d}", "seq_start": 50}},
        enabled=True)
    dlg4 = SaveDialog("maya", "save", fresh, "", sm=sm)
    app.processEvents()
    assert dlg4._name_edit.text().startswith("P_50"), dlg4._name_edit.text()
    dlg4.deleteLater()
    # 共通ルールは «プリセットが自前を持つ間» 使われない
    dlg5 = SaveDialog("maya", "save", chr_dir, "scene.ma", sm=sm)
    app.processEvents()
    assert dlg5._name_edit.text().startswith("scene"), dlg5._name_edit.text()
    dlg5.deleteLater()
    sm.clear_auto_naming_for_preset("default")
    sm.save_auto_naming_rules({})
    print("per-preset auto-naming reaches the save dialog too: OK")
    finish(True)


run(s1, delay=300)
