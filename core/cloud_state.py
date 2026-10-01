# -*- coding: utf-8 -*-
"""クラウドファイル（OneDrive / Dropbox / Google Drive の «オンラインのみ» 実体）
の判定（r83）。

なぜ必要か
----------
OneDrive 等の「オンラインのみ」ファイルは、**内容に触れた瞬間に実体が
ダウンロード（ハイドレート）される**。サムネイル生成やプレビューが
`open()` / `QPixmap(path)` を呼ぶと、フォルダを開いただけで数百 MB の
ダウンロードが始まり、UI も体感で固まる（ユーザー報告 2026-09-17）。

判定は Windows のファイル属性だけで行う。`os.lstat()` はディレクトリ
エントリのメタデータしか読まないので **ハイドレートを起こさない**。

    RECALL_ON_DATA_ACCESS / RECALL_ON_OPEN / OFFLINE → オンラインのみ
    PINNED                                          → 常にローカル保持
    それ以外                                        → ローカルにあり

ここが属性定数の «単一の真実»。core/integrations/cloud_provider.py も
これを import する（二重定義しない）。
"""

import os

# Windows のファイル属性（winnt.h）
ATTR_OFFLINE = 0x00001000
ATTR_RECALL_ON_OPEN = 0x00040000
ATTR_PINNED = 0x00080000
ATTR_UNPINNED = 0x00100000
ATTR_RECALL_ON_DATA = 0x00400000

# 「内容に触れるとダウンロードが走る」ことを示す属性の集合
ATTR_DEHYDRATED = ATTR_OFFLINE | ATTR_RECALL_ON_OPEN | ATTR_RECALL_ON_DATA


def file_attributes(path: str) -> int:
    """ファイル属性を返す（取得できなければ 0）。lstat のみでI/Oは最小。"""
    if os.name != "nt":
        return 0
    try:
        return getattr(os.lstat(path), "st_file_attributes", 0) or 0
    except OSError:
        return 0


def local_bytes(path: str) -> int:
    """**実体がローカルに何バイトあるか**。取得できなければ -1（r99）。

    `GetCompressedFileSizeW` はファイルを開かないので **ハイドレートを
    起こさない**。オンラインのみのプレースホルダは 0 を返し、
    ダウンロード済みのファイルは実サイズ相当を返す。
    """
    if os.name != "nt":
        return -1
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetCompressedFileSizeW.restype = wintypes.DWORD
        high = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        low = kernel32.GetCompressedFileSizeW(path, ctypes.byref(high))
        if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:
            return -1
        return (int(high.value) << 32) | int(low)
    except Exception:
        return -1


def is_online_only(path: str) -> bool:
    """«オンラインのみ»（実体がローカルに無い）なら True。

    内容を読む処理（サムネイル生成・プレビュー・ハッシュ）は、これが True の
    間は **実行してはならない**。ユーザーが明示的に開く/インポートする等の
    操作をしたときだけダウンロードさせる。

    r99: **属性だけで判定してはいけない**。OneDrive の «ファイル オンデマンド»
    が有効なフォルダでは、ダウンロード済み（ローカルにある）ファイルにも
    RECALL_ON_DATA_ACCESS が付いたままのことがあり、属性だけ見ると
    «全部オンラインのみ» と誤判定してサムネイルが1枚も出なくなる
    （ユーザー報告 2026-09-24、OneDrive 配下の PNG が全滅）。
    実体サイズ（ハイドレートを起こさない GetCompressedFileSizeW）で裏を取る。
    """
    attrs = file_attributes(path)
    if not (attrs & ATTR_DEHYDRATED):
        return False
    if attrs & ATTR_OFFLINE:
        return True                     # 明示的にオフライン＝実体なし
    n = local_bytes(path)
    if n < 0:
        return True                     # 判定不能 → 安全側（DLさせない）
    return n == 0                       # 実体 0 バイト＝プレースホルダ


def filter_local(paths):
    """オンラインのみのファイルを除いたリストを返す（先読み用）。"""
    return [p for p in paths if not is_online_only(p)]
