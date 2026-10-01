# -*- coding: utf-8 -*-
"""r119: «Maya バージョン別の履歴/ブックマーク» が効く範囲。

ユーザー指摘 2026-10-01:
  「これは Manager の Maya のバージョンプルダウンの切り替えで変わると
    いうことですか？ Maya が開いている前提でのツールではないので…」

そのとおりで、設計が壊れていた。_on_maya_version_changed（= «次に起動する
Maya» を選ぶだけの操作）が set_maya_version() を呼んでおり、プルダウンを
触っただけでブックマーク/履歴の箱が入れ替わっていた。スタンドアロンが主な
使い方なので «ブックマークが消えた» に見える。

約束:
* バージョン別の箱は «Maya の中で動いている時» だけ。
* ヘッダーのプルダウンは箱を切り替えない。
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run

import ui.main_window_dcc as mwd
from core.maya_version import MayaInstallation


def s1():
    sm.set("history_per_maya", True, save=False)
    sm.set("bookmarks_per_maya", True, save=False)

    # ── 1) スタンドアロン想定: 箱は共通（バージョン文脈なし）────────
    sm.set_maya_version(None)
    assert sm.get_active_maya_version() is None
    sm.add_to_history("/shared/a")
    assert "/shared/a" in sm.get_history()

    # ── 2) «起動する Maya» を切り替えても箱は変わらない ──────────────
    class _FakeCombo:
        def __init__(self, data): self._d = data
        def itemData(self, i): return self._d

    class _Host(mwd.MainWindowDccMixin):
        def __init__(self):
            self._sm = sm
            self._maya_inst = None
            self._blender_inst = None
            self._maya_combo = None

        def setWindowTitle(self, *_a):      # QMainWindow の代わり
            pass

    host = _Host()
    from pathlib import Path as _P
    inst = MayaInstallation(version="2026", path=_P("/fake/maya2026"))
    host._maya_combo = _FakeCombo(inst)
    host._on_maya_version_changed(0)
    assert host._maya_inst is inst, "選択が反映されていない"
    assert sm.get_active_maya_version() is None, \
        "プルダウンの切り替えでバージョン文脈が変わっている"
    assert "/shared/a" in sm.get_history(), \
        "プルダウンを触っただけで履歴が入れ替わった"
    # «次に起動する版» としては覚えている
    assert sm.get("last_maya_version") == "2026"
    print("changing the launch dropdown never switches the history box: OK")

    # ── 3) Maya の中で動いている時だけ箱が分かれる ──────────────────
    sm.set_maya_version("2026")
    assert sm.get_active_maya_version() == "2026"
    assert "/shared/a" not in sm.get_history(), "2026 の箱に共通履歴が混ざる"
    sm.add_to_history("/in2026/x")
    assert "/in2026/x" in sm.get_history()

    sm.set_maya_version("2025")
    assert "/in2026/x" not in sm.get_history(), "2025 に 2026 の履歴が出る"
    sm.add_to_history("/in2025/y")
    sm.set_maya_version("2026")
    assert "/in2026/x" in sm.get_history() and \
        "/in2025/y" not in sm.get_history()

    # 共通へ戻すと元の履歴
    sm.set_maya_version(None)
    assert "/shared/a" in sm.get_history()
    assert "/in2026/x" not in sm.get_history()
    print("per-version boxes are separate and only used inside Maya: OK")

    # ── 4) OFF なら文脈があっても共通 ───────────────────────────────
    sm.set("history_per_maya", False, save=False)
    sm.set_maya_version("2026")
    assert "/shared/a" in sm.get_history(), "OFF なのに分かれている"
    sm.set_maya_version(None)
    sm.set("bookmarks_per_maya", False, save=False)
    print("the switch really is a switch: OK")
    finish(True)


run(s1, delay=200)
