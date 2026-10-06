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
        # r125: 項目名から «Maya» «Blender» を外した（アイコンで示す）ので、
        # «どの DCC のどのコマンドか» は QAction.data() で見る。
        captured.append([(x.data(), x.text()) for x in self.actions() if x.text()])
    exec = exec_


bp.QMenu = _Menu


def texts(*names):
    captured.clear()
    b._popup_context_menu([os.path.join(d, n) for n in names], b.mapToGlobal(b.rect().center()))
    return captured[-1]


def has(ts, key):
    """key は (dcc, action) か、表示名の部分一致。"""
    if isinstance(key, tuple):
        return any(dt == key for dt, _t in ts)
    return any(key in t for _d, t in ts)


def has_dcc(ts, dcc):
    return any(isinstance(dt, tuple) and dt[0] == dcc for dt, _t in ts)


def step():
    # .fbx: 選択中 DCC のみ
    cur["dcc"] = "maya"
    ts = texts("Komano.fbx")
    assert has(ts, ("maya", "import")) and not has(ts, ("blender", "import")), ts
    cur["dcc"] = "blender"
    ts = texts("Komano.fbx")
    assert has(ts, ("blender", "import")) and not has(ts, ("maya", "import")), ts
    print("interchange (.fbx) follows selected DCC only: OK")
    # ネイティブ形式は選択中 DCC に関係なく
    ts = texts("scene.ma")                       # dcc=blender のまま
    assert has(ts, ("maya", "open")) and not has_dcc(ts, "blender"), ts
    cur["dcc"] = "maya"
    ts = texts("asset.blend")
    assert has(ts, ("blender", "open")) and not has(ts, ("maya", "import")), ts
    print("native formats always target their own DCC: OK")
    # 混在: .ma + .fbx → Maya のみ、.blend + .fbx → Blender のみ
    cur["dcc"] = "blender"
    ts = texts("scene.ma", "Komano.fbx")
    assert has(ts, ("maya", "import")) and not has_dcc(ts, "blender"), ts
    cur["dcc"] = "maya"
    ts = texts("asset.blend", "Komano.fbx")
    assert has(ts, ("blender", "import")) and not has_dcc(ts, "maya"), ts
    ts = texts("note.txt")
    assert not has(ts, ("maya", "import")) and not has(ts, ("blender", "import")), ts
    print("mixed selections: OK")
    # r125: 項目名にアプリ名を出さない／アイコンは必ず付く
    cur["dcc"] = "maya"
    ts = texts("scene.ma")
    for dt, t in ts:
        assert "Maya" not in t and "Blender" not in t, (dt, t)
    print("menu labels carry no app name: OK")
    finish(True)


run(step)
