# -*- coding: utf-8 -*-
"""r146: どの操作でも «カラムがルートから作り直される» 動きをさせない。

ユーザー報告 2026-10-24（C-9）:
  「そもそもジャンクションをクリックすると、カラムがルートから作り直される
    ような動きをします。以前に、どの操作でもこの動きは NG と伝えたはず
    なので、全ての状況を洗い出して修正してください。」

原因: QColumnView.setRootIndex は «同じルート» でも全カラムを閉じて根から
作り直す。CappedColumnView の上書きはさらに直前で横スクロールを 0（ルートの
位置）へ戻す。ジャンクション（_follow_link → _navigate）、アドレス欄、
ブックマーク、履歴、別の枝へのクリック、2.6 秒後の自己修復
（_force_column_rebuild）が全部 «同じルート» でここを呼んでいた。

確かめること（ユーザーの実際の経路で）:
  * 作り直し（_reset_hscroll_for_rebuild）が 1 回も走らない
  * 共通の祖先のカラムは «同じウィジェットのまま» 残る（作り直されていない）
  * 横スクロールがルートの位置（0）まで戻らない
経路: ジャンクションのクリック／アドレス欄（navigate_to）で別の枝へ／
     ↶ でリンクの中へ戻る／ジャンクションのダブルクリック"""
import os
import subprocess
import sys
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, QTest

from core.compat import QListView

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def settle(ms=120):
    QTest.qWait(ms)
    app.processEvents()


base = tmpdir()
deep = os.path.join(base, "l1", "l2", "l3", "l4")
other = os.path.join(base, "l1", "l2", "other", "o1")
real = os.path.join(base, "realdir")
for d in (deep, other, os.path.join(real, "sub1")):
    os.makedirs(d)
open(os.path.join(real, "f.txt"), "w").close()
open(os.path.join(deep, "a.ma"), "w").close()
link = os.path.join(deep, "jlink")
if sys.platform == "win32":
    # 実機と同じ «ジャンクション»（mklink /J は管理者権限が要らない）
    subprocess.run(["cmd", "/c", "mklink", "/J", link, real],
                   capture_output=True, check=True)
else:
    os.symlink(real, link)
check(os.path.isdir(link), "前提: リンクができている")

b = make_panel(700, 600)      # 横スクロールが出る幅
cv = b._column_view
b.navigate_to(deep)
settle(3600)                  # navigate 直後の作り直しを越える


def view_for(path):
    n = b._norm(path)
    for v in cv.findChildren(QListView):
        if v.isHidden() or v.model() is None:
            continue
        p = cv._path_for_index(v.rootIndex()) or ""
        if p and b._norm(p) == n:
            return v
    return None


def wait_view(path, timeout_ms=8000):
    waited = 0
    while waited < timeout_ms:
        v = view_for(path)
        if v is not None:
            return v
        settle(100)
        waited += 100
    return None


check(wait_view(deep) is not None, "前提: 深い階層のカラムが出ている")

rebuilds = []
_orig_reset = cv._reset_hscroll_for_rebuild


def _count_reset():
    import traceback
    rebuilds.append("".join(traceback.format_stack(limit=8)[:-1]))
    _orig_reset()


cv._reset_hscroll_for_rebuild = _count_reset
hvals = []
cv.horizontalScrollBar().valueChanged.connect(lambda v: hvals.append(v))


def alive(w):
    try:
        import shiboken6
        return shiboken6.isValid(w)
    except ImportError:
        import shiboken2
        return shiboken2.isValid(w)


def snapshot(paths):
    return {p: view_for(p) for p in paths}


def same_views(before, label):
    for p, v in before.items():
        now = view_for(p)
        check(v is not None and alive(v) and now is v,
              "%s: «%s» のカラムが作り直されていない" % (label, os.path.relpath(p, base) if p.startswith(base) else p))


def run_case(label, action, wait_path, keep):
    del rebuilds[:]
    del hvals[:]
    h0 = cv.horizontalScrollBar().value()
    before = snapshot(keep)
    action()
    settle(300)
    check(wait_view(wait_path) is not None, "%s: 行き先のカラムが出る" % label)
    settle(3600)              # 2.6 秒後の自己修復まで見届ける
    check(not rebuilds, "%s: ルートからの作り直しが走らない（%d 回）%s"
          % (label, len(rebuilds), ("\n" + rebuilds[0]) if rebuilds else ""))
    same_views(before, label)
    if h0 > 0:
        check(not hvals or min(hvals) > 0,
              "%s: 横スクロールがルートの位置まで戻らない（h0=%d, 最小=%r）"
              % (label, h0, min(hvals) if hvals else None))


ancestors = [base, os.path.join(base, "l1"), os.path.join(base, "l1", "l2"),
             os.path.join(base, "l1", "l2", "l3")]

# 1) ジャンクションのクリック（_on_item_clicked → _follow_link → _navigate）
run_case("ジャンクションのクリック",
         lambda: b._on_item_clicked(cv._proxy_index_for_path(link)),
         link, ancestors + [deep])
check(b._norm(b.current_path()) == b._norm(link), "ジャンクションの中にいる: %r" % b.current_path())

# 2) アドレス欄で同じドライブの別の枝へ（navigate_to＝ブックマークも同じ経路）
run_case("アドレス欄で別の枝へ",
         lambda: b.navigate_to(other), other, ancestors[:3])

# 3) ↶ でジャンクションの中へ戻る（履歴の重い経路: リンク）
run_case("↶ でジャンクションへ", b._go_back, link, ancestors[:3])

# 4) ジャンクションのダブルクリック（_on_item_activated）
b.navigate_to(deep)
settle(3600)
run_case("ジャンクションのダブルクリック",
         lambda: b._on_item_activated(cv._proxy_index_for_path(link)),
         link, ancestors + [deep])

# 5) 追い直しの途中でユーザーが横スクロールを «手で» 動かしたら、追いかけない
#    （値の変化ではなく、スクロールバーへの操作で判断する。r146）
b.navigate_to(other)
settle(3600)                       # 移動が落ち着くまで待つ
hbar = cv.horizontalScrollBar()
cv._schedule_ensure_visible(relayout=True)   # 追い直しを仕掛ける（260ms〜）
settle(50)
from core.compat import QtWidgets as _QtW
_QAS = _QtW.QAbstractSlider
try:
    _to_min = _QAS.SliderAction.SliderToMinimum
except AttributeError:
    _to_min = _QAS.SliderToMinimum
hbar.triggerAction(_to_min)     # ユーザーの操作（actionTriggered が出る）
settle(2600)
check(hbar.value() == hbar.minimum(),
      "手で動かした後は追い直しが位置を奪わない（値=%d 最小=%d）"
      % (hbar.value(), hbar.minimum()))

cv._reset_hscroll_for_rebuild = _orig_reset
finish(not fails)
