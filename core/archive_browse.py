# -*- coding: utf-8 -*-
"""圧縮ファイルの中身を «そのまま» ブラウズするための読み取り層（r97）。

できること／できないことは **Windows 標準のエクスプローラーと同じ** 方針:

  できる   : 中身の一覧表示・階層のたどり込み・中のファイルを開く
             （テンポラリへ展開してから既定アプリ／DCC へ）・展開（取り出し）
             ・中のファイルを外へ D&D（＝展開してから実ファイルを渡す）
  できない : 書庫の中身の書き換え（リネーム／削除／上書き保存）
             パスワード付き書庫の中身の展開（一覧は出る／エクスプローラー同様）
             .rar / .7z（Windows 標準が扱えないため対象外）

対応形式: .zip と tar 系（.tar/.tar.gz/.tgz/.tar.bz2/.tbz2/.tar.xz/.txz）。
tar 系はエクスプローラーでは開けないが、読み取り専用で害が無いため追加で対応する。
"""
import os
import hashlib
import tarfile
import tempfile
import zipfile

# 拡張子（長いものから判定する。.tar.gz を .gz と誤判定しないため）
ZIP_EXTS = (".zip",)
TAR_EXTS = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".tar")
ALL_EXTS = ZIP_EXTS + TAR_EXTS

# 安全弁: 巨大書庫で UI が固まらないようにする
MAX_ENTRIES = 20000
MAX_EXTRACT_BYTES = 2 * 1024 * 1024 * 1024      # 1ファイル 2GB まで


class Entry(object):
    """書庫内の 1 エントリ（ディレクトリ含む）。name は «/» 区切りの相対パス。"""

    __slots__ = ("name", "is_dir", "size", "mtime", "encrypted")

    def __init__(self, name, is_dir=False, size=0, mtime=0.0, encrypted=False):
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.mtime = mtime
        self.encrypted = encrypted

    @property
    def base(self):
        return self.name.rstrip("/").rsplit("/", 1)[-1]


def archive_kind(path) -> str:
    """"zip" / "tar" / "" （書庫でない）を返す。中身は見ずに拡張子で判定する。"""
    low = (path or "").lower()
    for e in TAR_EXTS:
        if low.endswith(e):
            return "tar"
    for e in ZIP_EXTS:
        if low.endswith(e):
            return "zip"
    return ""


def is_archive(path) -> bool:
    """ブラウズ対象の書庫ファイルか（実在するファイルであること）。"""
    try:
        return bool(archive_kind(path)) and os.path.isfile(path)
    except Exception:
        return False


def _norm(name) -> str:
    """書庫内パスを «/» 区切り・先頭 ./ 無し に正規化する。"""
    n = (name or "").replace("\\", "/")
    while n.startswith("./"):
        n = n[2:]
    return n.lstrip("/")


def list_entries(path):
    """書庫の中身を Entry のリストで返す（ディレクトリは実体が無くても補完する）。

    壊れた書庫・未対応形式は例外を送出せず ValueError を投げる（呼び出し側で表示）。
    """
    kind = archive_kind(path)
    entries = []
    try:
        if kind == "zip":
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist()[:MAX_ENTRIES]:
                    name = _norm(info.filename)
                    if not name:
                        continue
                    entries.append(Entry(
                        name=name,
                        is_dir=info.is_dir(),
                        size=int(info.file_size),
                        mtime=_zip_mtime(info),
                        # 0x1 = 暗号化フラグ（エクスプローラー同様、一覧は出せる）
                        encrypted=bool(info.flag_bits & 0x1)))
        elif kind == "tar":
            with tarfile.open(path) as tf:
                for m in tf.getmembers()[:MAX_ENTRIES]:
                    name = _norm(m.name)
                    if not name:
                        continue
                    entries.append(Entry(name=name, is_dir=m.isdir(),
                                         size=int(m.size), mtime=float(m.mtime)))
        else:
            raise ValueError("未対応の形式です: %s" % os.path.basename(path or ""))
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("書庫を読めません: %s" % (e,))
    return _with_implied_dirs(entries)


def _zip_mtime(info):
    try:
        import calendar
        return float(calendar.timegm(tuple(info.date_time) + (0, 0, -1)))
    except Exception:
        return 0.0


