# -*- coding: utf-8 -*-
"""DCC コマンドごとの «確実に扱える形式» の一覧（r91）。

ユーザー指示（2026-09-23）: «Maya・Blender 問わず、絶対にそのコマンドが対象の
ものにだけ反応する»。.py を D&D したら «開く» を送ろうとした、への対策。

ここに無い形式には、どの経路（クリック動作・右クリック・D&D・ブックマーク）
からでも DCC へコマンドを送らない。判定は MainWindow の送信直前
（_dcc_open / _dcc_import / _dcc_reference / _maya_run_script）で必ず行う。
右クリックメニューの項目もこの表から出す（見えるのに送れない、を作らない）。
"""

import os

_USD = {".usd", ".usda", ".usdc", ".usdz"}

CAPS = {
    "maya": {
        # 開く＝シーンとして開く。Maya のシーン形式のみ
        "open": {".ma", ".mb"},
        # インポート: 取り込み用プラグインを送信コード側で必ずロードする
        "import": {".ma", ".mb", ".fbx", ".obj", ".abc"} | _USD,
        # リファレンス: Maya がリファレンスとして確実に扱える形式
        "reference": {".ma", ".mb", ".fbx", ".abc"},
        # D&D で落とされたスクリプト（Maya と同じ作法で実行）
        "run_script": {".py", ".mel"},
    },
    "blender": {
        "open": {".blend"},
        "import": {".blend", ".fbx", ".obj", ".abc", ".gltf", ".glb", ".stl",
                   ".ply"} | _USD,
        # Blender の «リファレンス» はライブラリリンク＝ .blend のみ
        "reference": {".blend"},
    },
}

# Maya で取り込む時に先にロードするプラグイン
MAYA_PLUGIN_FOR_EXT = {
    ".fbx": "fbxmaya",
    ".obj": "objExport",
    ".abc": "AbcImport",
    ".usd": "mayaUsdPlugin", ".usda": "mayaUsdPlugin",
    ".usdc": "mayaUsdPlugin", ".usdz": "mayaUsdPlugin",
}

_COMMAND_ALIASES = {"reference_ask": "reference", "link": "reference"}


def exts(app: str, command: str) -> set:
    app = "blender" if app == "blender" else "maya"
    command = _COMMAND_ALIASES.get(command, command)
    return set(CAPS.get(app, {}).get(command, set()))


def supports(app: str, command: str, path: str) -> bool:
    """app の command が path を «確実に» 扱えるか。フォルダは常に False。"""
    if not path:
        return False
    try:
        if os.path.isdir(path):
            return False
    except OSError:
        return False
    return os.path.splitext(path)[1].lower() in exts(app, command)


def supports_all(app: str, command: str, paths) -> bool:
    paths = list(paths or [])
    return bool(paths) and all(supports(app, command, p) for p in paths)


def command_label(command: str) -> str:
    from core.i18n import tr
    command = _COMMAND_ALIASES.get(command, command)
    return {
        "open": tr("開く", "Open"),
        "import": tr("インポート", "Import"),
        "reference": tr("リファレンス", "Reference"),
        "run_script": tr("スクリプト実行", "Run script"),
    }.get(command, command)
