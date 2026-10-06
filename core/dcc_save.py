# -*- coding: utf-8 -*-
"""
接続中 DCC からの保存／書き出し（r70）
=======================================

- 右クリック「シーンを保存」「選択を書き出し」→ SaveDialog（ui/save_dialog.py）で
  ファイル名・拡張子・拡張子別オプションを決め、DCC へ Python を送る。
- 拡張子ごとのオプション定義（EXT_OPTIONS）と、Maya / Blender 側で実行する
  コードの組み立て（maya_code / blender_code）はここに集約する。
- オプション値は設定キー `save_options` に {dcc: {ext: {key: value}}} で記憶。

オプション定義の形式: (key, ラベル(ja), ラベル(en), 型, 既定値[, 選択肢])
    型: "bool" / "choice" / "int"
"""

import os

AUTO_EXT = "auto"           # 「Optional（任意）」＝ファイル名の拡張子で判定

# DCC が扱える書き出し形式（先頭ほど上に並ぶ。.ma/.mb/.fbx は必須）
MAYA_EXTS = [".ma", ".mb", ".fbx", ".obj", ".abc", ".usd", ".usda", ".usdc"]
BLENDER_EXTS = [".blend", ".fbx", ".obj", ".abc", ".usd", ".usda", ".usdc",
                ".gltf", ".glb", ".stl", ".ply"]

_FBX_VERSIONS = ["FBX202000", "FBX201900", "FBX201800", "FBX201600", "FBX201400"]

EXT_OPTIONS = {
    "maya": {
        ".ma": [("preserve_refs", "リファレンスを保持（書き出し時）", "Preserve references (export)", "bool", True)],
        ".mb": [("preserve_refs", "リファレンスを保持（書き出し時）", "Preserve references (export)", "bool", True)],
        ".fbx": [
            ("ascii", "ASCII 形式で保存", "ASCII format", "bool", False),
            ("version", "FBX バージョン", "FBX version", "choice", "FBX202000", _FBX_VERSIONS),
            ("animation", "アニメーションを含める", "Include animation", "bool", True),
            ("bake_anim", "アニメーションをベイク", "Bake animation", "bool", False),
            ("smoothing_groups", "スムージンググループ", "Smoothing groups", "bool", True),
            ("embed_media", "メディア（テクスチャ）を埋め込む", "Embed media", "bool", False),
            ("up_axis", "上方向", "Up axis", "choice", "y", ["y", "z"]),
        ],
        ".obj": [
            ("groups", "グループ", "Groups", "bool", True),
            ("materials", "マテリアル（.mtl）", "Materials (.mtl)", "bool", True),
            ("normals", "法線", "Normals", "bool", True),
            ("smoothing", "スムージング", "Smoothing", "bool", True),
        ],
        ".abc": [
            ("frame_range", "フレーム範囲（再生範囲）を書き出す", "Export playback range", "bool", False),
            ("uv_write", "UV を書き出す", "Write UVs", "bool", True),
            ("world_space", "ワールド空間", "World space", "bool", True),
            ("strip_namespaces", "Namespace を除去", "Strip namespaces", "bool", False),
        ],
        ".usd": [
            ("animation", "アニメーション（再生範囲）", "Animation (playback range)", "bool", False),
            ("materials", "マテリアル（UsdPreviewSurface）", "Materials (UsdPreviewSurface)", "bool", True),
            ("merge_shapes", "トランスフォームとシェイプを統合", "Merge transform and shape", "bool", True),
        ],
    },
    "blender": {
        ".blend": [("copy", "コピーとして保存（作業ファイルは切り替えない）",
                    "Save a copy (keep working file)", "bool", False)],
        ".fbx": [
            ("bake_anim", "アニメーションをベイク", "Bake animation", "bool", True),
            ("mesh_smooth", "スムージング", "Smoothing", "choice", "FACE", ["OFF", "FACE", "EDGE"]),
            ("apply_modifiers", "モディファイアを適用", "Apply modifiers", "bool", True),
            ("embed_textures", "テクスチャを埋め込む", "Embed textures", "bool", False),
            ("axis_up", "上方向", "Up axis", "choice", "Y", ["Y", "Z"]),
        ],
        ".obj": [
            ("materials", "マテリアル（.mtl）", "Materials (.mtl)", "bool", True),
            ("normals", "法線", "Normals", "bool", True),
            ("uv", "UV", "UVs", "bool", True),
            ("apply_modifiers", "モディファイアを適用", "Apply modifiers", "bool", True),
        ],
        ".abc": [
            ("frame_range", "フレーム範囲を書き出す", "Export frame range", "bool", False),
            ("uvs", "UV", "UVs", "bool", True),
            ("normals", "法線", "Normals", "bool", True),
        ],
        ".usd": [
            ("animation", "アニメーション", "Animation", "bool", False),
            ("materials", "マテリアル", "Materials", "bool", True),
        ],
        ".gltf": [("apply_modifiers", "モディファイアを適用", "Apply modifiers", "bool", True),
                  ("animation", "アニメーション", "Animation", "bool", True)],
        ".stl": [("apply_modifiers", "モディファイアを適用", "Apply modifiers", "bool", True)],
        ".ply": [("apply_modifiers", "モディファイアを適用", "Apply modifiers", "bool", True)],
    },
}
# 同系統の拡張子は定義を共有
for _d in ("maya", "blender"):
    for _e in (".usda", ".usdc"):
        EXT_OPTIONS[_d][_e] = EXT_OPTIONS[_d][".usd"]
