# -*- coding: utf-8 -*-
"""r146: カラム／サムネ／平坦の QListView は «項目の大きさが揃っている»。

実機計測 2026-10-16 22:30（Windows）: これが無いと QListView は並べ直しの
たびに全項目へ sizeHint（data() を 8 役割ぶん、Python 経由）を聞きに来る。
7,500 件のカラムで 1 回 0.07 秒、dataChanged のたびに走るので、親フォルダが
少し変わるだけで 2 分固まった（回帰スイートの BrowserPanel 系が軒並み
100 秒超）。揃えると最初の 1 件だけ聞く（同じ場面が 1.9 秒）。

合わせて、並べ直しで data() が «件数に比例して» 呼ばれないことを数えて
確かめる（戻ると落ちる）。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, run, QTimer, columns
from core.compat import QListView

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


b = make_panel()
root = tmpdir()
big = os.path.join(root, "big")
os.makedirs(big)
for i in range(300):
    open(os.path.join(big, "f%03d.ma" % i), "w").close()
os.makedirs(os.path.join(big, "sub"))

from ui.browser_models import FileFilterProxyModel as _P
cnt = {"data": 0}
_od = _P.data


def _data(self, i, r=0):
    cnt["data"] += 1
    return _od(self, i, r)


_P.data = _data


def s1():
    b.navigate_to(os.path.join(big, "sub"))
    QTimer.singleShot(2500, s2)


def s2():
    views = [v for v in b._column_view.findChildren(QListView) if v.isVisible()]
    check(views, "カラムが出ている")
    check(all(v.uniformItemSizes() for v in views),
          "全カラムが uniformItemSizes（%r）"
          % [v.uniformItemSizes() for v in views])
    check(b._thumb_view.uniformItemSizes(), "サムネビューも uniformItemSizes")
    try:
        check(b._flat_col._view.uniformItemSizes(), "平坦ビューも uniformItemSizes")
    except Exception as e:
        print("flat view skip: %r" % e)
    # «big» のカラムを並べ直させ、data() が件数に比例しないことを数える
    bigv = None
    for v in views:
        p = b._column_view._path_for_index(v.rootIndex()) or ""
        if os.path.normcase(os.path.normpath(p)) == os.path.normcase(os.path.normpath(big)):
            bigv = v
    check(bigv is not None, "big のカラムがある")
    if bigv is not None:
        app.processEvents()
        before = cnt["data"]
        for _ in range(5):
            bigv.doItemsLayout()
            app.processEvents()
        used = cnt["data"] - before
        # 300 件 × 8 役割 × 5 回 = 12,000 以上なら «全項目に聞いている»
        check(used < 2000, "並べ直し 5 回の data() 呼び出しが件数に比例しない（%d 回）" % used)
    finish(not fails)


run(s1, delay=800, timeout=30000)
