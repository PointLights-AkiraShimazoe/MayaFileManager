# -*- coding: utf-8 -*-
"""r88: Maya ⇔ Blender 切替が ON/OFF スイッチに見えないこと。
トラックの色もノブの色も «左右どちらでも同じ»（位置だけで選択を示す）。"""
from _common import *  # noqa: F401,F403
from _common import app, finish, run
from ui.dcc_header import DccSwitch


def _colors(sw):
    img = sw.grab().toImage()
    w, h = sw.width(), sw.height()
    track = img.pixelColor(w // 2, 3).name()                 # 上辺中央＝トラック
    kx = (w - h // 2 - 2) if sw.value() == "blender" else (h // 2 + 1)
    knob = img.pixelColor(kx, h // 2).name()                 # ノブの中心
    return track, knob


def step():
    a = DccSwitch("maya"); a.show()
    b = DccSwitch("blender"); b.show()
    app.processEvents()
    ta, ka = _colors(a)
    tb, kb = _colors(b)
    assert ta == tb, ("トラック色が左右で違う（ON/OFF に見える）", ta, tb)
    assert ka == kb, ("ノブの色が左右で違う", ka, kb)
    assert ka != ta, "ノブがトラックと同化している"
    # クリックで切り替わる挙動は従来どおり
    got = []
    a.changed.connect(got.append)
    a.set_value("blender")
    assert got == ["blender"] and a.value() == "blender"
    print("DCC selector looks the same on both sides (not on/off): OK")
    finish(True)


run(step, delay=300)
