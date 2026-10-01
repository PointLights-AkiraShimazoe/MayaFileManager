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
      "title": "衣装バリエーション",
      "names": { "CH002_cur": "キャラA 最新", "old_bk": "" }
    }

`names` の値が空文字のエントリは «表示名なし»（実体名をそのまま表示）。
`title` はカラム下部のバーへ左揃えで出す見出し（空なら出さない）。

Windows の落とし穴（r119）
--------------------------
このファイルには **隠し属性** を付ける。隠し属性の付いた既存ファイルを
`open(path, "w")` で開くと、Windows は **アクセス拒否（WinError 5）** を返す
（CREATE_ALWAYS が属性不一致で弾かれる）。そのため 2 回目以降の保存が
«黙って失敗» し、「有効／無効スイッチを押しても何も起きない」になっていた。
書く前に必ず隠し属性を外し、書いた後で付け直すこと。
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
                "title": str(raw.get("title", "") or "").strip(),
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


def title(directory: str) -> str:
    """カラム見出し。未設定なら空文字（＝見出しを出さない）。

    有効／無効スイッチの影響は受けない（見出しは «どのフォルダか» の目印で、
    表示名の置換とは役割が違うため）。"""
    data = load(directory)
    return (data or {}).get("title", "") or ""


def set_enabled(directory: str, on: bool) -> bool:
    """有効／無効スイッチ。ファイルが無ければ何もしない。"""
    data = load(directory)
    if data is None:
        return False
    return save(directory, data.get("names") or {}, enabled=bool(on),
                title=data.get("title", ""))


def save(directory: str, names: dict, enabled: bool = True,
         title: str = "") -> bool:
    """表示名を書き込む。

    **表示名も見出しも空なら書かずに削除**する（余計なファイルを残さない）。
    見出しだけ設定したい場合もあるので、判定は «両方空» のときだけ。
    """
    clean = {str(a): str(b).strip() for a, b in (names or {}).items()
             if str(b).strip()}
    ttl = str(title or "").strip()
    p = _path_for(directory)
    if not clean and not ttl:
        return remove(directory)
    try:
        # r119: 隠し属性の付いた既存ファイルは "w" で開けない（WinError 5）。
        # 先に外してから書き、最後に付け直す。
        _set_hidden(p, False)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"enabled": bool(enabled), "title": ttl,
                       "names": clean}, f, ensure_ascii=False, indent=2)
        _hide(p)
        _cache.pop(_key(directory), None)
        return True
    except Exception as _e:
        _swallow(_e, "core/display_names.py save %s" % p)
        _hide(p)            # 失敗しても隠し属性は戻す
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


_FILE_ATTRIBUTE_HIDDEN = 0x02
_FILE_ATTRIBUTE_NORMAL = 0x80
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def _set_hidden(path: str, on: bool):
    """Windows の隠し属性を付け外しする。

    **外す方も必要**: 隠し属性の付いた既存ファイルは `open(path, "w")` が
    アクセス拒否になるため、保存のたびに «外す→書く→付ける» をする。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        cur = k32.GetFileAttributesW(str(path))
        if cur == _INVALID_FILE_ATTRIBUTES:
            return                      # まだ無い＝何もしなくてよい
        new = (cur | _FILE_ATTRIBUTE_HIDDEN) if on \
            else (cur & ~_FILE_ATTRIBUTE_HIDDEN)
        if new == 0:
            new = _FILE_ATTRIBUTE_NORMAL
        if new != cur:
            k32.SetFileAttributesW(str(path), new)
    except Exception as _e:
        _swallow(_e, "core/display_names.py _set_hidden")


def _hide(path: str):
    """Windows で隠し属性を付ける（先頭ドットだけでは隠れないため）。"""
    _set_hidden(path, True)


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
