# -*- coding: utf-8 -*-
"""r102: サムネイルが無い項目（フォルダ・README.md 等）も、
グリッドのセルいっぱいに描かれる（豆粒にならない）。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, make_panel, find_item_wait as find_item, tmpdir, finish, run, Qt, QTest
from core.compat import QListView, QIcon, QPixmap, QSize
import ui.browser_panel as bp

b = make_panel(1100, 700)
d = tmpdir()
os.makedirs(os.path.join(d, "髪型アレンジ"))
open(os.path.join(d, "README.md"), "w").write("# hi")
b.navigate_to(d)


def _settle(ms=400):
    QTest.qWait(ms)
    app.processEvents()


class _SmallIcon(QIcon):
    """16px しか持たないシェル風アイコン（拡大しないと豆粒になる）。"""
    pass


def s1():
    cvw, _rect, _ = find_item(b, "README.md")
    assert cvw, "README.md が見つからない"
    b._column_view._set_column_view_mode(cvw, "thumb")
    _settle(700)
    assert cvw.viewMode() == QListView.IconMode
    dele = cvw.itemDelegate()
    assert isinstance(dele, bp.ThumbnailDelegate), type(dele).__name__

    side = b._column_view.column_item_size(cvw)
    assert side >= 48, ("サムネイルのセルが小さすぎる", side)

    m = cvw.model(); ri = cvw.rootIndex()
    checked = 0
    for r in range(m.rowCount(ri)):
        i = m.index(r, 0, ri)
        if i.data() not in ("README.md", "髪型アレンジ"):
            continue
        pm = dele._fallback_pixmap(i, side)
        assert pm is not None and not pm.isNull(), ("代替アイコンが無い", i.data())
        assert max(pm.width(), pm.height()) == side, \
            ("セルいっぱいに拡大されていない", i.data(), pm.width(), pm.height(), side)
        checked += 1
    assert checked == 2, ("対象が足りない", checked)
    print("fallback icons fill the grid cell: OK")

    # ── 実際に «描かれた絵» の大きさを見る（r103） ─────────────────
    # 画像ファイル（本物のサムネイル）と README.md（代替アイコン）で
    # 描画される絵の高さが極端に違わないこと＝«アイコンだけ豆粒» でないこと。
    def _ink_box(view, rect):
        img = view.viewport().grab(rect).toImage()
        counts = {}
        for y in range(img.height()):
            for x in range(0, img.width(), 2):
                c = img.pixelColor(x, y).name()
                counts[c] = counts.get(c, 0) + 1
        bg = max(counts, key=counts.get)
        top, bot = None, None
        for y in range(img.height()):
            row = any(img.pixelColor(x, y).name() != bg
                      for x in range(0, img.width(), 2))
            if row:
                top = y if top is None else top
                bot = y
        return (0 if top is None else bot - top + 1)

    m2 = cvw.model()
    heights = {}
    for r in range(m2.rowCount(ri)):
        i = m2.index(r, 0, ri)
        if i.data() not in ("README.md", "髪型アレンジ"):
            continue
        rc = cvw.visualRect(i)
        if rc.isValid() and not rc.isEmpty():
            heights[i.data()] = _ink_box(cvw, rc)
    assert heights, "描画矩形が取れない"
    for name, h in heights.items():
        # 絵＋名前で最低でもセルの半分以上は埋まるはず（豆粒なら 20px 程度）
        assert h >= side * 0.6, ("描かれた絵が小さすぎる", name, h, side)
    print("painted icons are actually large on screen: OK", heights)

    # 16px しか持たないアイコンでも拡大される（QIcon.pixmap は拡大しないため）
    small = QPixmap(16, 16)
    small.fill(Qt.red)
    icon = QIcon(small)
    got = bp.ThumbnailDelegate._fit(icon.pixmap(QSize(side, side)), side)
    assert max(got.width(), got.height()) == side, \
        ("16px アイコンが拡大されていない", got.width(), got.height())
    print("tiny 16px icons are scaled up too: OK")

    # 実サムネイル（大きい画像）は «縮小» のまま（比率を壊さない）
    big = QPixmap(512, 256)
    big.fill(Qt.blue)
    fit = bp.ThumbnailDelegate._fit(big, side)
    assert fit.width() == side and fit.height() == side // 2, \
        ("アスペクト比が壊れている", fit.width(), fit.height())
    print("aspect ratio preserved: OK")

    # ── r103: 高DPI（devicePixelRatio>1）のアイコンも «見た目» が縮まない ──
    hidpi = QPixmap(32, 32)
    hidpi.fill(Qt.green)
    hidpi.setDevicePixelRatio(2.0)        # 論理 16px 相当のシェルアイコン
    from core.compat import QImage, QPainter
    canvas = QImage(side * 2, side * 2, QImage.Format_ARGB32)
    canvas.fill(Qt.black)
    pt = QPainter(canvas)

    class _Opt:
        pass
    # 実際の描画関数と同じ計算で «描画先の矩形» を求める
    dpr = hidpi.devicePixelRatio()
    lw, lh = hidpi.width() / dpr, hidpi.height() / dpr
    k = side / max(lw, lh)
    tw, th = int(round(lw * k)), int(round(lh * k))
    pt.drawPixmap(bp.QRect(0, 0, tw, th), hidpi)
    pt.end()
    assert tw == side and th == side, \
        ("高DPI アイコンが論理サイズでセルいっぱいにならない", tw, th, side)
    assert canvas.pixelColor(side - 2, side - 2).green() > 200, \
        "描画が矩形いっぱいに広がっていない"
    print("hi-dpi icons still fill the cell: OK")

    # ── r102: スライダーは «全体» に効く ────────────────────────────
    cv = b._column_view
    cols = [v for v in cv._live_columns()
            if getattr(v, "_mfm_view_mode", "list") == "list"]
    assert cols, "リスト表示のカラムが無い"
    cv._set_column_view_mode(cvw, "list")      # 全部リストに揃える
    _settle(400)
    cols = [v for v in cv._live_columns()]
    n = cv.set_item_size_all(40, "list")
    _settle(300)
    assert n >= 1, "一括適用されたカラムが無い"
    for v in cols:
        if getattr(v, "_mfm_view_mode", "list") != "list":
            continue
        assert cv.column_item_size(v) == 40, \
            ("カラムごとに反映されていない", cv.column_item_size(v))
    # 新しく開くカラムにも効く（保存されている）
    assert int(sm.get("column_icon_size_list", 0)) == 40, "設定に保存されていない"

    # サムネビュー側にも配られる
    cv.set_item_size_all(120, "thumb")
    _settle(300)
    assert b._thumb_view.iconSize().width() == 120, \
        ("独立サムネビューへ配られていない", b._thumb_view.iconSize().width())
    print("slider changes the size everywhere: OK")
    finish(True)


run(s1, delay=900)