EXT_OPTIONS["blender"][".glb"] = EXT_OPTIONS["blender"][".gltf"]


def exts_for(dcc: str):
    return list(BLENDER_EXTS if dcc == "blender" else MAYA_EXTS)


def options_for(dcc: str, ext: str):
    return list(EXT_OPTIONS.get(dcc, {}).get(ext.lower(), []))


def defaults_for(dcc: str, ext: str) -> dict:
    return {o[0]: o[4] for o in options_for(dcc, ext)}


def _q(p: str) -> str:
    return (p or "").replace("\\", "/").replace("'", "\\'")


# ---------------------------------------------------------------------------
# Maya 側コード（commandPort へ送る «単一式» exec(...) 形式）
# ---------------------------------------------------------------------------

def maya_code(path: str, mode: str, opts: dict) -> str:
    """mode: "save"（シーン全体）/ "export"（選択のみ）。
    .ma/.mb の save はシーンの保存先を切り替えて保存（以後そのファイルが現在の
    シーン）。他形式の save は exportAll。エラーは Maya 側 confirmDialog。"""
    ext = os.path.splitext(path)[1].lower()
    p = _q(path)
    sel = (mode == "export")
    body = []
    if sel:
        # r125: **空の選択でも «成功» していた。**
        # FBXExport -s は何も選ばれていなくても «中身の無い FBX» を黙って書く
        # （Maya 側はエラーにしない）。そのため「書き出しました」と出るのに
        # 中身が空、になっていた（ユーザー報告 2026-10-06「fbx の書き出しが
        # 正常にできていない」）。.abc にだけ入れていたガードを全形式へ。
        body.append("    _sel = cmds.ls(selection=True) or []")
        body.append("    if not _sel:")
        body.append("        raise RuntimeError('Maya で何も選択されていません。"
                    "書き出す対象を選んでから実行してください。')")
    if ext in (".ma", ".mb"):
        typ = "mayaAscii" if ext == ".ma" else "mayaBinary"
        if sel:
            body.append("    cmds.file('%s', exportSelected=True, type='%s', force=True, "
                        "preserveReferences=%s)" % (p, typ, bool(opts.get("preserve_refs", True))))
        else:
            body.append("    cmds.file(rename='%s')" % p)
            body.append("    cmds.file(save=True, type='%s', force=True)" % typ)
    elif ext == ".fbx":
        body.append("    cmds.loadPlugin('fbxmaya', quiet=True)")
        body.append("    import maya.mel as mel")
        body.append("    mel.eval('FBXResetExport')")
        body.append("    mel.eval('FBXExportInAscii -v %s')" % ("true" if opts.get("ascii") else "false"))
        body.append("    mel.eval('FBXExportFileVersion -v %s')" % opts.get("version", "FBX202000"))
        body.append("    mel.eval('FBXExportAnimationOnly -v false')")
        body.append("    mel.eval('FBXExportBakeComplexAnimation -v %s')" % ("true" if opts.get("bake_anim") else "false"))
        body.append("    mel.eval('FBXExportSmoothingGroups -v %s')" % ("true" if opts.get("smoothing_groups", True) else "false"))
        body.append("    mel.eval('FBXExportEmbeddedTextures -v %s')" % ("true" if opts.get("embed_media") else "false"))
        body.append("    mel.eval('FBXExportUpAxis %s')" % opts.get("up_axis", "y"))
        if not opts.get("animation", True):
            # r125: ここで FBXExportSkins -v false も落としていたが、Skins は
            # «スキン（バインド）を書き出すか» であってアニメーションではない。
            # 「アニメーションを含める」を外しただけでウェイトの無い FBX に
            # なっていた。アニメを外す＝キーを書かない、だけにする。
            body.append("    mel.eval('FBXExportBakeComplexAnimation -v false')")
            body.append("    mel.eval('FBXExportConstraints -v false')")
            body.append("    mel.eval('FBXExportCameras -v false')")
            body.append("    mel.eval('FBXExportLights -v false')")
        body.append("    mel.eval('FBXExport -f \"%s\"%s')" % (p, " -s" if sel else ""))
    elif ext == ".obj":
        body.append("    cmds.loadPlugin('objExport', quiet=True)")
        o = "groups=%d;ptgroups=%d;materials=%d;smoothing=%d;normals=%d" % (
            int(bool(opts.get("groups", True))), int(bool(opts.get("groups", True))),
            int(bool(opts.get("materials", True))), int(bool(opts.get("smoothing", True))),
            int(bool(opts.get("normals", True))))
        body.append("    cmds.file('%s', %s=True, type='OBJexport', force=True, options='%s')"
                    % (p, "exportSelected" if sel else "exportAll", o))
    elif ext == ".abc":
        body.append("    cmds.loadPlugin('AbcExport', quiet=True)")
        body.append("    _fr = ('-frameRange %%d %%d ' %% (cmds.playbackOptions(q=True, min=True), "
                    "cmds.playbackOptions(q=True, max=True))) if %s else ''"
                    % bool(opts.get("frame_range")))
        body.append("    _roots = ''.join(' -root ' + r for r in (cmds.ls(sl=True, long=True) or [])) if %s else ''"
                    % sel)
        body.append("    _job = _fr + ('-uvWrite ' if %s else '') + ('-worldSpace ' if %s else '') "
                    "+ ('-stripNamespaces ' if %s else '') + _roots + ' -file \"%s\"'"
                    % (bool(opts.get("uv_write", True)), bool(opts.get("world_space", True)),
                       bool(opts.get("strip_namespaces")), p))
        body.append("    cmds.AbcExport(j=_job)")
    elif ext in (".usd", ".usda", ".usdc"):
        body.append("    cmds.loadPlugin('mayaUsdPlugin', quiet=True)")
        body.append("    _kw = dict(file='%s', selection=%s, exportMaterials=%s, "
                    "mergeTransformAndShape=%s, defaultUSDFormat='%s')"
                    % (p, sel, bool(opts.get("materials", True)),
                       bool(opts.get("merge_shapes", True)),
                       "usda" if ext == ".usda" else "usdc"))
        body.append("    if %s: _kw['frameRange'] = (cmds.playbackOptions(q=True, min=True), "
                    "cmds.playbackOptions(q=True, max=True))" % bool(opts.get("animation")))
        body.append("    cmds.mayaUSDExport(**_kw)")
    else:
        body.append("    raise RuntimeError('未対応の形式: %s')" % ext)
    # r125: 結果に «何件を何バイトで» を載せる。ステータスバーと送信ログの
    # 両方に出るので、«空のファイルができた» がその場で分かる（今回の
    # 「中身が空」は、成功としか表示されないせいで気付けなかった）。
    body.append("    import os as _os")
    body.append("    _sz = _os.path.getsize('%s') if _os.path.isfile('%s') else -1" % (p, p))
    if sel:
        body.append("    _note = ' [選択 %d 件 / %d bytes]' % (len(_sel), _sz)")
    else:
        body.append("    _note = ' [%d bytes]' % _sz")
    inner = ("import maya.cmds as cmds\n"
             "try:\n" + "\n".join(body) + "\n"
             "    _mfm_result = 'saved:%s' + _note\n"
             "except Exception as _e:\n"
             "    cmds.confirmDialog(title='Maya File Manager', message=u'保存に失敗しました:\\n' + str(_e), button=['OK'])\n"
             "    _mfm_result = 'Error: ' + str(_e)\n" % p)
    # commandPort(python) は «単一の式» の値しか返さない → exec を式に包み結果を返す
    return "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result', ''))[1])({})" % inner


