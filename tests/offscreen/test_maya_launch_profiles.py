# -*- coding: utf-8 -*-
"""r119: Maya の起動プロファイル（同じバージョンで別引数を持てる）。

ユーザー指示:
  「同じ maya バージョンでも別引数を持たせたいことはあると思うので、
    サーチされた Maya バージョンを羅列し、空のテキストフィールドを置き、
    行が追加出来るようにし、maya バージョンはプルダウンで選べる様にして…
    マネージャーのプルダウンへの表示名の設定も出来る様にしておいてください。」

* 1 行 = （バージョン, 表示名, 追加引数）。同じバージョンを何行でも置ける。
* 行が無ければ従来どおり «検出されたバージョンそのまま»。
* 表示名が空なら「Maya <版>」。
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, Qt

from ui.maya_launch_dialog import MayaLaunchDialog


def s1():
    sm.save_maya_launch_profiles([])
    sm.set("maya_extra_args", "", save=False)

    # ── 1) 保存と読み出し ────────────────────────────────────────────
    sm.save_maya_launch_profiles([
        {"id": "a", "version": "2026", "label": "2026 通常", "args": ""},
        {"id": "b", "version": "2026", "label": "2026 バッチ", "args": "-batch"},
        {"id": "c", "version": "2025", "label": "", "args": "-nosplash"},
        {"version": "", "label": "捨てられる行"},     # version 無しは保存しない
    ])
    got = sm.get_maya_launch_profiles()
    assert len(got) == 3, got
    assert [g["version"] for g in got] == ["2026", "2026", "2025"], got
    assert got[0]["label"] == "2026 通常" and got[1]["args"] == "-batch"
    print("same version can appear on several rows, each with its own args: OK")

    # ── 2) ダイアログ: 行の追加・バージョンはプルダウン・表示名 ──────
    dlg = MayaLaunchDialog(sm, ["2026", "2025", "2023"])
    assert len(dlg._rows) == 3, len(dlg._rows)
    r0 = dlg._rows[0]
    assert r0.ver.count() == 3, r0.ver.count()           # 検出版がプルダウンに
    assert r0.ver.currentData() == "2026"
    assert r0.label.text() == "2026 通常"
    assert dlg._rows[1].args.text() == "-batch"
    # 行の追加（空の引数欄つき）
    n = len(dlg._rows)
    new_row = dlg.add_row()
    assert len(dlg._rows) == n + 1
    assert new_row.args.text() == "" and new_row.label.text() == ""
    new_row.ver.setCurrentIndex(new_row.ver.findData("2023"))
    new_row.label.setText("2023 検証用")
    new_row.args.setText("-noAutoloadPlugins")
    # 行の削除
    dlg._remove_row(dlg._rows[2])
    vers = [r.get_data()["version"] for r in dlg._rows]
    assert vers == ["2026", "2026", "2023"], vers
    print("rows can be added and removed; version is a dropdown: OK")

    dlg._save()
    app.processEvents()
    saved = sm.get_maya_launch_profiles()
    assert [p["version"] for p in saved] == ["2026", "2026", "2023"], saved
    assert saved[2]["label"] == "2023 検証用"
    assert saved[2]["args"] == "-noAutoloadPlugins"
    # id は行ごとに別（同じバージョンでも衝突しない）
    assert len({p["id"] for p in saved}) == 3, saved
    print("saving keeps one id per row so same-version rows stay distinct: OK")

    # ── 3) 未検出のバージョンでも行が消えない ────────────────────────
    sm.save_maya_launch_profiles([{"id": "z", "version": "2019",
                                   "label": "旧案件", "args": ""}])
    dlg2 = MayaLaunchDialog(sm, ["2026"])
    assert len(dlg2._rows) == 1
    assert dlg2._rows[0].get_data()["version"] == "2019", "未検出版が落ちている"
    print("a profile for a version that is not installed is kept: OK")

    # ── 4) 共通引数はダイアログから保存される ────────────────────────
    dlg2._common_args.setText("-hideConsole")
    dlg2._save()
    app.processEvents()
    assert sm.get("maya_extra_args") == "-hideConsole"
    print("shared extra arguments round-trip: OK")

    dlg.deleteLater(); dlg2.deleteLater()
    sm.save_maya_launch_profiles([])
    sm.set("maya_extra_args", "", save=False)
    finish(True)


run(s1, delay=300)
