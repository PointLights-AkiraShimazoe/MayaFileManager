"""
Merge Browse Core
=================
複数フォルダ選択時の「マージ表示」の中核ロジック（ビュー非依存・純Python）。

仕様（ユーザー確定 2026-06-19 / 2026-06-20 補足）:
- 複数の選択フォルダの子をマージして1カラム分の項目列を作る
- 同名フォルダは1つにまとめる（その名前を持つ全ソースを sources に集約）
- ファイルは全件表示（同名でもソースごとに別エントリ）
- マージは常に「直下1階層のみ」。マージフォルダに入った時も、その同名フォルダ群の
  直下1階層だけを再びマージする（カラムを1段進むごとに1階層マージ。深い再帰の
  フラット化はしない）

ビュー側はこの結果を使って「マージノード」をカラム表示する。各マージフォルダは
構成元の実パス群(sources)を保持し、入ると merge_children(sources) を呼んで
その階層だけをマージする。
"""

import os
from typing import List, Tuple


# 1つのマージフォルダ = (表示名, [構成元の実フォルダパス...])
MergedFolder = Tuple[str, List[str]]


def merge_children(source_dirs: List[str]) -> Tuple[List[MergedFolder], List[str]]:
    """複数ソースフォルダの子をマージする。

    Returns:
        (merged_folders, files)
        merged_folders: [(name, [そのnameを持つ各ソースの実フォルダパス...]), ...] 名前昇順
        files:          [実ファイルパス, ...] ベース名の昇順（全ソースの全ファイル）
    """
    folder_map = {}      # lower-name -> [display_name, [paths...]]
    files: List[str] = []
    for d in source_dirs or []:
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                is_dir = e.is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                key = e.name.lower()
                if key not in folder_map:
                    folder_map[key] = [e.name, []]
                folder_map[key][1].append(e.path)
            else:
                files.append(e.path)
    merged_folders = [
        (folder_map[k][0], folder_map[k][1])
        for k in sorted(folder_map.keys())
    ]
    files.sort(key=lambda p: os.path.basename(p).lower())
    return merged_folders, files


def flatten_files(source_dirs: List[str], max_files: int = 5000,
                  time_budget_sec: float = 2.0) -> List[str]:
    """選択フォルダ群以下の «全階層の全ファイル» を再帰収集（平坦・ベース名昇順）。
    「全ファイル平坦表示」モード用。フォルダは返さずファイルのみ。

    安全弁: プロジェクト直下など巨大ツリーを選択した場合に UI が
    フリーズしないよう、件数(max_files)と走査時間(time_budget_sec)で
    打ち切る。0/None を渡すと無制限。"""
    import time as _time
    deadline = (_time.monotonic() + time_budget_sec) if time_budget_sec else None
    out: List[str] = []
    stack = list(source_dirs or [])
    seen = set()
    while stack:
        if max_files and len(out) >= max_files:
            break
        if deadline is not None and _time.monotonic() > deadline:
            break
        d = stack.pop()
        nd = os.path.normcase(os.path.normpath(d))
        if nd in seen:
            continue
        seen.add(nd)
        try:
            for e in os.scandir(d):
                try:
                    if e.is_dir():
                        stack.append(e.path)
                    else:
                        out.append(e.path)
                except OSError:
                    pass
                if max_files and len(out) >= max_files:
                    break
        except OSError:
            pass
    out.sort(key=lambda p: os.path.basename(p).lower())
    return out
