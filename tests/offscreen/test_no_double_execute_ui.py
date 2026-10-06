# -*- coding: utf-8 -*-
"""r124: **Maya 以外の機能でも二重実行が起きないこと。**

ユーザー指示 2026-10-06:
  「maya以外の機能すべてで2度重複実行はありえないので、本当にすべて見てください」

全 UI 経路を棚卸しして見つかった «1 操作で 2 つ走る» 穴を固定する。

  1) ダブルクリック … Qt は 1 回目の離しで clicked、2 回目で activated を
     «両方» 出す。シングルクリック動作が「開く/インポート/リファレンス」の時、
     続く activated の «関連付けで開く» まで走ると 1 ジェスチャーで 2 動作。
  2) シングルクリック動作が «なし» の時は、ダブルクリックの関連付け起動は
     ちゃんと 1 回走る（抑止しすぎない）。
  3) 右クリックメニューは開くたびに溜めない（溜まった QAction の
     Delete ショートカットが本体の Delete と多重登録になり、キー操作が
     «効かない／二度目で効く» 壊れ方になっていた）。
  4) プリセット名の改名は Enter で二重に走らない（returnPressed と
     editingFinished が両方飛ぶ）。
  5) DCC の «起動» は連打しても 1 本だけ。
"""
import os
from _common import *  # noqa: F401,F403
from _common import tmpdir, finish, make_panel, find_item_wait, flush_deleted

import ui.browser_panel as bp

root = tmpdir()
scene = os.path.join(root, "chr_A.ma")
open(scene, "w").close()

fails = []
_keep = []        # ウィジェットは生かしておく（終了時の破棄順で落ちるのを避ける）


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


# ---------------------------------------------------------------------------
# 1) / 2) ダブルクリック（パネルは 1 つを使い回す）
# ---------------------------------------------------------------------------
ext_opened = []
dcc_opened = []
orig_open = bp.open_with_default_app
bp.open_with_default_app = lambda p: ext_opened.append(p)

b = make_panel()
b.set_open_callback(lambda p: dcc_opened.append(p))
b.navigate_to(root)
QTest.qWait(800)
app.processEvents()
cv, rect, idx = find_item_wait(b, "chr_A.ma")


def dblclick_case(click_action, expect_dcc, expect_ext, label):
    del ext_opened[:]
    del dcc_opened[:]
    sm.set("single_click_action", click_action, save=False)
    b._last_click_action = None
    # 実機のダブルクリックと同じ順で: clicked → activated
    b._on_item_clicked(idx)
    b._on_item_activated(idx)
    app.processEvents()
    check(len(dcc_opened) == expect_dcc,
          "%s: クリック動作 %d 回（期待 %d）" % (label, len(dcc_opened), expect_dcc))
    check(len(ext_opened) == expect_ext,
          "%s: 関連付け起動 %d 回（期待 %d）" % (label, len(ext_opened), expect_ext))


if cv is None:
    check(False, "項目 chr_A.ma が見つからない")
else:
    dblclick_case("open", 1, 0, "ダブルクリック（クリック動作=開く）")
    dblclick_case("none", 0, 1, "ダブルクリック（クリック動作=なし）")

bp.open_with_default_app = orig_open
sm.set("single_click_action", "none", save=False)

# ---------------------------------------------------------------------------
# 3) 右クリックメニューが溜まらない
# ---------------------------------------------------------------------------
from core.compat import QMenu, QPoint  # noqa: E402


class _NoExecMenu(QMenu):
    """exec だけ «出さない» メニュー（本物の QMenu には触らない）。"""
    shown = []

    def exec_(self, *a, **k):
        _NoExecMenu.shown.append(1)

    def exec(self, *a, **k):          # noqa: A003
        _NoExecMenu.shown.append(1)


_orig_menu_cls = bp.QMenu
bp.QMenu = _NoExecMenu
try:
    for _ in range(6):
        b._popup_context_menu([scene], QPoint(10, 10))
finally:
    bp.QMenu = _orig_menu_cls
# deleteLater の予約を «今» 片付けてから数える（実機の qWait 事情に依らない）
flush_deleted()
QTest.qWait(80)
flush_deleted()
alive = b.findChildren(_NoExecMenu)
check(len(_NoExecMenu.shown) == 6,
      "メニューは呼んだ回数だけ出る（%d 回）" % len(_NoExecMenu.shown))
check(len(alive) == 0,
      "メニューは溜まらない（残り %d 個 / 6 回開いた）" % len(alive))
dels = []
for a in b.actions():
    try:
        if a.shortcut().toString().lower() == "del":
            dels.append(a)
    except Exception:
        pass
check(len(dels) == 1, "Delete ショートカットは 1 つだけ（%d 個）" % len(dels))
_keep.append(b)

# ---------------------------------------------------------------------------
# 4) プリセット名の改名が二重に走らない
# ---------------------------------------------------------------------------
from ui.quick_nav_editor import QuickNavPresetEditor  # noqa: E402

ed = QuickNavPresetEditor(sm)
calls = []
_real = ed._apply_rename_now
ed._apply_rename_now = lambda: (calls.append(1), _real())[1]
ed._apply_rename()                       # 1 回目
ed._renaming = True                      # モーダル中に editingFinished が飛ぶのと同じ形
ed._apply_rename()
ed._renaming = False
check(len(calls) == 1, "改名の再入は抑止される（%d 回）" % len(calls))
_keep.append(ed)

# ---------------------------------------------------------------------------
# 5) 起動の連打
# ---------------------------------------------------------------------------
import ui.main_window_dcc as mwd  # noqa: E402


class _Bar:
    def showMessage(self, *a, **k):
        pass


class _LaunchHost(mwd.MainWindowDccMixin):
    def statusBar(self):
        return _Bar()


h = _LaunchHost()
ok1 = h._launch_allowed("maya")
ok2 = h._launch_allowed("maya")
ok3 = h._launch_allowed("blender")
check(ok1 and not ok2, "Maya の起動は連打しても 1 回だけ")
check(ok3, "別の DCC の起動は妨げない")

finish(not fails)
