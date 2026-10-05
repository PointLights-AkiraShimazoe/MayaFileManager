# -*- coding: utf-8 -*-
"""r120: ソート種類に «表示名» を追加（ユーザー指示 2026-10-02）。

「名前」は実体名で並ぶ。表示名を付けても並び順が変わらないので、
一覧の見た目と順序が噛み合わなかった。«表示名» を選ぶと、画面に出ている
名前で並ぶ（付けていない項目は実体名で並ぶ）。
"""
import os
from _common import *  # noqa: F401,F403
from _common import (app, make_panel, find_item_wait as find_item,
                     tmpdir, finish, run, Qt, QTest)
from core import display_names as dn

root = tmpdir()
# 実体名の順序（a_, b_, c_）と表示名の順序（1/2/3）をわざと «逆» にする。
# これなら、どちらのキーで並んでいるかが順序だけで判別できる。
REAL = ["a_zebra", "b_yak", "c_wolf"]
for n in REAL:
    os.makedirs(os.path.join(root, n))
ALIAS = {"a_zebra": "3_さん", "b_yak": "2_に", "c_wolf": "1_いち"}

b = make_panel(1100, 650)
b.navigate_to(root)


def _settle(ms=400):
    QTest.qWait(ms)
    app.processEvents()


def _order(real=False):
    """現在の並び順（既定は画面に出ている名前、real=True で実体名）。"""
    m = b._proxy
    pidx = m.mapFromSource(b._fs_model.index(root))
    out = []
    for r in range(m.rowCount(pidx)):
        i = m.index(r, 0, pidx)
        if real:
            out.append(b._fs_model.fileName(m.mapToSource(i)))
        else:
            out.append(i.data(Qt.DisplayRole))
    return out


def s1():
    cvw, _r, _i = find_item(b, "a_zebra")
    assert cvw, "カラムに項目が出ていない"

    # ── 1) ソート種類に «表示名» が並んでいること ─────────────────
    cv = b._column_view
    keys = [k for k, _label in cv._SORT_KEYS]
    assert "alias" in keys, ("ソート種類に表示名が無い", keys)
    labels = dict(cv._SORT_KEYS)
    assert labels["alias"] == "表示名", labels["alias"]
    # ヘッダのプルダウンにも実際に入っていること
    from core.compat import QComboBox
    hdr = getattr(cvw, "_mfm_header", None)
    combos = hdr.findChildren(QComboBox) if hdr is not None else []
    assert combos and any(
        combos[0].itemData(i) == "alias" for i in range(combos[0].count())), \
        "ヘッダのソートプルダウンに «表示名» が出ていない"
    print("the sort combo offers a display-name key: OK")

    # ── 2) 表示名を付ける ───────────────────────────────────────
    dn.save(root, ALIAS)
    cv._refresh_display_names()
    _settle(500)
    assert sorted(_order()) == sorted(ALIAS.values()), _order()

    # 「名前」順は «実体名» で並ぶ（従来どおり。ここは変えない）
    b._proxy.set_column_sort(root, "name", True)
    _settle(400)
    assert _order(real=True) == REAL, ("名前順が実体名で並んでいない",
                                       _order(real=True))
    print("the name key still sorts by the real file name: OK")

    # ── 3) «表示名» 順は画面に出ている名前で並ぶ ──────────────────
    b._proxy.set_column_sort(root, "alias", True)
    _settle(400)
    assert _order() == ["1_いち", "2_に", "3_さん"], ("表示名順になっていない",
                                                     _order())
    assert _order(real=True) == ["c_wolf", "b_yak", "a_zebra"], _order(real=True)
    print("the display-name key sorts by what is shown: OK")

    # 降順も効く
    b._proxy.set_column_sort(root, "alias", False)
    _settle(400)
    assert _order() == ["3_さん", "2_に", "1_いち"], _order()
    print("descending works for the display-name key: OK")

    # ── 4) 表示名を «付けていない» 項目は実体名で並ぶ ───────────────
    dn.save(root, {"a_zebra": "3_さん"})      # b_yak / c_wolf は素のまま
    cv._refresh_display_names()
    _settle(500)
    b._proxy.set_column_sort(root, "alias", True)
    _settle(400)
    assert _order() == ["3_さん", "b_yak", "c_wolf"], \
        ("表示名の無い項目が実体名で並んでいない", _order())
    print("items without a display name fall back to the real name: OK")

    # ── 5) 表示名を変えたら «並びも» 追従すること ──────────────────
    # 対応表を捨てるだけでは並びは古いまま（実機で «名前を変えても動かない»
    # に見える）。_refresh_display_names から並べ直しも呼ぶ。
    dn.save(root, {"a_zebra": "zzz_最後"})
    cv._refresh_display_names()
    _settle(500)
    assert _order() == ["b_yak", "c_wolf", "zzz_最後"], \
        ("表示名を変えても並びが追従していない", _order())
    print("changing a display name re-sorts the column: OK")

    # ── 6) 実体パスは一切変わらない（表示名機能の大前提）──────────
    m = b._proxy
    pidx = m.mapFromSource(b._fs_model.index(root))
    for r in range(m.rowCount(pidx)):
        si = m.mapToSource(m.index(r, 0, pidx))
        p = b._fs_model.filePath(si)
        assert os.path.exists(p), ("実体パスが壊れた", p)
    print("sorting by display name never touches the real paths: OK")
    finish(True)


run(s1, delay=900)
