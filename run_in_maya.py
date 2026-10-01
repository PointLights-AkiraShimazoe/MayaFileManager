# -*- coding: utf-8 -*-
"""
Maya内モードの起動スニペット（Maya 2023〜2027 共通）
=====================================================

Maya のスクリプトエディタ（Python）で以下を実行するか、シェルフボタンに登録する:

    exec(open(r"D:\\Claude\\PLs-Tools\\MayaFileManager\\run_in_maya.py",
              encoding="utf-8").read())

- ツールのフォルダを sys.path に追加し、main.show_in_maya() でウィンドウを出す。
- 2回目以降は既存ウィンドウを前面に出す（多重起動しない）。
- 開発中にコードを更新した後は、Maya を再起動するか
  `MFM_RELOAD = True` を設定してから実行すると、モジュールを読み直す。
"""
import os
import sys

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() \
    else r"D:\Claude\PLs-Tools\MayaFileManager"

if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

if globals().get("MFM_RELOAD"):
    # 開発用: ツールのモジュールを全て破棄して読み直す
    for _name in list(sys.modules):
        if _name == "main" or _name.startswith(("core.", "ui.", "core", "ui")):
            _mod = sys.modules.get(_name)
            if _mod is not None and getattr(_mod, "__file__", "") and \
                    os.path.abspath(_mod.__file__).startswith(_TOOL_DIR):
                del sys.modules[_name]

import main  # noqa: E402
main.show_in_maya()
