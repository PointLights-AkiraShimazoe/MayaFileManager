"""
Path guard — 非同期パスプローブ＆ドライブ隔離 (N-1)
====================================================

Maya標準ファイルダイアログ最大の不満である
「切断済み/不調ドライブの同期ポーリングによるUIフリーズ」への対策。

設計原則:
- UIスレッドでは絶対に os.path.exists() / os.listdir() を
  未確認のネットワークパスに対して呼ばない
- 応答しないパスはタイムアウトで「到達不能」として隔離し、UIは止めない
- スレッドはすべて daemon（ハングしてもアプリ終了を妨げない）

Usage:
    prober = PathProber(parent)
    prober.probed.connect(on_result)   # (path: str, reachable: bool)
    prober.probe("Z:/projects")

    scanner = DriveScanner(parent)
    scanner.drives_ready.connect(on_drives)  # list[tuple[str, bool]]
    scanner.scan()
"""

import os
import time
import platform
import threading

from core.compat import QObject, Signal

# パスごとのプローブ結果キャッシュ（再訪時の即応用）
_CACHE_TTL = 30.0  # 秒
_cache = {}        # path -> (timestamp, reachable)
_cache_lock = threading.Lock()


def _check_exists_with_timeout(path: str, timeout: float) -> bool:
    """
    os.path.exists() を別daemonスレッドで実行し、timeout秒で見切る。
    ハングしたスレッドは放置される（daemonなので終了時に回収）。
    """
    result = {"ok": False, "done": False}

    def _check():
        try:
            result["ok"] = os.path.exists(path)
        except OSError:
            result["ok"] = False
        result["done"] = True

    t = threading.Thread(target=_check, daemon=True,
                         name=f"mfm-probe:{path[:32]}")
    t.start()
    t.join(timeout)
    return result["ok"] if result["done"] else False


def probe_cached(path: str, timeout: float = 2.0) -> bool:
    """同期版プローブ（ワーカースレッド内から使う想定）。TTLキャッシュ付き。"""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(path)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    ok = _check_exists_with_timeout(path, timeout)
    with _cache_lock:
        _cache[path] = (now, ok)
    return ok


def invalidate_cache(path: str = None):
    """キャッシュ破棄（再スキャン時）。path=None で全消去。"""
    with _cache_lock:
        if path is None:
            _cache.clear()
        else:
            _cache.pop(path, None)


class PathProber(QObject):
    """単一パスの到達可能性を非同期に確認する。"""

    probed = Signal(str, bool)  # (path, reachable)

    def probe(self, path: str, timeout: float = 2.0):
        def _run():
            ok = probe_cached(path, timeout)
            self.probed.emit(path, ok)  # クロススレッドemitはqueued接続で安全

        threading.Thread(target=_run, daemon=True,
                         name="mfm-path-prober").start()


class DriveScanner(QObject):
    """
    全ドライブレターを並列プローブし、(root, reachable) のリストを返す。
    - exists()が即Falseを返したレター → 存在しないので一覧に含めない
    - timeout内に応答しなかったレター → マッピングは存在するが不調と
      みなし (root, False) として返す（UI側でグレーアウト表示する）
    """

    drives_ready = Signal(list)  # list[tuple[str, bool]]

    def scan(self, timeout: float = 1.5):
        def _run():
            if platform.system() != "Windows":
                self.drives_ready.emit([("/", True)])
                return

            import string
            letters = [f"{c}:\\" for c in string.ascii_uppercase]
            results = {}

            def _check(letter):
                try:
                    results[letter] = os.path.exists(letter)
                except OSError:
                    results[letter] = False

            threads = []
            for letter in letters:
                t = threading.Thread(target=_check, args=(letter,),
                                     daemon=True, name=f"mfm-drive:{letter[0]}")
                t.start()
                threads.append((letter, t))

            deadline = time.monotonic() + timeout
            out = []
            for letter, t in threads:
                t.join(max(0.0, deadline - time.monotonic()))
                if letter in results:
                    if results[letter]:
                        out.append((letter, True))
                    # 即False = レター未使用 → 一覧に含めない
                else:
                    # タイムアウト = マッピングはあるが応答なし
                    out.append((letter, False))

            self.drives_ready.emit(out)

        threading.Thread(target=_run, daemon=True,
                         name="mfm-drive-scanner").start()
