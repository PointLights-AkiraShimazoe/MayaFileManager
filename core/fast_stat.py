# -*- coding: utf-8 -*-
"""UI スレッドを止めない stat（r146）。

実機 2026-10-16（島添）: 外付け SSD へのジャンクション配下（C:\\...\\MM-SA →
A:）で、os.path.isdir() 1 回が 10〜20 秒かかることがあり、右クリック・
削除前の手放し・見張りの付け直し・ナビゲーションのたびに UI が固まった
（mfm_freeze.log: _release_fs_handles_for / _rearm_fs_watch_now /
_popup_context_menu の os.path.isdir で停止）。

方針: **UI スレッドでは待たない。**
  * 答えは裏のスレッドで取り、短い猶予（WAIT_SEC）だけ待つ
  * 間に合わなければ «それらしい仮の答え»（拡張子の有無など）を返し、
    本当の答えは後から効く（次の呼び出しから）
  * 同じパスの問い合わせは 1 本に束ねる（遅いボリュームで溜まらない）
  * 自分で動かした・消したパスは invalidate() で捨てる

正確さが要る所（名前変更の衝突判定・新規作成の空き名）では使わない。
MFM_DIAG=no_fast_stat で素の os.path に戻せる（切り分け用）。
"""
import os
import threading
import time

from core.diag import diag_off as _diag_off

WAIT_SEC = 0.05          # UI スレッドが待つ上限
TTL_SEC = 2.0            # 答えを信じる時間（その後は裏で取り直す）
MAX_WORKERS = 4

_lock = threading.Lock()
_cache = {}              # key -> (kind, monotonic)
_inflight = {}           # key -> threading.Event
_queue = []              # [(key, path)]
_cv = threading.Condition(_lock)
_workers = []

# kind: "dir" / "file" / "link" / "missing"


def _key(path):
    try:
        return os.path.normcase(os.path.normpath(path))
    except Exception:
        return path


def _stat_kind(path):
    try:
        if os.path.islink(path):
            return "link"
    except OSError:
        pass
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    import stat as _st
    if _st.S_ISDIR(st.st_mode):
        return "dir"
    return "file"


def _worker():
    while True:
        with _cv:
            while not _queue:
                _cv.wait()
            key, path = _queue.pop(0)
        kind = _stat_kind(path)
        with _cv:
            _cache[key] = (kind, time.monotonic())
            ev = _inflight.pop(key, None)
            if len(_cache) > 50000:
                _cache.clear()
        if ev is not None:
            ev.set()


def _ensure_workers():
    if _workers:
        return
    with _lock:
        if _workers:
            return
        for i in range(MAX_WORKERS):
            t = threading.Thread(target=_worker, daemon=True,
                                 name="mfm-fast-stat-%d" % i)
            t.start()
            _workers.append(t)


def kind(path, wait=WAIT_SEC):
    """"dir"/"file"/"link"/"missing"。猶予内に取れなければ None（仮の答えは呼ぶ側）。"""
    if not path:
        return "missing"
    if _diag_off("no_fast_stat"):
        return _stat_kind(path)
    _ensure_workers()
    key = _key(path)
    now = time.monotonic()
    with _cv:
        hit = _cache.get(key)
        fresh = hit is not None and (now - hit[1]) < TTL_SEC
        if not fresh:
            ev = _inflight.get(key)
            if ev is None:
                ev = threading.Event()
                ev.started = now
                _inflight[key] = ev
                _queue.append((key, path))
                _cv.notify()
            elif now - getattr(ev, "started", now) > wait:
                # 既に猶予を使い切った問い合わせ。同じパスを連打されても
                # もう待たない（20 件選択で 20 回待つ、を避ける）
                wait = 0.0
        else:
            ev = None
    if fresh:
        return hit[0]
    if ev.wait(wait):
        with _cv:
            hit = _cache.get(key)
        return hit[0] if hit else None
    # 間に合わなかった。古い答えがあればそれ、無ければ None
    return hit[0] if hit else None


def _guess_dir(path):
    try:
        return not os.path.splitext(path.rstrip("/\\"))[1]
    except Exception:
        return False


def isdir(path, wait=WAIT_SEC):
    k = kind(path, wait)
    if k is None:
        return _guess_dir(path)
    return k == "dir"


def isfile(path, wait=WAIT_SEC):
    k = kind(path, wait)
    if k is None:
        return not _guess_dir(path)
    return k == "file"


def islink(path, wait=WAIT_SEC):
    k = kind(path, wait)
    return k == "link"


def exists(path, wait=WAIT_SEC):
    k = kind(path, wait)
    if k is None:
        return True              # 表示されていた物は «ある» と見なす
    return k != "missing"


def invalidate(*paths):
    """自分で動かした／消したパスの答えを捨てる（親フォルダも）。"""
    with _cv:
        for p in paths:
            if not p:
                continue
            k = _key(p)
            _cache.pop(k, None)
            try:
                _cache.pop(_key(os.path.dirname(p.rstrip("/\\"))), None)
            except Exception:
                pass


def clear():
    with _cv:
        _cache.clear()