def _with_implied_dirs(entries):
    """"a/b/c.ma" しか無い書庫でも "a" と "a/b" を補って階層を作る。"""
    have = {e.name.rstrip("/") for e in entries}
    extra = []
    for e in list(entries):
        parts = e.name.rstrip("/").split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            if d and d not in have:
                have.add(d)
                extra.append(Entry(name=d + "/", is_dir=True))
    return entries + extra


def children(entries, inner_dir=""):
    """inner_dir（"" = 書庫ルート）の直下だけを返す。フォルダ先・名前順。"""
    base = _norm(inner_dir).rstrip("/")
    prefix = (base + "/") if base else ""
    out, seen = [], set()
    for e in entries:
        n = e.name.rstrip("/")
        if not n.startswith(prefix) or n == base:
            continue
        rest = n[len(prefix):]
        if not rest or "/" in rest:
            continue
        if rest in seen:
            continue
        seen.add(rest)
        out.append(e)
    out.sort(key=lambda x: (not x.is_dir, x.base.lower()))
    return out


def has_encrypted(entries) -> bool:
    return any(getattr(e, "encrypted", False) for e in entries)


# ---------------------------------------------------------------------------
# 取り出し（展開）
# ---------------------------------------------------------------------------

def _cache_root(archive_path) -> str:
    """書庫ごとのテンポラリ展開先。同じ書庫は同じ場所を使い回す。"""
    try:
        st = os.stat(archive_path)
        key = "%s|%s|%s" % (os.path.abspath(archive_path), st.st_size, int(st.st_mtime))
    except Exception:
        key = os.path.abspath(archive_path or "")
    h = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]
    root = os.path.join(tempfile.gettempdir(), "mfm_archive", h)
    os.makedirs(root, exist_ok=True)
    return root


def _safe_join(dest, name) -> str:
    """Zip Slip 対策: 展開先の外に出るパスを拒否する。"""
    target = os.path.normpath(os.path.join(dest, *_norm(name).split("/")))
    root = os.path.normpath(dest)
    if os.path.normcase(target) != os.path.normcase(root) and \
            not os.path.normcase(target).startswith(os.path.normcase(root) + os.sep):
        raise ValueError("書庫内に不正なパスが含まれています: %s" % name)
    return target


def extract_members(archive_path, names, dest):
    """names（書庫内パス）を dest へ展開し、作成された実パスのリストを返す。
    ディレクトリを指定した場合はその配下をまとめて展開する。"""
    kind = archive_kind(archive_path)
    wanted = [_norm(n) for n in (names or [])]
    made = []
    os.makedirs(dest, exist_ok=True)

    def _want(n):
        if not wanted:
            return True
        for w in wanted:
            ww = w.rstrip("/")
            if n == ww or n.startswith(ww + "/"):
                return True
        return False

    if kind == "zip":
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                n = _norm(info.filename).rstrip("/")
                if not n or not _want(n):
                    continue
                target = _safe_join(dest, n)
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                if info.file_size > MAX_EXTRACT_BYTES:
                    raise ValueError("大きすぎて展開できません: %s" % n)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 256)
                        if not chunk:
                            break
                        dst.write(chunk)
                made.append(target)
    elif kind == "tar":
        with tarfile.open(archive_path) as tf:
            for m in tf.getmembers():
                n = _norm(m.name).rstrip("/")
                if not n or not _want(n):
                    continue
                target = _safe_join(dest, n)
                if m.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                if not m.isfile():
                    continue        # シンボリックリンク等は展開しない（安全側）
                os.makedirs(os.path.dirname(target), exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    continue
                with src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 256)
                        if not chunk:
                            break
                        dst.write(chunk)
                made.append(target)
    else:
        raise ValueError("未対応の形式です")
    return made


def extract_to_temp(archive_path, names):
    """開く／D&D 用にテンポラリへ展開し、実パスのリストを返す。
    同じ書庫の同じファイルは 2 度展開しない（更新日時が変われば別扱い）。"""
    root = _cache_root(archive_path)
    todo, ready = [], []
    for n in (names or []):
        try:
            p = _safe_join(root, n)
        except ValueError:
            continue
        if os.path.isfile(p):
            ready.append(p)
        else:
            todo.append(n)
    if todo:
        ready.extend(extract_members(archive_path, todo, root))
    # 順序は要求順を保つ
    out, seen = [], set()
    for n in (names or []):
        try:
            p = _safe_join(root, n)
        except ValueError:
            continue
        k = os.path.normcase(p)
        if k in seen:
            continue
        seen.add(k)
        if os.path.exists(p):
            out.append(p)
    return out or ready
