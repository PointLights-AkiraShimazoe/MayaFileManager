# -*- coding: utf-8 -*-
"""r125: 右クリックの DCC 項目は «アイコンで DCC、文字で動作» を示す。

ユーザー指示 2026-10-06:
  「5件、Maya という表記をなくし、アイコンを Maya のアイコンにしてください。
    そのうえで、『開く』『インポート』『リファレンス』『シーンを保存』
    『選択を書き出し』としてください。英語でも端的な単語にしてください。
    Blender も同じく、アイコンでツールを示し、テキストは端的な単語に」

ここで固定すること:
  1) 項目名に «Maya» / «Blender» が出ない（同じ語が 5 行並ぶのを避ける）
  2) 5 つの動作の日本語・英語がその通りであること
  3) **全項目にアイコンが付く**（アイコンが唯一の DCC の手がかりなので、
     空アイコンは «どっちか分からない» を意味する＝不可）
  4) どの DCC のどのコマンドかは QAction.data() で判る
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, sm
from core import i18n
import ui.browser_panel as bp

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


d = tmpdir()
for n in ("scene.ma", "asset.blend"):
    open(os.path.join(d, n), "w").close()

b = make_panel()
_keep.append(b)
b._dcc_callback = lambda *a: None
cur = {"dcc": "maya"}
b.set_dcc_target_provider(lambda: cur["dcc"])
b._current_path = d

captured = []


class _Menu(bp.QMenu):
    def exec_(self, *a, **k):
        captured.append([(x.data(), x.text(), not x.icon().isNull())
                         for x in self.actions() if x.text()])
    exec = exec_


bp.QMenu = _Menu


def items(*names):
    captured.clear()
    b._popup_context_menu([os.path.join(d, n) for n in names],
                          b.mapToGlobal(b.rect().center()))
    return captured[-1]


def dcc_items(rows):
    return [(dt, t, ic) for dt, t, ic in rows if isinstance(dt, tuple)]


def label_of(rows, key):
    for dt, t, _ic in rows:
        if dt == key:
            return t
    return None


# ── 日本語 ───────────────────────────────────────────────────────────
rows = items("scene.ma")
dis = dcc_items(rows)
check(bool(dis), "Maya の項目が出る（%d 件）" % len(dis))
check(all("Maya" not in t and "Blender" not in t for _d, t, _i in dis),
      "項目名にアプリ名が出ない（%r）" % [t for _d, t, _i in dis])
check(all(ic for _d, _t, ic in dis),
      "全項目にアイコンが付く（%r）" % [(d_, i) for d_, _t, i in dis])
for key, want in ((("maya", "open"), "開く"),
                  (("maya", "import"), "インポート"),
                  (("maya", "reference"), "リファレンス"),
                  (("maya", "save_scene"), "シーンを保存..."),
                  (("maya", "export_selection"), "選択を書き出し...")):
    check(label_of(rows, key) == want,
          "%s → %r（期待 %r）" % (key[1], label_of(rows, key), want))

cur["dcc"] = "blender"
rows = items("asset.blend")
dis = dcc_items(rows)
check(all("Blender" not in t and "Maya" not in t for _d, t, _i in dis),
      "Blender の項目名にもアプリ名が出ない（%r）" % [t for _d, t, _i in dis])
check(all(ic for _d, _t, ic in dis), "Blender の項目にもアイコンが付く")
for key, want in ((("blender", "open"), "開く"),
                  (("blender", "import"), "アペンド"),
                  (("blender", "reference"), "リンク")):
    check(label_of(rows, key) == want,
          "blender %s → %r（期待 %r）" % (key[1], label_of(rows, key), want))

# ── 英語 ─────────────────────────────────────────────────────────────
sm.set("ui_language", "en", save=False)
i18n.init(sm)
try:
    cur["dcc"] = "maya"
    rows = items("scene.ma")
    for key, want in ((("maya", "open"), "Open"),
                      (("maya", "import"), "Import"),
                      (("maya", "reference"), "Reference"),
                      (("maya", "save_scene"), "Save Scene..."),
                      (("maya", "export_selection"), "Export Selection...")):
        check(label_of(rows, key) == want,
              "en %s → %r（期待 %r）" % (key[1], label_of(rows, key), want))
    cur["dcc"] = "blender"
    rows = items("asset.blend")
    for key, want in ((("blender", "open"), "Open"),
                      (("blender", "import"), "Append"),
                      (("blender", "reference"), "Link")):
        check(label_of(rows, key) == want,
              "en blender %s → %r（期待 %r）" % (key[1], label_of(rows, key), want))
finally:
    sm.set("ui_language", "ja", save=False)
    i18n.init(sm)

finish(not fails)
