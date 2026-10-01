# -*- coding: utf-8 -*-
"""水平スクロールの整合性（r54）: カラム位置 x == -hbar.value、hbar.maximum ==
総幅-表示幅、最右端で末尾カラムがビューポート右端にぴったり収まること。
再構築（setRootIndex）がスクロールアニメーション中に走っても崩れないことを、
連続クリック／アニメ中の navigate_to／戻る／幅変更 で検証する。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, columns, tmpdir, finish, run, QTimer, find_item
from _common import QTest
from core.compat import Qt

root = tmpdir()
p = root
chain = []
for i in range(1, 8):
    for s in range(4):
        os.makedirs(os.path.join(p, "L%d_%d" % (i, s)), exist_ok=True)
        open(os.path.join(p, "f%d_%d.ma" % (i, s)), "w").close()
    p = os.path.join(p, "L%d_2" % i)
    chain.append(p)
other = tmpdir()
for s in range(3):
    os.makedirs(os.path.join(other, "O%d" % s))

b = make_panel(1400, 700)
cv = b._column_view
hbar = cv.horizontalScrollBar()
bad = []


def check(tag):
    cols = columns(b)
    vw = cv.viewport().width()
    total = sum(c.width() for c in cols)
    probs = []
    for a, c in zip(cols, cols[1:]):
        if a.x() + a.width() != c.x():
            probs.append("gap %d->%d" % (a.x() + a.width(), c.x()))
    if cols and cols[0].x() != -hbar.value():
        probs.append("first.x=%d hval=%d" % (cols[0].x(), hbar.value()))
    exp_max = max(0, total - vw)
    if hbar.maximum() != exp_max:
        probs.append("hmax=%d exp=%d" % (hbar.maximum(), exp_max))
    if cols and total > vw and hbar.value() == hbar.maximum():
        last_r = cols[-1].x() + cols[-1].width()
        if abs(last_r - vw) > 1:
            probs.append("last_right=%d vw=%d" % (last_r, vw))
    # current のカラムとその子カラムが可視域にあること
    cur = cv.currentIndex()
    if cur.isValid():
        for i, v in enumerate(cols):
            if v.rootIndex() == cur.parent():
                nxt = cols[min(i + 1, len(cols) - 1)]
                if v.x() < -1 or nxt.x() + nxt.width() > vw + 1:
                    probs.append("current col not visible x=%d right=%d vw=%d"
                                 % (v.x(), nxt.x() + nxt.width(), vw))
                break
    print("[%-28s] n=%d hval=%d/%d %s" % (tag, len(cols), hbar.value(), hbar.maximum(),
                                          probs or "OK"))
    if probs:
        bad.append((tag, probs))


def click(name):
    c, rect, idx = find_item(b, name)
    assert c is not None, "item not visible: " + name
    QTest.mouseClick(c.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())


steps = []


def later(ms, fn):
    steps.append((ms, fn))


def s_clicks():
    # 1) 連続クリックで 7 階層降りる（各クリック後にアニメ完了を待つ）
    names = ["L%d_2" % i for i in range(1, 8)]
    def go(i=0):
        if i >= len(names):
            check("after 7 clicks")
            QTimer.singleShot(50, s_navigate_mid_anim)
            return
        click(names[i])
        QTimer.singleShot(450, lambda: (check("click %s" % names[i]), go(i + 1)))
    go()


def s_navigate_mid_anim():
    # 2) 最右端までスクロールした状態で、別パスへ navigate_to（再構築）。
    #    直後（アニメ中）にさらに深いパスへ navigate_to して再構築を重ねる。
    b.navigate_to(other)
    QTimer.singleShot(40, lambda: b.navigate_to(chain[4]))
    QTimer.singleShot(1500, lambda: (check("navigate mid-anim"), s_click_after()))


def s_click_after():
    click("L6_2")
    QTimer.singleShot(450, lambda: (check("click after rebuild"), s_back()))


def s_back():
    # 3) 戻る（履歴）→ 再構築
    b.go_back() if hasattr(b, "go_back") else b.navigate_to(other)
    QTimer.singleShot(1200, lambda: (check("back"), s_resize()))


def s_resize():
    # 4) 最右端で幅変更（広げる/縮める）
    b.navigate_to(chain[5])
    def do():
        cols = columns(b)
        if len(cols) >= 2:
            cv.set_column_width_for_view(cols[-2], 420)
            cv.set_column_width_for_view(cols[0], 160)
        QTimer.singleShot(300, lambda: (check("after resize"), s_done()))
    QTimer.singleShot(1200, do)


def s_done():
    if bad:
        print("PROBLEMS:", bad)
    finish(not bad)


b.navigate_to(root)
run(s_clicks, delay=2500, timeout=60000)
