# -*- coding: utf-8 -*-
"""ログの置き場所（r146、2026-11-12）。

置き場所は従来どおり «ユーザーフォルダの下»（~/.maya_file_manager/logs）。
ツールを置いたドライブが応答しなくなっても記録が残るように、監視対象とは
別の場所へ書く（r117）。

これまで同じパスの組み立てが 4 か所（連携・Maya 連携・Maya の起動・UI）に
別々に書かれていた。ここへ 1 つにまとめる。**作れなかった時にどこへ退避
するかは、これまでどおり呼び出し側がそれぞれ決める**（ここは OSError を
そのまま返すだけ）。

MFM_LOG_DIR: 回帰テスト用の差し替え先。テストが実機のログへ書き込むと、
起動の記録（mfm_startup.log）が上書きされ、連携・起動のログに試験用の
行が混ざって、実機の調査の邪魔になる（2026-11-12 に実機のログで確認）。
テストは一時フォルダを指す。普段は未設定で、動きは変わらない。
"""
import os


def default_log_dir() -> str:
    """既定の置き場所（差し替えを見ない）。作りはしない。"""
    return os.path.join(os.path.expanduser("~"), ".maya_file_manager", "logs")


def user_log_dir() -> str:
    """ログの置き場所を作って返す。作れなければ OSError。"""
    d = os.environ.get("MFM_LOG_DIR") or default_log_dir()
    os.makedirs(d, exist_ok=True)
    return d
