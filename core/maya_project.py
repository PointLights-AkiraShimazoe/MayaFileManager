# -*- coding: utf-8 -*-
"""Maya ファイルを読み込む時の «プロジェクトのセット» 確認（r89）。

開く／インポート／リファレンスの前に、ファイルの場所から上の階層へ
workspace.mel を探し、見つかったフォルダが «今の Maya のプロジェクト» と
違えば、Maya 側で「このプロジェクトをセットしますか？」と確認する。
  - workspace.mel が無い → 何もしない
  - 既にそのプロジェクト → 何もしない
  - 「セットしない」を選んだプロジェクトは、その Maya のセッション中は再確認しない
    （複数ファイルを続けてリファレンスした時に毎回聞かれないように）

探索はマネージャー側（ファイル操作）、比較と確認は Maya 側で行う。
今のプロジェクトは Maya しか知らず、マネージャーから問い合わせると
Maya がビジーな時に UI が止まるため（commandPort の応答待ちはしない方針）。
"""

import os
from typing import Optional

WORKSPACE_FILE = "workspace.mel"
_MAX_LEVELS = 40


def find_project_root(path: str) -> Optional[str]:
    """path（ファイル or フォルダ）から上へ登り、workspace.mel を含む最初の
    フォルダを返す。無ければ None。ドライブ直下まで見る。"""
    if not path:
        return None
    try:
        d = path if os.path.isdir(path) else os.path.dirname(path)
    except OSError:
        return None
    d = os.path.normpath(d)
    for _ in range(_MAX_LEVELS):
        try:
            if os.path.isfile(os.path.join(d, WORKSPACE_FILE)):
                return d
        except OSError:
            pass
        parent = os.path.dirname(d)
        if not parent or parent == d:
            break
        d = parent
    return None


def setproject_code(path: str, indent: str = "") -> str:
    """Maya 側で実行する «プロジェクトのセット確認» の Python 文を返す。
    該当プロジェクトが無ければ空文字（＝何も挿入しない）。
    indent は挿入先ブロックの字下げ（if 文の中などに差し込む時用）。"""
    from core.i18n import tr
    root = find_project_root(path)
    if not root:
        return ""
    pj = root.replace("\\", "/")
    msg = tr("このファイルは次のプロジェクトの中にあります:\n%s\n\n"
             "現在のプロジェクト:\n%s\n\nこのプロジェクトをセットしますか？",
             "This file is inside the project:\n%s\n\n"
             "Current project:\n%s\n\nSet this project?")
    yes = tr("セットする", "Set Project")
    no = tr("セットしない", "Don't Set")
    log_set = tr("[MayaFileManager] プロジェクトをセット: ",
                 "[MayaFileManager] Set project: ")
    lines = [
        "import os as _mfm_os, __main__ as _mfm_main",
        "import maya.cmds as _mfm_cmds, maya.mel as _mfm_mel",
        "_mfm_pj = %r" % pj,
        "_mfm_n = lambda p: _mfm_os.path.normcase(_mfm_os.path.normpath(p or '')).rstrip('\\\\/')",
        "_mfm_cur = _mfm_cmds.workspace(q=True, rootDirectory=True) or ''",
        "_mfm_decl = _mfm_main.__dict__.setdefault('_mfm_setproj_declined', set())",
        "if _mfm_n(_mfm_pj) != _mfm_n(_mfm_cur) and _mfm_n(_mfm_pj) not in _mfm_decl:",
        "    _mfm_ans = _mfm_cmds.confirmDialog(title='Maya File Manager',"
        " message=%r %% (_mfm_pj, _mfm_cur or '-'),"
        " button=[%r, %r], defaultButton=%r, cancelButton=%r, dismissString=%r)"
        % (msg, yes, no, yes, no, no),
        "    if _mfm_ans == %r:" % yes,
        "        try:",
        "            _mfm_mel.eval('setProject \"%s\"' % _mfm_pj)",
        "        except Exception:",
        "            _mfm_cmds.workspace(_mfm_pj, openWorkspace=True)",
        "        try:",
        "            import maya.api.OpenMaya as _mfm_om",
        "            _mfm_om.MGlobal.displayInfo(%r + _mfm_pj)" % log_set,
        "        except Exception:",
        "            pass",
        "    else:",
        "        _mfm_decl.add(_mfm_n(_mfm_pj))",
    ]
    return "".join(indent + l + "\n" for l in lines)
