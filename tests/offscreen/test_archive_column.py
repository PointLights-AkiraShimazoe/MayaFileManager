# -*- coding: utf-8 -*-
"""r97: zip/tar の中身をそのままブラウズできる（エクスプローラー相当の範囲）。

- カラムで書庫を選ぶと «中身カラム» が出て、階層をたどれる
- 面の色が通常カラムと違う（plane_archive）
- 展開（取り出し）ができる／書庫の中身は読み取り専用
- D&D は «テンポラリへ展開した実ファイル» の URL を渡す
"""
import os, tarfile, zipfile
from _common import *  # noqa: F401,F403
from _common import app, make_panel, find_item_wait as find_item, tmpdir, finish, run, Qt, QTest

b = make_panel(1300, 700)
root = tmpdir()
ZIP = os.path.join(root, "assets.zip")
with zipfile.ZipFile(ZIP, "w") as z:
    z.writestr("scenes/chr_A.ma", "maya-ascii")
    z.writestr("scenes/sub/chr_B.ma", "maya-ascii")
    z.writestr("readme.txt", "hello")
TGZ = os.path.join(root, "assets.tar.gz")
with tarfile.open(TGZ, "w:gz") as t:
    t.add(ZIP, arcname="inner/assets.zip")
open(os.path.join(root, "plain.ma"), "w").close()
b.navigate_to(root)


def _settle(ms=350):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    from core import archive_browse as ab
    ac = b._archive_col

    # 1) 書庫の判定（エクスプローラーが扱えない .rar/.7z は対象外）
    assert ab.is_archive(ZIP) and ab.is_archive(TGZ)
    assert not ab.is_archive(os.path.join(root, "plain.ma"))
    assert not ab.is_archive(os.path.join(root, "nope.rar"))

    # 2) カラムで zip をクリック → 中身カラムが開く
    cvw, rect, _ = find_item(b, "assets.zip")
    assert cvw, "assets.zip が見つからない"
    QTest.mouseClick(cvw.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    _settle()
    assert ac.isVisible(), "書庫カラムが開かない"
    # Windows では QFileSystemModel 由来のパスが «/» 区切りで返る
    # （_safe_file_path）。生文字列で比べると実機だけ落ちる（2026-10-06）。
    def _same(a, b):
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))
    assert _same(ac.archive_path(), ZIP), (ac.archive_path(), ZIP)

    # 3) 階層がある（scenes/ の下に sub/ と chr_A.ma）
    m = ac._view.model()
    tops = [m.item(i).text() for i in range(m.rowCount())]
    assert "scenes" in tops and "readme.txt" in tops, ("ルートの中身が違う", tops)
    scenes = m.item(tops.index("scenes"))
    kids = [scenes.child(i).text() for i in range(scenes.rowCount())]
    assert sorted(kids) == ["chr_A.ma", "sub"], ("scenes の中身が違う", kids)
    print("archive column lists nested entries: OK")

    # 4) 通常カラムと違う面の色（plane_archive）を使っている
    from core.theme_engine import qss_vars
    tv = qss_vars()
    assert tv["plane_archive"] != tv["plane_flat"], "通常カラムと同じ色"
    assert tv["plane_archive"] in ac.styleSheet(), "書庫カラムの面色が未適用"
    print("archive column uses its own plane color: OK")

    # 5) D&D は «展開した実ファイル» を渡す（書庫の中身は直接掴めない）
    idx = scenes.child(kids.index("chr_A.ma")).index()
    mime = m.mimeData([idx])
    urls = [u.toLocalFile() for u in mime.urls()]
    assert len(urls) == 1 and os.path.isfile(urls[0]), ("D&D 用の展開に失敗", urls)
    assert open(urls[0]).read() == "maya-ascii"
    assert os.path.normcase(urls[0]) != os.path.normcase(ZIP)
    print("drag hands out extracted real files: OK")

    # 6) 展開（取り出し）
    dest = os.path.join(root, "out")
    made = ab.extract_members(ZIP, ["scenes"], dest)
    assert os.path.isfile(os.path.join(dest, "scenes", "chr_A.ma")), made
    assert not os.path.exists(os.path.join(dest, "readme.txt")), "選択外まで展開した"
    print("extract selected subtree: OK")

    # 7) tar.gz も同じように読める
    ents = ab.list_entries(TGZ)
    assert any(e.name.endswith("inner/assets.zip") for e in ents), \
        [e.name for e in ents]

    # 8) 書庫内の不正パス（Zip Slip）は拒否する
    evil = os.path.join(root, "evil.zip")
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../escaped.txt", "x")
    try:
        ab.extract_members(evil, None, os.path.join(root, "evil_out"))
        raise AssertionError("書庫外への展開が通ってしまった")
    except ValueError:
        pass
    print("zip-slip rejected: OK")

    # 9) 通常ファイルを選んだら閉じる
    cvw2, r2, _ = find_item(b, "plain.ma")
    QTest.mouseClick(cvw2.viewport(), Qt.LeftButton, Qt.NoModifier, r2.center())
    _settle()
    assert not ac.isVisible(), "通常ファイル選択で書庫カラムが閉じない"
    print("closes for non-archive files: OK")
    finish(True)


run(s1, delay=900)
