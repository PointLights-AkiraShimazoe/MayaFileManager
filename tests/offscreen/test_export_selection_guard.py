# -*- coding: utf-8 -*-
"""r125: «選択を書き出し» が空の選択で «成功» してはいけない。

ユーザー報告 2026-10-06:
  「fbx の書き出しが正常にできていない」→ 症状は «ファイルはできるが中身が空»

原因: FBXExport -s は何も選択されていなくても «中身の無い FBX» を黙って書き、
Maya はエラーにしない。.abc にだけガードがあり、他形式には無かった。
結果、「書き出しました」と出るのに中身が空、になっていた。

ここで固定すること:
  1) Maya / Blender とも、選択書き出しのコードには «空選択で止める» 行がある
  2) シーン保存（mode="save"）には入れない（選択と無関係なので）
  3) 結果に «何件 / 何バイト» が載る（空のファイルがその場で分かる）
  4) 生成コードは今までどおり単一式・構文エラー無し
"""
import ast
import re
from _common import *  # noqa: F401,F403
from _common import finish

from core import dcc_save

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def inner_of(code):
    """送信される単一式から «Maya/Blender 側で走る中身» を取り出す。"""
    m = re.search(r"exec\((.*?), _ns\)", code, re.S)
    assert m, code[:200]
    return ast.literal_eval(m.group(1))


MAYA_EXTS = [".ma", ".mb", ".fbx", ".obj", ".abc", ".usd"]
BLENDER_EXTS = [".blend", ".fbx", ".obj", ".abc", ".usd", ".stl", ".ply", ".gltf"]

for ext in MAYA_EXTS:
    opts = dcc_save.defaults_for("maya", ext)
    exp = inner_of(dcc_save.maya_code("C:/w/a" + ext, "export", opts))
    sav = inner_of(dcc_save.maya_code("C:/w/a" + ext, "save", opts))
    check("何も選択されていません" in exp, "maya %-6s 書き出しに空選択ガードがある" % ext)
    check("何も選択されていません" not in sav, "maya %-6s 保存にはガードを入れない" % ext)
    check("_sz" in exp and "bytes" in exp, "maya %-6s 結果にバイト数が載る" % ext)
    check("選択 %d 件" in exp, "maya %-6s 結果に選択件数が載る" % ext)
    for src in (exp, sav):
        try:
            ast.parse(src)
        except SyntaxError as e:
            check(False, "maya %s 生成コードが壊れている: %s" % (ext, e))

for ext in BLENDER_EXTS:
    opts = dcc_save.defaults_for("blender", ext)
    exp = inner_of(dcc_save.blender_code("C:/w/a" + ext, "export", opts))
    sav = inner_of(dcc_save.blender_code("C:/w/a" + ext, "save", opts))
    check("何も選択されていません" in exp, "blender %-6s 書き出しに空選択ガードがある" % ext)
    check("何も選択されていません" not in sav, "blender %-6s 保存にはガードを入れない" % ext)
    check("bytes" in exp, "blender %-6s 結果にバイト数が載る" % ext)
    for src in (exp, sav):
        try:
            ast.parse(src)
        except SyntaxError as e:
            check(False, "blender %s 生成コードが壊れている: %s" % (ext, e))

# r125: «アニメーションを含めない» がスキンまで落としていた回帰を防ぐ
no_anim = dict(dcc_save.defaults_for("maya", ".fbx"))
no_anim["animation"] = False
src = inner_of(dcc_save.maya_code("C:/w/a.fbx", "export", no_anim))
check("FBXExportSkins -v false" not in src,
      "アニメ無効でスキンを落とさない（Skins はアニメではない）")
check("FBXExportBakeComplexAnimation -v false" in src,
      "アニメ無効ならベイクはしない")

finish(not fails)
