# -*- coding: utf-8 -*-
"""処理中のファイル／フォルダだけを «触れない» ようにする（r146）。

なぜ要るのか
------------
コピー・移動・削除の進捗ダイアログを非モーダルにした（ユーザー指示
2026-10-13「移動中やコピー中にマネージャーが止まらない様にしてください」）。
止まらないのは良いが、**走っている最中の対象を触れてしまう** のは別の話で、

    コピー中のフォルダをリネームする / 移動元を削除する /
    移動先へ別の移動をかぶせる

は、途中で失敗するか、もっと悪いと «中身が半分だけ» の結果を残す。

そこで «アプリ全体を止める» のではなく、**その操作が触っている場所だけ**
を予約する。予約と重なる操作だけ断り、それ以外（別フォルダを見る・別の
ファイルを開く・無関係な場所でのコピー）は今までどおり通す。

重なりの判定
------------
同じパスはもちろん、**上下関係にあるものも重なり** とみなす。

    予約: D:/work/shot_010            （コピー中のフォルダ）
    断る: D:/work/shot_010            （そのもの）
          D:/work/shot_010/ch/a.ma    （その中身）
          D:/work                     （その親。親を動かすと中身が迷子になる）

プロセス内だけの取り決めで、OS のロックではない。マネージャーが自分の
操作同士でぶつからないようにするためのもの。
"""
from core.diag import swallow as _swallow

import os
import threading
import uuid

_lock = threading.RLock()
_held = {}          # token -> (ラベル, [正規化したパス])


def _norm(p: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(p))).rstrip("\\/")
    except Exception:
        return ""


def _overlaps(a: str, b: str) -> bool:
    """a と b が «同じ／どちらかがどちらかの中» なら True。"""
    if not a or not b:
        return False
    if a == b:
        return True
    return a.startswith(b + os.sep) or b.startswith(a + os.sep)


def acquire(paths, label: str = "") -> str:
    """paths を予約して token を返す。必ず release() すること。"""
    norm = [n for n in (_norm(p) for p in (paths or [])) if n]
    token = uuid.uuid4().hex
    with _lock:
        _held[token] = (label or "", norm)
    return token


def release(token: str):
    if not token:
        return
    with _lock:
        _held.pop(token, None)


def release_all():
    """テスト用／終了時の後始末。"""
    with _lock:
        _held.clear()


def conflicts(paths):
    """paths のうち «今走っている操作» と重なるものを返す。

    戻り値 [(ぶつかったパス, 相手のラベル), ...]（空なら通してよい）。
    """
    out = []
    try:
        wanted = [(p, _norm(p)) for p in (paths or [])]
        with _lock:
            items = list(_held.values())
        for label, held in items:
            for raw, n in wanted:
                if not n:
                    continue
                for h in held:
                    if _overlaps(n, h):
                        out.append((raw, label))
                        break
    except Exception as _e:
        _swallow(_e, "core/file_locks.py conflicts")
    return out


def is_busy(path: str) -> bool:
    return bool(conflicts([path]))


def busy_paths():
    """今予約されている全パス（表示用）。"""
    with _lock:
        out = []
        for _label, held in _held.values():
            out.extend(held)
    return out
