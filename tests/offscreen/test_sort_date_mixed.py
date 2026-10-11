# -*- coding: utf-8 -*-
"""r132: 日付順はフォルダとファイルを «混ぜて» 並べる。

ユーザー報告 2026-10-08:
  「日付順でのソートですが、フォルダとファイルを混在させての日付順にして
    ほしいです。例えばDownloadフォルダが日付順にしても、フォルダがズラッと
    並んだ後に、ファイルの最新が来てしまうので、日付順の意味をなしていません。」

従来は key に関わらず «フォルダは常に先頭» で固定していた。名前順・種類順では
探しやすさが勝つのでそのままにし、日付順だけ混ぜる。
"""
import os
import time
from _common import *  # noqa: F401,F403
from _common import (app, make_panel, find_item_wait as find_item,
                     tmpdir, finish, run, Qt, QTest)

root = tmpdir()
# 古い順に: dir_old → file_mid → dir_new → file_new
# フォルダ・ファイルが «交互» になるよう仕込む。種別で固めると必ず崩れる。
PLAN = [("dir_old", True), ("file_mid.ma", False),
        ("dir_new", True), ("file_new.ma", False)]
now = time.time()
for i, (name, is_dir) in enumerate(PLAN):
    p = os.path.join(root, name)
    if is_dir:
        os.makedirs(p)
    else:
        open(p, "w").close()
    t = now - (len(PLAN) - i) * 3600          # 1時間ずつ新しくする
    os.utime(p, (t, t))

b = make_panel(1100, 650)
b.navigate_to(root)
fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _settle(ms=400):
    QTest.qWait(ms)
    app.processEvents()


def _order():
    m = b._proxy
    pidx = m.mapFromSource(b._fs_model.index(root))
    return [b._fs_model.fileName(m.mapToSource(m.index(r, 0, pidx)))
            for r in range(m.rowCount(pidx))]


def s1():
    cvw, _r, _i = find_item(b, "dir_old")
    check(bool(cvw), "前提: カラムに項目が出ている")

    # ── 日付の昇順（古い順）: 種別を跨いで古い順に並ぶ ────────────────
    b._proxy.set_column_sort(root, "date", True)
    _settle(500)
    want = [n for n, _d in PLAN]
    check(_order() == want,
          "日付の昇順が種別を跨いで古い順（%s）" % (_order(),))

    # ── 日付の降順（新しい順）─────────────────────────────────────
    b._proxy.set_column_sort(root, "date", False)
    _settle(500)
    check(_order() == list(reversed(want)),
          "日付の降順が種別を跨いで新しい順（%s）" % (_order(),))

    # ── 名前順・種類順は «従来どおりフォルダが先» ────────────────────
    for key in ("name", "type"):
        b._proxy.set_column_sort(root, key, True)
        _settle(400)
        got = _order()
        dirs = [n for n, d in PLAN if d]
        check(set(got[:2]) == set(dirs),
              "%s 順ではフォルダが先頭のまま（%s）" % (key, got))

    finish(not fails)


run(s1, delay=900)
