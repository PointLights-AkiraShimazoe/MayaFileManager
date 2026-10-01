# -*- coding: utf-8 -*-
"""表示名（実体名とは別に、カラムへ表示する名前）（r115）。

設計の約束
----------
* **パスに関わる処理は必ず実体名を使う。** 表示名は «見た目だけ» を差し替える。
  コピー・移動・パスのコピー・DCC への受け渡しは一切影響を受けない。
* 保存先は対象ディレクトリ直下の隠しファイル `.mfm_display_names.json`。
  そのフォルダと一緒に移動・コピーされるので、設定が離れない。
* **ファイルが無ければ機能自体を動かさない。** 存在判定の結果はキャッシュし、
  カラムを描くたびに stat が走らないようにする（負荷軽減）。

ファイル形式::

    {
      "enabled": true,
      "names": { "CH002_cur": "キャラA 最新", "old_bk": "" }
    }

`names` の値が空文字のエントリは «表示名なし»（実体名をそのまま表示）。
"""

import json
import os

from core.diag import swallow as _swallow

FILE_NAME = ".mfm_display_names.json"

# dir(normcase) -> (mtime, size, data or None)
_cache = {}


def _path_for(directory: str) -> str:
    return os.path.join(directory, FILE_NAME)


def _key(directory: str) -> str:
    return os.path.normcase(os.path.abspath(directory or ""))


def _stat_sig(path: str):
    """(mtime, size)。無ければ None。**ここ以外で stat しない。**"""
    try:
        st = os.stat(path)
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


def load(directory: str):
    """そのディレクトリの表示名データ。**無ければ None**（機能オフ）。

    返すのは {"enabled": bool, "names": {実体名: 表示名}}。
    """
    if not directory:
        return None
    k = _key(directory)
    p = _path_for(directory)
    sig = _stat_sig(p)
    cached = _cache.get(k)
    if cached is not None and cached[0] == sig:
        return cached[1]
    if sig is None:
        _cache[k] = (None, None)
        return None
    data = None
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            names = raw.get("names")
            data = {
                "enabled": bool(raw.get("enabled", True)),
                "names": {str(a): str(b) for a, b in names.items()}
                if isinstance(names, dict) else {},
            }
    except Exception as _e:
        _swallow(_e, "core/display_names.py load %s" % p)
        data = None
    _cache[k] = (sig, data)
    return data


def has_file(directory: str) -> bool:
    """隠しファイルがあるか（キャッシュ利用。無ければ以後ほぼ無コスト）。"""
    return load(directory) is not None


def alias_map(directory: str):
    """**表示に使うべき** 対応表。無効・未設定なら None（呼び出し側は素通し）。"""
    data = load(directory)
    if not data or not data.get("enabled"):
        return None
    names = {a: b for a, b in data.get("names", {}).items() if b}
    return names or None


def display_for(directory: str, real_name: str) -> str:
    """実体名 → 表示名。設定が無ければ実体名をそのまま返す。"""
    m = alias_map(directory)
    if not m:
        return real_name
    return m.get(real_name) or real_name


def is_enabled(directory: str) -> bool:
    data = load(directory)
    return bool(data and data.get("enabled"))


def set_enabled(directory: str, on: bool) -> bool:
    """有効／無効スイッチ。ファイルが無ければ何もしない。"""
    data = load(directory)
    if data is None:
        return False
    data["enabled"] = bool(on)
    return save(directory, data["names"], enabled=bool(on))


def save(directory: str, names: dict, enabled: bool = True) -> bool:
    """表示名を書き込む。**中身が空なら書かずに削除**する（余計なファイルを残さない）。"""
    clean = {str(a): str(b).strip() for a, b in (names or {}).items()
             if str(b).strip()}
    p = _path_for(directory)
    if not clean:
        return remove(directory)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"enabled": bool(enabled), "names": clean},
                      f, ensure_ascii=False, indent=2)
        _hide(p)
        _cache.pop(_key(directory), None)
        return True
    except Exception as _e:
        _swallow(_e, "core/display_names.py save %s" % p)
        return False


def remove(directory: str) -> bool:
    """隠しファイルを削除する（表示名機能をそのフォルダで完全に止める）。"""
    p = _path_for(directory)
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError as _e:
        _swallow(_e, "core/display_names.py remove %s" % p)
        return False
    finally:
        _cache.pop(_key(directory), None)
    return True


def invalidate(directory: str = None):
    """キャッシュを捨てる。directory 省略で全部。"""
    if directory is None:
        _cache.clear()
    else:
        _cache.pop(_key(directory), None)


def _hide(path: str):
    """Windows で隠し属性を付ける（先頭ドットだけでは隠れないため）。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path),
                                                  FILE_ATTRIBUTE_HIDDEN)
    except Exception as _e:
        _swallow(_e, "core/display_names.py _hide")


def subdirectories(directory: str):
    """表示名を付けられる対象（直下のディレクトリ）を名前順で返す。"""
    out = []
    try:
        with os.scandir(directory) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                        out.append(e.name)
                except OSError:
                    continue
    except OSError as _e:
        _swallow(_e, "core/display_names.py subdirectories")
    out.sort(key=lambda s: s.lower())
    return out
