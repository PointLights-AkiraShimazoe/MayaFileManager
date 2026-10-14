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
    # r146（ユーザー指示 2026-10-14）:
    #   「『プロジェクトをセットしなおしますか？』／現在：…／セット：… と
    #     してください。パスはわかりやすく表示出来るとありがたいです。」
    #
    # r145 までは «ファイルの場所» と «現在» を別々の段落で長々と出していて、
    # どこが違うのかを目で追う必要があった。実際のパスは
    # D:/users/OneDrive - PointLights/Projects/… のように頭が長く同じなので、
    # **共通の親をいったん括り出し、違うところだけを並べる**。
    # confirmDialog は飾りの付かない素のテキストなので、できるのは
    #   * 区切りを Windows の \\ に揃える
    #   * 共通の頭を 1 行にまとめる
    #   * 見出しの幅を揃えて «現在 / セット» を縦に並べる
    # の 3 つ。これだけで «どこが違うのか» が一目で分かる。
    q = tr("プロジェクトをセットしなおしますか？", "Set this project?")
    l_common = tr("共通　：%s", "Common：%s")
    l_cur = tr("現在　：%s", "Current：%s")
    l_new = tr("セット：%s", "Set　　：%s")
    none_txt = tr("（未設定）", "(none)")
    yes = tr("セットする", "Set Project")
    no = tr("セットしない", "Don't Set")
    log_set = tr("[MayaFileManager] プロジェクトをセット: ",
                 "[MayaFileManager] Set project: ")
    lines = [
        "import os as _mfm_os, __main__ as _mfm_main",
        # パスを «読める形» に整える（共通の頭を括り出す）。Maya 側で
        # 現在のプロジェクトが分かってから組み立てるので、ここに置く。
        "def _mfm_split_common(cur, new):",
        "    sep = '\\\\'",
        "    a = (cur or '').replace('/', sep).rstrip(sep)",
        "    b = (new or '').replace('/', sep).rstrip(sep)",
        "    if not a or not b:",
        "        return ('', a, b)",
        "    pa, pb = a.split(sep), b.split(sep)",
        "    i = 0",
        "    while (i < len(pa) - 1 and i < len(pb) - 1",
        "           and pa[i].lower() == pb[i].lower()):",
        "        i += 1",
        "    if i >= 2:",
        "        return (sep.join(pa[:i]), sep.join(pa[i:]), sep.join(pb[i:]))",
        "    return ('', a, b)",
        "import maya.cmds as _mfm_cmds, maya.mel as _mfm_mel",
        "_mfm_pj = %r" % pj,
        "_mfm_n = lambda p: _mfm_os.path.normcase(_mfm_os.path.normpath(p or '')).rstrip('\\\\/')",
        "_mfm_cur = _mfm_cmds.workspace(q=True, rootDirectory=True) or ''",
        "_mfm_decl = _mfm_main.__dict__.setdefault('_mfm_setproj_declined', set())",
        "if _mfm_n(_mfm_pj) != _mfm_n(_mfm_cur) and _mfm_n(_mfm_pj) not in _mfm_decl:",
        "    _mfm_head, _mfm_a, _mfm_b = _mfm_split_common(_mfm_cur, _mfm_pj)",
        "    _mfm_rows = []",
        "    if _mfm_head:",
        "        _mfm_rows.append(%r %% _mfm_head)" % l_common,
        "    _mfm_rows.append(%r %% (_mfm_a or %r))" % (l_cur, none_txt),
        "    _mfm_rows.append(%r %% _mfm_b)" % l_new,
        "    _mfm_msg = %r + '\\n\\n' + '\\n'.join(_mfm_rows)" % q,
        "    _mfm_ans = _mfm_cmds.confirmDialog(title='Maya File Manager',"
        " message=_mfm_msg,"
        " button=[%r, %r], defaultButton=%r, cancelButton=%r, dismissString=%r)"
        % (yes, no, yes, no, no),
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