# ---------------------------------------------------------------------------
# Blender 側コード（resources/blender_bridge.py の名前空間で eval/exec）
# ---------------------------------------------------------------------------

def blender_code(path: str, mode: str, opts: dict) -> str:
    ext = os.path.splitext(path)[1].lower()
    p = _q(path)
    sel = (mode == "export")
    body = []
    if sel:
        # r125: Maya と同じ。use_selection=True は空選択でも中身の無い
        # ファイルを書いてしまう。
        body.append("    if not bpy.context.selected_objects:")
        body.append("        raise RuntimeError('Blender で何も選択されていません。"
                    "書き出す対象を選んでから実行してください。')")
    if ext == ".blend":
        if sel:
            body.append("    _objs = set(bpy.context.selected_objects)")
            body.append("    bpy.data.libraries.write('%s', _objs, fake_user=True)" % p)
        else:
            body.append("    bpy.ops.wm.save_as_mainfile(filepath='%s', copy=%s)"
                        % (p, bool(opts.get("copy"))))
    elif ext == ".fbx":
        body.append("    bpy.ops.export_scene.fbx(filepath='%s', use_selection=%s, bake_anim=%s, "
                    "mesh_smooth_type='%s', use_mesh_modifiers=%s, embed_textures=%s, "
                    "path_mode='%s', axis_up='%s')"
                    % (p, sel, bool(opts.get("bake_anim", True)), opts.get("mesh_smooth", "FACE"),
                       bool(opts.get("apply_modifiers", True)), bool(opts.get("embed_textures")),
                       "COPY" if opts.get("embed_textures") else "AUTO", opts.get("axis_up", "Y")))
    elif ext == ".obj":
        body.append("    if hasattr(bpy.ops.wm, 'obj_export'):")
        body.append("        bpy.ops.wm.obj_export(filepath='%s', export_selected_objects=%s, "
                    "export_materials=%s, export_normals=%s, export_uv=%s, apply_modifiers=%s)"
                    % (p, sel, bool(opts.get("materials", True)), bool(opts.get("normals", True)),
                       bool(opts.get("uv", True)), bool(opts.get("apply_modifiers", True))))
        body.append("    else:")
        body.append("        bpy.ops.export_scene.obj(filepath='%s', use_selection=%s, use_materials=%s, "
                    "use_normals=%s, use_uvs=%s, use_mesh_modifiers=%s)"
                    % (p, sel, bool(opts.get("materials", True)), bool(opts.get("normals", True)),
                       bool(opts.get("uv", True)), bool(opts.get("apply_modifiers", True))))
    elif ext == ".abc":
        body.append("    _s, _e = (bpy.context.scene.frame_start, bpy.context.scene.frame_end) if %s "
                    "else (bpy.context.scene.frame_current, bpy.context.scene.frame_current)"
                    % bool(opts.get("frame_range")))
        body.append("    bpy.ops.wm.alembic_export(filepath='%s', selected=%s, start=_s, end=_e, "
                    "uvs=%s, normals=%s)"
                    % (p, sel, bool(opts.get("uvs", True)), bool(opts.get("normals", True))))
    elif ext in (".usd", ".usda", ".usdc"):
        body.append("    bpy.ops.wm.usd_export(filepath='%s', selected_objects_only=%s, "
                    "export_animation=%s, export_materials=%s)"
                    % (p, sel, bool(opts.get("animation")), bool(opts.get("materials", True))))
    elif ext in (".gltf", ".glb"):
        body.append("    bpy.ops.export_scene.gltf(filepath='%s', use_selection=%s, "
                    "export_format='%s', export_apply=%s, export_animations=%s)"
                    % (p, sel, "GLB" if ext == ".glb" else "GLTF_SEPARATE",
                       bool(opts.get("apply_modifiers", True)), bool(opts.get("animation", True))))
    elif ext == ".stl":
        body.append("    if hasattr(bpy.ops.wm, 'stl_export'):")
        body.append("        bpy.ops.wm.stl_export(filepath='%s', export_selected_objects=%s, apply_modifiers=%s)"
                    % (p, sel, bool(opts.get("apply_modifiers", True))))
        body.append("    else:")
        body.append("        bpy.ops.export_mesh.stl(filepath='%s', use_selection=%s, use_mesh_modifiers=%s)"
                    % (p, sel, bool(opts.get("apply_modifiers", True))))
    elif ext == ".ply":
        body.append("    if hasattr(bpy.ops.wm, 'ply_export'):")
        body.append("        bpy.ops.wm.ply_export(filepath='%s', export_selected_objects=%s, apply_modifiers=%s)"
                    % (p, sel, bool(opts.get("apply_modifiers", True))))
        body.append("    else:")
        body.append("        bpy.ops.export_mesh.ply(filepath='%s', use_selection=%s, use_mesh_modifiers=%s)"
                    % (p, sel, bool(opts.get("apply_modifiers", True))))
    else:
        body.append("    raise RuntimeError('未対応の形式: %s')" % ext)
    # r125: Maya 側と同じく «何バイトになったか» を結果に載せる
    # （空のファイルができても «成功» としか出ないのを防ぐ）。
    inner = ("def _mfm_do():\n" + "\n".join(body) + "\n"
             "import os as _os\n"
             "try:\n"
             "    mfm_run(_mfm_do)\n"
             "    _mfm_sz = _os.path.getsize('%s') if _os.path.isfile('%s') else -1\n"
             "    _mfm_result = 'saved:%s' + (' [%%d bytes]' %% _mfm_sz)\n"
             "except Exception as _e:\n"
             "    mfm_popup('保存に失敗しました:\\n' + str(_e))\n"
             "    _mfm_result = 'Error: ' + str(_e)\n" % (p, p, p))
    # ブリッジは «式» なら値を返す: exec して _mfm_result を返す複合式にする
    return "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result', ''))[1])(dict(globals()))" % inner
