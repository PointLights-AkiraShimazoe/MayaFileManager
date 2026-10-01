# -*- coding: utf-8 -*-
"""握り潰しを «安全なまま» 可視化する（r112）。

背景
----
本ツールには `except Exception: pass` が 249 箇所ある。UI の描画や破棄済み
ウィジェットへの操作など、**失敗しても実害が無く、落とす方が有害**な箇所が
多いため、これ自体は妥当な設計だった。

一方で、この握り潰しは **実装ミスまで隠してしまう**。実際 2026-09〜10 に
だけでも、D&D が一切動かない／連携バッジが更新されない／ツールチップが
出ない、の 3 件がここに隠れていた（いずれも NameError・AttributeError）。

方針
----
例外の «型» で選り分ける。

  想定内（黙殺）  : OSError, RuntimeError（破棄済み C++ オブジェクト等）,
                    ValueError, KeyboardInterrupt 以外の環境起因
  実装ミス（記録）: NameError, AttributeError, TypeError, ImportError,
                    IndexError, KeyError, UnboundLocalError

記録は `~/mfm_debug.log` へ 1 行。**MFM_DEBUG の有無によらず常時記録**する
（頻度が低く、かつ «出たら必ずバグ» のため）。同じ場所は 1 回だけ出す。
"""

import os
import datetime

# 実装ミスを強く示唆する例外型（＝握り潰してはいけない）
BUG_TYPES = (
    NameError,          # 未定義参照（モジュール分割の取りこぼし等）
    AttributeError,     # API の取り違え・綴り間違い
    TypeError,          # 引数の数・型の不一致
    ImportError,        # import 経路の誤り
    IndexError,
    KeyError,
    UnboundLocalError,
)

_LOG_PATH = os.path.join(os.path.expanduser("~"), "mfm_debug.log")
_seen = set()


def swallow(exc, where=""):
    """握り潰しの共通処理。**絶対に例外を投げない**。

    exc   : 捕まえた例外
    where : 発生場所（"ui/browser_panel.py:1234 _on_item_clicked" 等）
    """
    try:
        if not isinstance(exc, BUG_TYPES):
            return                      # 想定内 → 従来どおり黙殺
        if where in _seen:
            return                      # 同じ場所は 1 回だけ
        _seen.add(where)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] BUG?: %s で %s: %s\n"
                    % (datetime.datetime.now().strftime("%H:%M:%S"),
                       where or "(場所不明)", type(exc).__name__, exc))
    except Exception:
        pass                            # ログ自体の失敗は本当に無視してよい


def swallowed_count() -> int:
    """記録済みの箇所数（テスト・自己診断用）。"""
    return len(_seen)


def reset():
    """テスト用。"""
    _seen.clear()
