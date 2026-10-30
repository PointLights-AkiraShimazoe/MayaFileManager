# -*- coding: utf-8 -*-
"""r146（ユーザー指示 2026-10-30）: アプリのアイコンは採用した X-3。

* app.ico は 16〜256px を持ち、中身が原画（app_icon_source.png）と一致する
* スタンドアロン起動でアプリのアイコンが設定される（ウィンドウ左上・タスク
  バー・コンパクトのパックが同じ絵になる）。以前は設定しておらず、ソースから
  起動すると Python のアイコンになっていた
* generate_icon.py / build_tools/make_icon.py を実行しても古いデザインに
  戻らない（原画から作るだけ）
* ビルド（spec / インストーラ）は app.ico を使う"""
import io
import os
import shutil
import tempfile
from _common import *  # noqa: F401,F403
from _common import app, finish

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
ICONS = os.path.join(ROOT, "resources", "icons")
src_png = os.path.join(ICONS, "app_icon_source.png")
ico = os.path.join(ICONS, "app.ico")
check(os.path.isfile(src_png) and os.path.isfile(ico), "原画と app.ico がある")

from core.compat import QIcon, QImage, Qt
qico = QIcon(ico)
sizes = sorted(s.width() for s in qico.availableSizes())
check(16 in sizes and 256 in sizes and 32 in sizes and 48 in sizes,
      "app.ico が 16 / 32 / 48 / 256px を持つ: %r" % sizes)


def avg_diff(a, b):
    a = a.convertToFormat(QImage.Format_ARGB32)
    b = b.convertToFormat(QImage.Format_ARGB32)
    tot = n = 0
    for y in range(0, a.height(), 8):
        for x in range(0, a.width(), 8):
            ca, cb = a.pixelColor(x, y), b.pixelColor(x, y)
            tot += abs(ca.red() - cb.red()) + abs(ca.green() - cb.green()) + \
                abs(ca.blue() - cb.blue()) + abs(ca.alpha() - cb.alpha())
            n += 4
    return tot / max(1, n)


big = qico.pixmap(256, 256).toImage()
ref = QImage(src_png).scaled(256, 256, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
d = avg_diff(big, ref)
check(d < 6, "app.ico の 256px が原画（X-3）と一致（平均差 %.1f）" % d)

# スタンドアロン起動の設定でアプリのアイコンが入る
import main as _main
app.setWindowIcon(QIcon())
_main._apply_app_icon(app)
check(not app.windowIcon().isNull(), "スタンドアロン起動でアプリのアイコンが設定される")
d2 = avg_diff(app.windowIcon().pixmap(256, 256).toImage(), ref)
check(d2 < 6, "設定されたアイコンが X-3（平均差 %.1f）" % d2)

# 生成スクリプトを走らせても古いデザインに戻らない
import generate_icon as _gen
tmp = tempfile.mkdtemp(prefix="mfm_icon_")
out_ico, out_png = _gen.build(src_png, tmp)
d3 = avg_diff(QIcon(str(out_ico)).pixmap(256, 256).toImage(), ref)
check(d3 < 6, "generate_icon.py が原画から作る（平均差 %.1f）" % d3)
mk = io.open(os.path.join(ROOT, "build_tools", "make_icon.py"), encoding="utf-8").read()
check("from generate_icon import main" in mk, "build_tools/make_icon.py も同じ処理を使う")
shutil.rmtree(tmp, ignore_errors=True)

spec = io.open(os.path.join(ROOT, "MayaFileManager.spec"), encoding="utf-8").read()
iss = io.open(os.path.join(ROOT, "build_tools", "installer.iss"), encoding="utf-8", errors="replace").read()
check("'app.ico'" in spec and not os.path.isfile(os.path.join(ICONS, "mfm.ico")),
      "EXE は app.ico を使う（先に探される mfm.ico が無い）")
check(r"resources\icons\app.ico" in iss, "インストーラも app.ico を使う")

finish(not fails)
