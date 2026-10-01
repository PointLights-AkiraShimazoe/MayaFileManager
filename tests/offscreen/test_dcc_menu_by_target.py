# -*- coding: utf-8 -*-
"""r80: 右クリックの DCC 項目は「ヘッダで選択中の DCC 1つ分」だけ出る。
交換形式(.fbx 等) → 選択中 DCC のみ／.ma .mb → 常に Maya／.blend → 常に Blender。
以前は .fbx で Maya と Blender の両方が並んでいた（仕様外）。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, tmpdir, finish, run
import ui.browser_panel as bp

b = make_panel()
d = tmpdir()
for n in ("Komano.fbx", "scene.ma", "asset.blend", "note.txt"):
    open(os.path.join(d, n), "w").close()
b.navigate_to(d)
b._dcc_callback = lambda app_, action, paths: None
cur = {"dcc": "maya"}
b.set_dcc_target_provider(lambda: cur["dcc"])

captured = []


class _Menu(bp.QMenu):
    def exec_(self, *a, **k):
        captured.append([x.text() for x in self.actions() if x.text()])
    exec = exec_


bp.QMenu = _Menu


def texts(*names):
    captured.clear()
    b._popup_context_menu([os.path.join(d, n) for n in names], b.mapToGlobal(b.rect().center()))
    return captured[-1]


def has(ts, word):
    return any(word in t for t in ts)


def step():
    # .fbx: 選択中 DCC のみ
    cur["dcc"] = "maya"
    ts = texts("Komano.fbx")
    assert has(ts, "Maya にインポート") and not has(ts, "Blender にインポート"), ts
    cur["dcc"] = "blender"
    ts = texts("Komano.fbx")
    assert has(ts, "Blender にインポート") and not has(ts, "Maya にインポート"), ts
    print("interchange (.fbx) follows selected DCC only: OK")
    # ネイティブ形式は選択中 DCC に関係なく
    ts = texts("scene.ma")                       # dcc=blender のまま
    assert has(ts, "Maya で開く") and not has(ts, "Blender"), ts
    cur["dcc"] = "maya"
    ts = texts("asset.blend")
    assert has(ts, "Blender で開く") and not has(ts, "Maya にインポート"), ts
    print("native formats always target their own DCC: OK")
    # 混在: .ma + .fbx → Maya のみ、.blend + .fbx → Blender のみ
    cur["dcc"] = "blender"
    ts = texts("scene.ma", "Komano.fbx")
    assert has(ts, "Maya にインポート") and not has(ts, "Blender"), ts
    cur["dcc"] = "maya"
    ts = texts("asset.blend", "Komano.fbx")
    assert has(ts, "Blender にインポート") and not has(ts, "Maya"), ts
    ts = texts("note.txt")
    assert not has(ts, "Maya にインポート") and not has(ts, "Blender にインポート"), ts
    print("mixed selections: OK")
    finish(True)


run(step)
