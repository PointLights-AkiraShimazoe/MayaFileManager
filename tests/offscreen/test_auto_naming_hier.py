# -*- coding: utf-8 -*-
"""r140: 自動命名は «ルート + 対象階層 + ファイル名 + ver 設定» で指定する。

ユーザー指示 2026-10-08（例をそのまま使う）::

    D:/Work/SampleProject/A01/02/n003/
        SampleProject_A01_02_003.ma  ← 拡張子は保存時に付く

    ルートディレクトリ : D:/Work/SampleProject/
    対象階層           : {Group}/{Set}/n{Item}/
    ファイル名         : {Group}_{Set}_{Item}_{ver}   ← 拡張子は書かない

固定すること:
  1) 変数は固定で持たない。対象階層に書いた {...} がそのまま変数になる
  2) **ルート以下ならどこでも、ではない。** 対象階層と同じ深さだけ
  3) {ver} だけ特別（最新表記 / 接頭辞 / 桁数 / インクリメンタルセーブ）
  4) インクリメンタル ON  … 既存の最新を «次の番号» へ退避し、最新表記で保存
  5) インクリメンタル OFF … 一致するファイルの最大番号 +1（無ければ 1）
  6) ファイルを選んでいて、かつインクリメンタルが OFF なら口を出さない
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, tmpdir, finish, run

from core import auto_name as an

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


LEVELS = "{Group}/{Set}/n{Item}"
# r145: 設定に拡張子は入れない（保存時に選んだ形式が付く）
FILENAME = "SampleProject_{Group}_{Set}_{Item}_{ver}"


def _rule(incremental=False, digits=3, latest="cur", prefix="v"):
    return {"levels": LEVELS, "filename": FILENAME,
            "ver": {"latest_label": latest, "prefix": prefix,
                    "digits": digits, "incremental": incremental}}


def s1():
    root = os.path.join(tmpdir(), "SampleProject")
    deep = os.path.join(root, "A01", "02", "n003")
    os.makedirs(deep)
    shallow = os.path.join(root, "A01", "02")

    # ── 1) 変数は対象階層から決まる ──────────────────────────────────
    vals = an.match_levels(root, deep, LEVELS)
    check(vals == {"Group": "A01", "Set": "02", "Item": "003"},
          "対象階層から変数を拾う（%r）" % (vals,))
    check(an.level_vars(LEVELS)
          == ["Group", "Set", "Item"], "変数名の並び")

    # ── 2) ルート以下ならどこでも、ではない ──────────────────────────
    check(an.match_levels(root, shallow, LEVELS) is None,
          "浅すぎる階層は対象にしない")
    check(an.match_levels(root, os.path.join(deep, "more"), LEVELS) is None,
          "深すぎる階層は対象にしない")
    check(an.match_levels(root, root, LEVELS) is None, "ルート直下も対象外")
    # 固定文字（n{Item} の n）も見る
    other = os.path.join(root, "A01", "02", "x003")
    os.makedirs(other)
    check(an.match_levels(root, other, LEVELS) is None,
          "固定文字が合わないフォルダは対象にしない")

    # ── 3) 名前の組み立て ───────────────────────────────────────────
    name = an.expand(FILENAME, dict(vals, ver="cur"))
    check(name == "SampleProject_A01_02_003_cur", name)
    check(an.missing_vars(_rule()) == [], "未定義の変数は無い")
    check(an.missing_vars({"levels": "{Group}", "filename": "{Group}_{Nope}"})
          == ["Nope"], "未定義の変数を見つけられる")

    # ── 5) インクリメンタル OFF: 最大 +1（無ければ 1）────────────────
    r = _rule(incremental=False)
    check(an.suggest_name(deep, r, vals, False)
          == "SampleProject_A01_02_003_v001",
          "一致が無ければ 1（%s）" % an.suggest_name(deep, r, vals, False))
    for v in ("v001", "v002", "cur"):
        open(os.path.join(deep, "SampleProject_A01_02_003_%s.ma" % v), "w").close()
    # 紛らわしいファイルは数に入れない
    open(os.path.join(deep, "SampleProject_A01_02_OTHER_v009.ma"), "w").close()
    open(os.path.join(deep, "memo.txt"), "w").close()
    nums, latest = an.scan_versions(deep, r, vals)
    check(sorted(nums) == [1, 2], "番号の拾い方（%s）" % (sorted(nums),))
    check(latest == "SampleProject_A01_02_003_cur.ma", "最新ファイル（%s）" % latest)
    check(an.suggest_name(deep, r, vals, False)
          == "SampleProject_A01_02_003_v003",
          "最大 +1（%s）" % an.suggest_name(deep, r, vals, False))

    # ── 4) インクリメンタル ON: 退避してから最新表記で保存 ────────────
    r2 = _rule(incremental=True)
    check(an.suggest_name(deep, r2, vals, True)
          == "SampleProject_A01_02_003_cur",
          "ON の時は最新表記で保存する（%s）" % an.suggest_name(deep, r2, vals, True))
    dst, err = an.archive_latest(deep, r2, vals)
    check(err is None and dst == "SampleProject_A01_02_003_v003.ma",
          "既存の最新を «次の番号» へ退避（%r, %r）" % (dst, err))
    check(os.path.isfile(os.path.join(deep, dst)), "退避先が実在する")
    check(os.path.isfile(os.path.join(deep,
                                      "SampleProject_A01_02_003_v002.ma")),
          "既存の v002 を潰していない")
    check(os.path.isfile(os.path.join(deep, latest)),
          "退避は複製（最新ファイルはまだ残る＝この後 DCC が上書きする）")

    # ── r145: 拡張子は設定に持たない（ユーザー指示 2026-10-09）─────────
    #   「ファイル名ですが、拡張子は設定には入れず、保存時には選択されている
    #     拡張子を付けてください。」
    check(an.stem_template({"filename": "{A}_{ver}.ma"}) == "{A}_{ver}",
          "古い設定に残った拡張子は落とす")
    check(an.stem_template({"filename": "{A}_{ver}"}) == "{A}_{ver}",
          "拡張子が無ければそのまま")
    check(an.stem_template({"filename": "{A}.{ver}"}) == "{A}.{ver}",
          "拡張子の位置が変数なら落とさない")
    # 最新ファイルがまだ無い時は何もしない
    fresh = os.path.join(root, "B02", "01", "n001")
    os.makedirs(fresh)
    fvals = an.match_levels(root, fresh, LEVELS)
    dst2, err2 = an.archive_latest(fresh, r2, fvals)
    check(dst2 == "" and err2 is None, "退避するものが無ければ何もしない")

    # 桁数・接頭辞・最新表記を変えられる
    r3 = _rule(incremental=False, digits=2, prefix="ver", latest="LATEST")
    check(an.suggest_name(fresh, r3, fvals, False)
          == "SampleProject_B02_01_001_ver01",
          an.suggest_name(fresh, r3, fvals, False))
    check(an.suggest_name(fresh, r3, fvals, True)
          == "SampleProject_B02_01_001_LATEST",
          an.suggest_name(fresh, r3, fvals, True))

    # ── 6) 保存ダイアログ ───────────────────────────────────────────
    sm.set("auto_naming_enabled", True, save=False)
    sm.set("quick_nav_preset", "default", save=False)
    sm.save_auto_naming_rules({})
    sm.save_auto_naming_for_preset("default", {root: _rule(incremental=False)},
                                   enabled=True)
    from ui.save_dialog import SaveDialog

    # 何も選ばずに保存 → 提案する
    dlg = SaveDialog("maya", "save", deep, "", sm=sm)
    app.processEvents()
    check(dlg._name_edit.text() == "SampleProject_A01_02_003_v004.ma",
          "何も選ばずなら提案する（%s）" % dlg._name_edit.text())
    # r140b: «出すつもりか» は _incr_available で見る。ダイアログを show() して
    # いない間、子ウィジェットの isVisible() は常に False なので判定に使えない。
    check(not dlg._incr_available,
          "ルールが OFF ならインクリメンタルのスイッチを出さない")
    dlg.deleteLater()

    # ファイルを選んでいて、ルールが OFF → 口を出さない
    dlg2 = SaveDialog("maya", "save", deep, "scene.ma", sm=sm)
    app.processEvents()
    check(dlg2._name_edit.text().startswith("scene"),
          "選んでいる時は選択名のまま（%s）" % dlg2._name_edit.text())
    dlg2.deleteLater()

    # ルールが ON → スイッチが出て、ON の間は最新表記
    sm.save_auto_naming_for_preset("default", {root: _rule(incremental=True)},
                                   enabled=True)
    dlg3 = SaveDialog("maya", "save", deep, "scene.ma", sm=sm)
    app.processEvents()
    check(dlg3._incr_available, "ルールが ON ならスイッチを出す")
    dlg3._incr_cb.setChecked(True)
    app.processEvents()
    check(dlg3._name_edit.text() == "SampleProject_A01_02_003_cur.ma",
          "ON の間は最新表記（%s）" % dlg3._name_edit.text())
    dlg3._incr_cb.setChecked(False)
    app.processEvents()
    check(dlg3._name_edit.text() == "SampleProject_A01_02_003_v004.ma",
          "OFF に戻すと番号（%s）" % dlg3._name_edit.text())
    dlg3.deleteLater()

    # 対象外の階層では何も起きない
    dlg4 = SaveDialog("maya", "save", shallow, "scene.ma", sm=sm)
    app.processEvents()
    check(dlg4._name_edit.text().startswith("scene"),
          "対象階層でなければ口を出さない（%s）" % dlg4._name_edit.text())
    check(not dlg4._incr_available, "対象外ではスイッチも出さない")
    dlg4.deleteLater()

    sm.clear_auto_naming_for_preset("default")
    # 拡張子違いも同じバージョンとして数える（.ma と .mb が並んでいても）。
    # ここは «ファイルを増やす» ので、番号に依存する検証より後ろで行う。
    open(os.path.join(deep, "SampleProject_A01_02_003_v007.mb"), "w").close()
    nums2, _l2 = an.scan_versions(deep, r, vals)
    check(7 in nums2, "拡張子が違っても同じ名前なら数える（%s）" % (sorted(nums2),))

    finish(not fails)


run(s1, delay=300)
