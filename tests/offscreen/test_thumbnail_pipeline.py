# -*- coding: utf-8 -*-
"""r99: 実ファイルから実際にサムネイルが出るところまで（スレッド経路込み）。

- ワーカーが返すのは QImage（QPixmap を GUI スレッド外で作らない）
- ThumbnailManager 経由で «中身のある» QPixmap が届く
- 日本語を含むパスでも出る
- OneDrive: 属性がオンラインのみでも «実体がある» なら生成する（r99）
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, tmpdir, finish, run, QTest
from core.compat import QImage, QPixmap, Qt
from core.thumbnail_generator import ThumbnailManager, ThumbnailWorker
import core.cloud_state as cs

root = tmpdir()
JP_DIR = os.path.join(root, "源あかり", "個別4枚")
os.makedirs(JP_DIR)
PNG = os.path.join(JP_DIR, "01_正面.png")
img = QImage(64, 48, QImage.Format_RGB32)
img.fill(Qt.red)
assert img.save(PNG, "PNG"), "テスト用 PNG を作れない"

GOT = []


def s1():
    # 1) ワーカーの戻りは QImage（QPixmap ではない）
    out = ThumbnailWorker(PNG, 64)._generate(PNG, 64)
    assert isinstance(out, QImage), ("ワーカーが QImage を返していない", type(out))
    assert not out.isNull() and out.width() > 0, "画像を読めていない"
    assert not isinstance(out, QPixmap)
    print("worker returns a real QImage (not QPixmap): OK")

    # 2) マネージャ経由（バックグラウンド→GUIスレッド変換）で絵が届く
    mgr = ThumbnailManager(cache_size=8, thumb_size=64)
    mgr.thumbnail_ready.connect(lambda p, pm: GOT.append((p, pm)))
    assert mgr.get(PNG) is None, "初回はキャッシュ無しのはず"
    for _ in range(40):
        QTest.qWait(50)
        app.processEvents()
        if GOT:
            break
    assert GOT, "thumbnail_ready が来ない"
    path, pm = GOT[-1]
    assert path == PNG
    assert isinstance(pm, QPixmap) and not pm.isNull(), \
        ("サムネイルが空（日本語パス/スレッド経路）", pm)
    assert mgr.get(PNG) is not None, "キャッシュに入っていない"
    print("manager delivers a non-empty pixmap for a JP path: OK")

    # 3) OneDrive: 属性が «オンラインのみ» でも実体があれば生成する
    orig_attr, orig_bytes = cs.file_attributes, cs.local_bytes
    try:
        cs.file_attributes = lambda p: cs.ATTR_RECALL_ON_DATA
        cs.local_bytes = lambda p: 12345          # ダウンロード済み
        assert not cs.is_online_only(PNG), \
            "ダウンロード済みなのに «オンラインのみ» と誤判定"
        out = ThumbnailWorker(PNG, 64)._generate(PNG, 64)
        assert not out.isNull(), "ダウンロード済みなのにサムネイルを作らない"

        cs.local_bytes = lambda p: 0              # 実体なし＝プレースホルダ
        assert cs.is_online_only(PNG), "実体0バイトを見逃した"
        out = ThumbnailWorker(PNG, 64)._generate(PNG, 64)
        assert out.isNull(), "オンラインのみの中身に触れてしまった"

        cs.file_attributes = lambda p: cs.ATTR_OFFLINE
        cs.local_bytes = lambda p: 999
        assert cs.is_online_only(PNG), "OFFLINE 属性は実体サイズによらず対象"
    finally:
        cs.file_attributes, cs.local_bytes = orig_attr, orig_bytes
    print("cloud check uses real local bytes, not attributes alone: OK")
    finish(True)


run(s1, delay=500)
