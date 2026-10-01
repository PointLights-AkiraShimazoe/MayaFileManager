# -*- coding: utf-8 -*-
"""r83: クラウド（オンラインのみ）対策と、自己修復が選択を壊さないこと。

1) サムネイル生成はオンラインのみのファイルの «内容を読まない»（ダウンロード誘発）
2) 先読みはリスト表示では走らない／オンラインのみは対象外
3) 遅延実行される _force_column_rebuild は、複数選択中・ユーザー選択後は中止する
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, QTimer, Qt
from _common import QTest
import core.cloud_state as cs
from core.thumbnail_generator import ThumbnailWorker

b = make_panel()
d = tmpdir()
os.makedirs(os.path.join(d, "sub"))
names = ["a.png", "b.png", "c.ma", "d.txt"]
for n in names:
    open(os.path.join(d, n), "w").close()
b.navigate_to(d)

# --- オンラインのみを擬似的に作る（Windows 属性は Linux では出ないため差し替え）
ONLINE = {os.path.normcase(os.path.join(d, "a.png")),
          os.path.normcase(os.path.join(d, "c.ma"))}
_real = cs.is_online_only
cs.is_online_only = lambda p: os.path.normcase(os.path.abspath(p)) in ONLINE


def test_thumbnail_does_not_read_online_only():
    opened = []
    real_load = ThumbnailWorker._load_image

    def spy(path, size):
        opened.append(path)
        return real_load(path, size)

    ThumbnailWorker._load_image = staticmethod(spy)
    try:
        for n in names:
            try:
                ThumbnailWorker(os.path.join(d, n), 64)._generate(
                    os.path.join(d, n), 64)
            except Exception:
                pass    # 中身が空の .png は読めなくて当然（読んだ事実だけ見る）
    finally:
        ThumbnailWorker._load_image = staticmethod(real_load)
    for p in opened:
        assert os.path.normcase(os.path.abspath(p)) not in ONLINE, ("読んでしまった", p)
    print("thumbnail never reads online-only files: OK")


def test_prefetch_list_excludes_online_only():
    files = b._list_visible_files(d)
    got = {os.path.basename(x) for x in files}
    assert "a.png" not in got and "c.ma" not in got, got
    assert "b.png" in got and "d.txt" in got, got
    print("prefetch list excludes online-only: OK")


def test_prefetch_skipped_in_list_mode():
    calls = []
    real = b._list_visible_files
    b._list_visible_files = lambda p: (calls.append(p), real(p))[1]
    try:
        assert not b._any_thumb_column(), "既定はリスト表示のはず"
        b._prefetch_thumbs_async(d)          # force なし → 走らない
        QTest.qWait(250)
        assert not calls, ("リスト表示で先読みが走った", calls)
        b._prefetch_thumbs_async(d, force=True)
        QTest.qWait(400)
        assert calls, "force でも走らなかった"
    finally:
        b._list_visible_files = real
    print("prefetch runs only for thumbnail columns: OK")


def s1():
    test_thumbnail_does_not_read_online_only()
    test_prefetch_list_excludes_online_only()
    test_prefetch_skipped_in_list_mode()
    # --- 自己修復が選択を壊さないこと
    cv, rect, _ = find_item(b, "b.png"); assert cv
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    cv2, rect2, _ = find_item(b, "d.txt")
    QTest.mouseClick(cv2.viewport(), Qt.LeftButton, Qt.ControlModifier, rect2.center())
    QTimer.singleShot(300, s2)


def s2():
    sel = sorted(os.path.basename(p) for p in b._get_selected_paths()
                 if os.path.dirname(p.replace("\\", "/")) == d.replace("\\", "/"))
    assert sel == ["b.png", "d.txt"], ("前提の複数選択が作れていない", sel)
    assert getattr(b._column_view, "_mfm_user_selected", False), "選択フラグが立たない"
    # 遅延タイマーと同じ呼び出しを直接起こす（クラウドで実際に起きる状況）
    b._force_column_rebuild(d)
    QTimer.singleShot(300, s3)


def s3():
    sel = sorted(os.path.basename(p) for p in b._get_selected_paths()
                 if os.path.dirname(p.replace("\\", "/")) == d.replace("\\", "/"))
    assert sel == ["b.png", "d.txt"], ("自己修復が複数選択を壊した", sel)
    print("delayed column rebuild keeps the multi-selection: OK")
    # ナビゲーションでガードが解除されること
    b.navigate_to(os.path.join(d, "sub"))
    QTimer.singleShot(600, s4)


def s4():
    assert not getattr(b._column_view, "_mfm_user_selected", True), \
        "ナビゲーション後もガードが残っている（自己修復が永久に止まる）"
    print("guard resets on navigation: OK")
    cs.is_online_only = _real
    finish(True)


run(s1)
