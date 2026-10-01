# -*- coding: utf-8 -*-
"""r71: Blender 検出（明示パス／標準外フォルダ名からのバージョン判定）と、
ヘッダコンボの展開リストが文字幅・項目数に合わせて広がること。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, tmpdir, finish, run, QTimer
from core import blender_version as bv
from ui.dcc_header import _PopupCombo, DccHeader


def s1():
    # 1) 明示パス（設定 blender_exe 相当）は標準外の場所でも拾い、フォルダ名から版を取る
    d = tmpdir()
    exe_dir = os.path.join(d, "Blender 5.2")
    os.makedirs(os.path.join(exe_dir, "5.2"))
    exe = os.path.join(exe_dir, "blender.exe")
    open(exe, "wb").close()
    found = bv.find_installed_blender_versions([exe, exe.upper(), ""])
    mine = [b for b in found if str(b.executable).lower() == os.path.normpath(exe).lower()]
    assert len(mine) == 1, ("重複または未検出", found)
    assert mine[0].version == "5.2", mine[0].version
    # フォルダ名に版が無くても隣の «X.Y» フォルダから判定
    exe_dir2 = os.path.join(d, "portable")
    os.makedirs(os.path.join(exe_dir2, "4.5"))
    exe2 = os.path.join(exe_dir2, "blender.exe")
    open(exe2, "wb").close()
    found = bv.find_installed_blender_versions([exe2])
    assert any(b.version == "4.5" for b in found), found
    rep = bv.detection_report([exe])
    assert "5.2" in rep
    # r75: Microsoft Store 版はパッケージフォルダ名から版を取り、ACL で stat が
    # 拒否されても «存在» とみなす
    from pathlib import Path
    sp = Path(r"C:\Program Files\WindowsApps\BlenderFoundation.Blender_5.2.1.0_x64__ppwjx1n5r4v9t\Blender\blender.exe")
    assert bv._version_from_store_path(sp) == "5.2"
    store_dir = os.path.join(d, "WindowsApps", "BlenderFoundation.Blender_4.5.0.0_x64__abc", "Blender")
    os.makedirs(store_dir); sexe = os.path.join(store_dir, "blender.exe"); open(sexe, "wb").close()
    found = bv.find_installed_blender_versions([sexe])
    assert any(b.version == "4.5" and str(b.executable).lower() == os.path.normpath(sexe).lower() for b in found), found
    import unittest.mock as um
    with um.patch.object(Path, "is_file", side_effect=PermissionError("acl")):
        assert bv._exe_present(sp) is True
        assert bv._exe_present(Path(r"C:\nope\blender.exe")) is False
    # r76: Store 版（WindowsApps / エイリアス）はシェルのアイコン取得をしない（起動クラッシュ）
    from ui.dcc_header import app_icon_for
    assert app_icon_for(r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\blender-launcher.exe").isNull()
    assert app_icon_for(sp).isNull()
    # r79: WindowsApps 直下の exe は直接起動できない → 同パッケージの実行エイリアスへ
    alias_root = os.path.join(d, "LocalAppData", "Microsoft", "WindowsApps")
    fam = os.path.join(alias_root, "BlenderFoundation.Blender_ppwjx1n5r4v9t"); os.makedirs(fam)
    alias = os.path.join(fam, "blender-launcher.exe"); open(alias, "wb").close()
    with um.patch.dict(os.environ, {"LOCALAPPDATA": os.path.join(d, "LocalAppData")}):
        got = bv._launchable_exe(sp)
    assert str(got).lower() == os.path.normpath(alias).lower(), got
    assert bv._launchable_exe(Path(exe)) == Path(exe)     # 通常インストールはそのまま
    print("blender detection via explicit path / version from dir: OK")

    # 2) 展開リストは項目の文字幅・項目数に合わせる（本体は固定幅のまま）
    hdr = DccHeader("maya"); hdr.show()
    c = hdr.maya_conn
    assert isinstance(c, _PopupCombo) and c.width() == 196
    long = "Maya 2027 — some_really_long_scene_name_v012_final.ma (:20261)"
    c.addItem(long, 20261)
    c.addItem("Maya 2026 — b.ma (:20262)", 20262)
    c.showPopup()
    app.processEvents()
    v = c.view()
    need = v.fontMetrics().horizontalAdvance(long)
    assert v.minimumWidth() >= need + 40 and v.minimumWidth() > c.width(), (v.minimumWidth(), need)
    assert v.minimumHeight() >= 2 * 22, v.minimumHeight()
    c.hidePopup()
    assert c.width() == 196, "本体の幅が変わった"
    # r79: exe からアイコンが取れなくてもユーザー提供の resources/icons/app_blender.png を使う
    hdr.set_app_icons(None, r"C:\Program Files\WindowsApps\x\blender.exe")
    assert not hdr.blender_badge.icon().isNull() and hdr.blender_badge.text() == "", "app_blender.png not used"
    print("header combo popup widens to content: OK")
    finish()


run(s1, delay=500)
