# -*- coding: utf-8 -*-
"""r98: Manager で開いているフォルダもリネーム／移動でき、
現在地が消えたら «実在する一番近い親» へ移動する。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, run, QTest

b = make_panel(1200, 700)
root = tmpdir()
deep = os.path.join(root, "proj", "scenes", "cut010")
os.makedirs(deep)
open(os.path.join(deep, "a.ma"), "w").close()
b.navigate_to(deep)


def _settle(ms=400):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    # ── 一番近い «実在する親» の算出 ──────────────────────────────
    gone = os.path.join(root, "proj", "nope", "deeper", "x")
    assert b.nearest_existing_dir(gone) == os.path.join(root, "proj"), \
        b.nearest_existing_dir(gone)
    assert b.nearest_existing_dir(deep) == deep

    # ── 開いているフォルダをリネーム → 新しい場所へ追従する ──────
    scenes = os.path.join(root, "proj", "scenes")
    new_scenes = os.path.join(root, "proj", "shots")
    ok, err = b._rename_path(scenes, new_scenes)
    assert ok, ("開いているフォルダをリネームできない", err)
    b._on_fs_file_renamed(os.path.join(root, "proj"), "scenes", "shots",
                          record=False)
    _settle(900)
    cur = (b.current_path() or "").replace("\\", "/")
    assert cur.rstrip("/") == os.path.join(new_scenes, "cut010").replace("\\", "/"), \
        ("リネーム後の場所へ追従していない", cur)
    print("renaming the open folder follows to the new path: OK")

    # ── 現在地が外部から消えた → 一番近い親へ逃げる ───────────────
    import shutil
    shutil.rmtree(os.path.join(new_scenes, "cut010"))
    assert b._fallback_to_existing_ancestor(), "現在地消失を検出できない"
    _settle(900)
    cur = (b.current_path() or "").replace("\\", "/")
    assert cur.rstrip("/") == new_scenes.replace("\\", "/"), \
        ("一番近い親へ移動していない", cur)
    print("falls back to the nearest existing ancestor: OK")

    # ── 監視タイマーが動いている（外部操作でも自動で逃げる） ──────
    assert b._gone_watch.isActive(), "消失監視タイマーが止まっている"
    print("watchdog active: OK")
    finish(True)


run(s1, delay=900)
