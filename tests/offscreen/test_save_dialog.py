# -*- coding: utf-8 -*-
"""r70: DCC からの保存／書き出し — 送信コードの構文、ダイアログの拡張子連動・
オプション記憶、空白右クリック（フォルダメニュー）とファイル右クリックの項目。"""
import ast, os
from _common import *  # noqa: F401,F403
from _common import app, sm, make_panel, find_item, tmpdir, finish, run, QTimer, Qt
from core import dcc_save
from core.compat import QMenu, QComboBox, QCheckBox, QPoint
from ui.save_dialog import SaveDialog

b = make_panel()
d = tmpdir()
for n in ("scene.mb", "chr_A.v012.ma", "mesh.fbx", "note.txt"):
    open(os.path.join(d, n), "w").close()
os.makedirs(os.path.join(d, "sub"))
b.navigate_to(d)


def _inner_source(code: str) -> str:
    """送信コード（単一式）から exec に渡す内部ソースを取り出す。"""
    tree = ast.parse(code, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "exec":
            arg = node.args[0]
            return arg.value if isinstance(arg, ast.Constant) else arg.s
    raise AssertionError("exec(...) not found")


def s1():
    # 1) 全形式 × 両モード × 両DCC の送信コードが «単一式» で、内部も構文OK
    n = 0
    for dcc, fn in (("maya", dcc_save.maya_code), ("blender", dcc_save.blender_code)):
        for ext in dcc_save.exts_for(dcc):
            for mode in ("save", "export"):
                path = os.path.join(d, "out" + ext)
                opts = dcc_save.defaults_for(dcc, ext)
                code = fn(path, mode, opts)
                assert "\n" not in code, (dcc, ext, "must be one line")
                compile(code, "<outer>", "eval")
                inner = _inner_source(code)
                compile(inner, "<inner>", "exec")
                assert "_mfm_result" in inner and "Error:" in inner, (dcc, ext)
                n += 1
    # 抜き取り: Maya の保存はシーンの保存先を切替、書き出しは exportSelected
    inner = _inner_source(dcc_save.maya_code(os.path.join(d, "a.mb"), "save", {}))
    assert "rename=" in inner and "mayaBinary" in inner and "exportSelected" not in inner
    inner = _inner_source(dcc_save.maya_code(os.path.join(d, "a.ma"), "export", {}))
    assert "exportSelected=True" in inner and "mayaAscii" in inner
    inner = _inner_source(dcc_save.maya_code(os.path.join(d, "a.fbx"), "export",
                                             {"ascii": True, "version": "FBX201800"}))
    assert "FBXExportInAscii -v true" in inner and "FBX201800" in inner and "-s'" in inner
    inner = _inner_source(dcc_save.maya_code(os.path.join(d, "a.abc"), "export", {"frame_range": True}))
    assert "-frameRange %d %d" in inner, inner
    inner = _inner_source(dcc_save.blender_code(os.path.join(d, "a.fbx"), "export", {}))
    assert "use_selection=True" in inner and "mfm_run" in inner
    print("dcc_save codes compile (%d variants): OK" % n)

    # 2) ダイアログ: 選択ファイル名が初期値、拡張子プルダウンで名前の拡張子が変わる
    dlg = SaveDialog("maya", "export", d, "scene.mb", sm)
    # r86: コンボの既定は Optional。ファイル名は選択ファイルのまま
    assert dlg._name_edit.text() == "scene.mb", dlg._name_edit.text()
    assert dlg.selected_ext() == dcc_save.AUTO_EXT and dlg.effective_ext() == ".mb"
    assert dlg._name_edit.selectedText() == "scene", dlg._name_edit.selectedText()
    assert "preserve_refs" in dlg._opt_widgets
    dlg._ext_combo.setCurrentIndex(dlg._ext_combo.findData(".fbx"))
    assert dlg._name_edit.text() == "scene.fbx", dlg._name_edit.text()
    assert isinstance(dlg._opt_widgets.get("version"), QComboBox) and \
        isinstance(dlg._opt_widgets.get("ascii"), QCheckBox)
    dlg._opt_widgets["ascii"].setChecked(True)
    dlg._opt_widgets["version"].setCurrentIndex(dlg._opt_widgets["version"].findData("FBX201800"))
    # Optional（任意）: 入力した拡張子で判定し、オプションも追従
    dlg._ext_combo.setCurrentIndex(0)
    assert dlg.selected_ext() == dcc_save.AUTO_EXT and dlg._name_edit.text() == "scene.fbx"
    dlg._name_edit.setText("scene.abc")
    assert dlg.effective_ext() == ".abc" and "uv_write" in dlg._opt_widgets
    dlg._name_edit.setText("scene.fbx")
    assert dlg._opt_widgets["ascii"].isChecked(), "同一ダイアログ内でオプションが失われた"
    # «.» を含む名前は既知の拡張子だけ差し替える
    dlg._name_edit.setText("chr_A.v012.ma")
    dlg._ext_combo.setCurrentIndex(dlg._ext_combo.findData(".mb"))
    assert dlg._name_edit.text() == "chr_A.v012.mb", dlg._name_edit.text()
    dlg._ext_combo.setCurrentIndex(dlg._ext_combo.findData(".fbx"))
    dlg._name_edit.setText("newname.fbx")
    assert dlg.path().replace("\\", "/") == os.path.join(d, "newname.fbx").replace("\\", "/")
    dlg._on_accept()
    assert dlg.result_path().endswith("newname.fbx") and dlg.result_options()["ascii"] is True
    assert dlg.result_options()["version"] == "FBX201800"
    code = dlg.dcc_code()
    assert "FBXExportInAscii -v true" in _inner_source(code)
    # 記憶: 次回は直近の形式（export→.fbx）とオプションが復元される
    saved = sm.get("save_options")["maya"][".fbx"]
    assert saved["ascii"] is True and saved["version"] == "FBX201800", saved
    dlg2 = SaveDialog("maya", "export", d, "", sm)
    # r86: コンボの既定は «Optional（任意）»。形式はファイル名側で決まる
    assert dlg2.selected_ext() == dcc_save.AUTO_EXT, dlg2.selected_ext()
    assert dlg2.effective_ext() == ".fbx", dlg2.effective_ext()
    assert dlg2._name_edit.text() == ".fbx", dlg2._name_edit.text()
    assert not dlg2._ok.isEnabled(), "名前が空なのに保存できる"
    assert dlg2._opt_widgets["ascii"].isChecked() and \
        dlg2._opt_widgets["version"].currentData() == "FBX201800"
    dlg2._name_edit.setText("x")
    assert dlg2._ok.isEnabled() and dlg2.path().endswith("x.fbx")
    # 別モードは別記憶（save の既定は .ma）。コンボは Optional、実効は .ma
    dlg3 = SaveDialog("maya", "save", d, "", sm)
    assert dlg3.selected_ext() == dcc_save.AUTO_EXT, dlg3.selected_ext()
    assert dlg3.effective_ext() == ".ma", dlg3.effective_ext()
    assert dlg3._ext_combo.currentIndex() == 0, "既定が Optional になっていない"
    # Blender
    dlg4 = SaveDialog("blender", "save", d, "", sm)
    assert dlg4.effective_ext() == ".blend" and "copy" in dlg4._opt_widgets
    exts = [dlg4._ext_combo.itemData(i) for i in range(dlg4._ext_combo.count())]
    assert exts[0] == dcc_save.AUTO_EXT and ".gltf" in exts and ".ma" not in exts
    # 明示的に選べば従来どおりそれが使われる
    dlg4._ext_combo.setCurrentIndex(dlg4._ext_combo.findData(".gltf"))
    assert dlg4.selected_ext() == ".gltf" and dlg4.effective_ext() == ".gltf"
    for x in (dlg, dlg2, dlg3, dlg4):
        x.close()
    print("SaveDialog ext sync / auto / options remembered: OK")

    # 3) メニュー項目は «選択中の DCC 1つ分（2項目）» だけ。
    #    フォルダ/共通形式 → 選択中 DCC、.mb → 常に Maya、.txt → 無し
    calls = []
    b._dcc_callback = lambda app_, action, paths: calls.append((app_, action, list(paths)))
    cur = {"dcc": "maya"}
    b.set_dcc_target_provider(lambda: cur["dcc"])
    m = QMenu(); assert b._add_dcc_save_actions(m, d)
    texts = [a.text() for a in m.actions()]
    assert len(texts) == 2 and all("Maya" in t for t in texts), texts
    m.actions()[0].trigger()
    assert calls and calls[-1] == ("maya", "save_scene", [d]), calls
    m.actions()[1].trigger()
    assert calls[-1][1] == "export_selection"
    cur["dcc"] = "blender"
    m = QMenu(); assert b._add_dcc_save_actions(m, d)
    assert len(m.actions()) == 2 and all("Blender" in a.text() for a in m.actions())
    m.actions()[0].trigger(); assert calls[-1][0] == "blender"
    m = QMenu(); assert b._add_dcc_save_actions(m, os.path.join(d, "mesh.fbx"))
    assert len(m.actions()) == 2 and all("Blender" in a.text() for a in m.actions())
    m = QMenu(); assert b._add_dcc_save_actions(m, os.path.join(d, "scene.mb"))
    assert len(m.actions()) == 2 and all("Maya" in a.text() for a in m.actions()), "「.mb は常に Maya」"
    m = QMenu(); assert not b._add_dcc_save_actions(m, os.path.join(d, "note.txt"))
    cur["dcc"] = "maya"
    print("DCC save/export menu entries follow selected DCC / native format: OK")

    # 4) 空白の右クリック → そのカラムのフォルダメニュー
    got = []
    b._popup_folder_context_menu = lambda folder, gpos: got.append(folder)
    b._popup_context_menu = lambda paths, gpos: got.append(("items", list(paths)))
    cv, rect, _ = find_item(b, "sub"); assert cv
    last_bottom = max(cv.visualRect(cv.model().index(r, 0, cv.rootIndex())).bottom()
                      for r in range(cv.model().rowCount(cv.rootIndex())))
    assert last_bottom + 40 < cv.viewport().height(), "空白域が無い"
    local = QPoint(rect.center().x(), last_bottom + 30)          # 最終項目の下の空白
    colpos = b._column_view.viewport().mapFromGlobal(cv.viewport().mapToGlobal(local))
    b.sender = lambda: b._column_view
    b._show_context_menu(colpos)
    assert got and got[-1] and isinstance(got[-1], str) and \
        got[-1].replace("\\", "/").rstrip("/") == d.replace("\\", "/").rstrip("/"), got
    # 項目上は従来どおり
    got.clear()
    colpos = b._column_view.viewport().mapFromGlobal(cv.viewport().mapToGlobal(rect.center()))
    b._show_context_menu(colpos)
    assert got and got[-1][0] == "items", got
    print("empty-area right-click → folder menu / item → item menu: OK")
    finish()


run(s1)
