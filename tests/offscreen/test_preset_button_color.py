# -*- coding: utf-8 -*-
"""r151（ユーザー指示 2026-11-18）:「プリセット設定でボタンに色を付けられる様にしてください」

確かめること:
  1) 色の値をそろえる（#RGB → #RRGGBB、使えない値は色なし）。文字色は背景で白か黒
  2) プリセット設定の行で色を選ぶと、ボタンの項目に "color" が入る。«なし» で消える
  3) 選べる色は 8 色＋なし＋その他
  4) 保存した色がプリセットのボタンに付く。色なしのボタンはテーマのまま
"""
from _common import *  # noqa: F401,F403
from _common import finish, sm
from core.compat import QApplication

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


from core import preset_colors as pc
check(pc.normalize("#abc") == "#AABBCC" and pc.normalize("#2f6fc9") == "#2F6FC9"
      and pc.normalize("red") == "" and pc.normalize(None) == "", "色の値をそろえる")
check(pc.text_color("#2F6FC9") == "#FFFFFF" and pc.text_color("#D4AC2B") == "#111111"
      and pc.text_color("#FFFFFF") == "#111111", "文字色は背景で白か黒")
check(pc.button_qss("") == "" and "#2F6FC9" in pc.button_qss("#2f6fc9"), "色なしはスタイルを付けない")

from ui.quick_nav_editor import NavItemRow
item = {"label": "Assets", "path": "D:/x"}
row = NavItemRow(item)
changed = []
row.changed.connect(lambda: changed.append(1))
m = row.color_menu()
acts = [a for a in m.actions() if not a.isSeparator()]
check(len(acts) == 10, "選べるのは 8 色＋なし＋その他（%d）" % len(acts))
acts[5].trigger()                    # 青
check(item.get("color") == "#2F6FC9" and changed, "色を選ぶとボタンの項目に入る（%r）" % (item,))
row._label_edit.setText("Assets2")
check(item.get("color") == "#2F6FC9" and item["label"] == "Assets2", "名前を変えても色は残る")
acts[8].trigger()                    # なし
check("color" not in item, "«なし» で色が消える")

from ui.nav_panels import QuickNavBar
sm.save_quick_nav_presets({"default": [{"label": "A", "path": "D:/a", "color": "#C0392B"},
                                       {"label": "B", "path": "D:/b"}]})
sm.set("quick_nav_preset", "default")
bar = QuickNavBar(sm)
QApplication.processEvents()
b = bar._buttons
check(len(b) == 2 and "#C0392B" in b[0].styleSheet() and b[1].styleSheet() == "",
      "保存した色がボタンに付く。色なしはテーマのまま")
finish(not fails)
