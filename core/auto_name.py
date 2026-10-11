# -*- coding: utf-8 -*-
"""自動命名（階層型）— r140。

ユーザー指示 2026-10-08（例）::

    D:/Work/SampleProject/A01/02/n003/
        SampleProject_A01_02_003.ma  ← 拡張子は保存時に付く

    ルートディレクトリ : D:/Work/SampleProject/
    対象階層           : {Group}/{Set}/n{Item}/
    ファイル名         : {Group}_{Set}_{Item}_{ver}   ← 拡張子は書かない

設計の芯:

* **変数は固定で持たない。** 対象階層に書いた ``{...}`` がそのまま変数名になり、
  実フォルダ名から値を拾う。ファイル名側はその変数を参照するだけ。
* **「ルート以下ならどこでも」ではない。** 対象階層と *同じ深さ* で、各段が
  パターンに合うフォルダだけが対象（``n{Item}`` の ``n`` のような固定文字も見る）。
* ``{ver}`` だけは **固定の特別な変数**。最新表記・接頭辞・桁数・
  インクリメンタルセーブの有無を設定できる。

バージョンの決め方:

* インクリメンタルセーブ **ON**
    保存名の ``{ver}`` は最新表記（既定 ``cur``）。保存の直前に、既にある
    最新ファイルを ``<接頭辞><次の番号>`` へ複製して退避する。
    *次の番号* は既にある番号の最大 +1（既存を上書きしない）。
* インクリメンタルセーブ **OFF**
    パターンに一致するファイルを探し、番号の最大 +1 を使う。
    一致が無ければ 1。この時は最新表記は関係ない。
"""
from core.diag import swallow as _swallow

import os
import re
import shutil

VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z_0-9]*)\}")

DEFAULT_VER = {
    "latest_label": "cur",      # 最新バージョンの表記
    "prefix": "v",              # バージョンのプレフィックス表記
    "digits": 3,                # バージョンの桁数
    "incremental": False,       # インクリメンタルセーブ
}


def ver_settings(rule) -> dict:
    v = dict(DEFAULT_VER)
    try:
        v.update({k: val for k, val in (rule or {}).get("ver", {}).items()
                  if val is not None})
    except Exception as _e:
        _swallow(_e, "core/auto_name.py ver_settings")
    try:
        v["digits"] = max(1, min(12, int(v["digits"])))
    except (TypeError, ValueError):
        v["digits"] = DEFAULT_VER["digits"]
    v["incremental"] = bool(v["incremental"])
    for k in ("latest_label", "prefix"):
        v[k] = str(v.get(k) or DEFAULT_VER[k])
    return v


def is_hier_rule(rule) -> bool:
    """r140 の階層型ルールか（旧い連番型と見分ける）。"""
    return bool(isinstance(rule, dict) and rule.get("levels")
                and rule.get("filename"))


def level_vars(levels: str):
    """対象階層に書かれた変数名を、出てくる順に返す。"""
    return VAR_RE.findall(levels or "")


def _split_levels(levels: str):
    return [s for s in re.split(r"[\\/]+", (levels or "").strip()) if s]


def _segment_regex(seg: str) -> str:
    """対象階層の 1 段をパターンへ。``n{Item}`` → ``n(?P<Item>...)``。"""
    out, last = [], 0
    names = []
    for m in VAR_RE.finditer(seg):
        out.append(re.escape(seg[last:m.start()]))
        names.append(m.group(1))
        out.append("(?P<%s>.+)" % m.group(1))
        last = m.end()
    out.append(re.escape(seg[last:]))
    body = "".join(out)
    # 1 段に変数が 2 つ以上あると貪欲同士がぶつかるので、最後以外は控えめに
    if len(names) > 1:
        body = body.replace("(?P<%s>.+)" % names[-1], "\x00", 1)
        body = body.replace(">.+)", ">.+?)")
        body = body.replace("\x00", "(?P<%s>.+)" % names[-1], 1)
    return body


def match_levels(root: str, directory: str, levels: str):
    """directory が「root + 対象階層」に *ぴったり* 合うなら変数の辞書を返す。

    合わなければ None。深さが違うだけでも None（ルート以下ならどこでも、
    ではない。ユーザー指示 2026-10-08）。
    """
    if not root or not directory or not levels:
        return None
    try:
        r = os.path.normcase(os.path.normpath(root))
        d = os.path.normcase(os.path.normpath(directory))
        if d == r:
            rel_parts = []
        else:
            base = r if r.endswith(os.sep) else r + os.sep
            if not d.startswith(base):
                return None
            rel_parts = [p for p in re.split(r"[\\/]+", d[len(base):]) if p]
    except Exception as _e:
        _swallow(_e, "core/auto_name.py match_levels")
        return None
    segs = _split_levels(levels)
    if len(rel_parts) != len(segs):
        return None
    # 値は «実際のフォルダ名» で拾いたいので、正規化前の directory から取り直す
    real_parts = [p for p in re.split(r"[\\/]+",
                                      os.path.normpath(directory))][-len(segs):] \
        if segs else []
    out = {}
    for seg, real in zip(segs, real_parts):
        m = re.match("^" + _segment_regex(seg) + "$", real, re.IGNORECASE)
        if not m:
            return None
        out.update(m.groupdict())
    return out


def expand(template: str, values: dict) -> str:
    """``{Name}`` を values で差し替える。未定義はそのまま残す。"""
    def sub(m):
        k = m.group(1)
        return str(values[k]) if k in values else m.group(0)
    return VAR_RE.sub(sub, template or "")


def missing_vars(rule) -> list:
    """ファイル名に書かれているのに対象階層で定義されていない変数。"""
    known = set(level_vars(rule.get("levels", ""))) | {"ver"}
    return [v for v in VAR_RE.findall(stem_template(rule))
            if v not in known]


def stem_template(rule) -> str:
    """ファイル名テンプレート（拡張子なし）。

    r145（ユーザー指示 2026-10-09）:
      「ファイル名ですが、拡張子は設定には入れず、保存時には選択されている
        拡張子を付けてください。」
    設定に ``.ma`` 等が残っていても、ここで落として «拡張子なし» に揃える。
    古い設定をそのまま読んでも壊れないようにするための受け皿でもある。
    """
    name = (rule or {}).get("filename", "") if isinstance(rule, dict) else ""
    stem, ext = os.path.splitext(name or "")
    # ".{ver}" のように «拡張子の位置に変数» が来ている場合は落とさない
    if ext and not VAR_RE.search(ext):
        return stem
    return name or ""


def _file_regex(rule, values):
    """その場所の «ver 以外を確定させた» ファイル名パターン。

    拡張子は設定に持たないので、**どの拡張子でも一致させる**。
    同じ名前で .ma と .mb が並んでいても、どちらもそのバージョンとして数える。
    """
    name = expand(stem_template(rule), values)
    out, last = [], 0
    for m in VAR_RE.finditer(name):
        if m.group(1) != "ver":
            continue
        out.append(re.escape(name[last:m.start()]))
        out.append("(?P<ver>.+?)")
        last = m.end()
    out.append(re.escape(name[last:]))
    return re.compile("^" + "".join(out) + r"(?P<ext>\.[^.]+)?$", re.IGNORECASE)


def scan_versions(directory: str, rule, values):
    """そのフォルダの一致ファイルを調べる。

    戻り値 (numbers, latest_name)
      numbers     … 見つかったバージョン番号の一覧（int）
      latest_name … 最新表記のファイル名。無ければ ""
    """
    v = ver_settings(rule)
    rx = _file_regex(rule, values)
    num_rx = re.compile("^" + re.escape(v["prefix"]) + r"(\d+)$", re.IGNORECASE)
    numbers, latest = [], ""
    try:
        names = os.listdir(directory)
    except OSError:
        return numbers, latest
    for n in names:
        m = rx.match(n)
        if not m:
            continue
        tok = m.group("ver")
        if tok.lower() == v["latest_label"].lower():
            latest = n
            continue
        nm = num_rx.match(tok)
        if nm:
            try:
                numbers.append(int(nm.group(1)))
            except ValueError:
                pass
    return numbers, latest


def next_version_text(directory: str, rule, values) -> str:
    """インクリメンタルセーブ OFF の時の ``{ver}``（最大 +1、無ければ 1）。"""
    v = ver_settings(rule)
    numbers, _latest = scan_versions(directory, rule, values)
    nxt = (max(numbers) + 1) if numbers else 1
    return "%s%0*d" % (v["prefix"], v["digits"], nxt)


def suggest_name(directory: str, rule, values, incremental: bool) -> str:
    """保存名の提案（**拡張子なし**）。

    拡張子は保存ダイアログで選ばれているものを呼び出し側が付ける（r145）。
    """
    v = ver_settings(rule)
    ver = v["latest_label"] if incremental else \
        next_version_text(directory, rule, values)
    return expand(stem_template(rule), dict(values, ver=ver))


def archive_latest(directory: str, rule, values):
    """インクリメンタルセーブ: 既存の最新ファイルを «次の番号» へ複製する。

    戻り値 (複製先のファイル名, 例外)。何もしなかった時は ("", None)。
    保存の **直前** に呼ぶ（新しい内容が cur を上書きする前に退避させる）。
    """
    v = ver_settings(rule)
    numbers, latest = scan_versions(directory, rule, values)
    if not latest:
        return "", None          # まだ最新ファイルが無い＝退避するものが無い
    nxt = (max(numbers) + 1) if numbers else 1
    ver = "%s%0*d" % (v["prefix"], v["digits"], nxt)
    # 拡張子は «退避するファイルが持っているもの» をそのまま引き継ぐ（r145）
    dst_name = (expand(stem_template(rule), dict(values, ver=ver))
                + os.path.splitext(latest)[1])
    src = os.path.join(directory, latest)
    dst = os.path.join(directory, dst_name)
    if os.path.exists(dst):
        # 既にある番号へは書かない（上書きしない）
        return "", FileExistsError(dst)
    try:
        shutil.copy2(src, dst)
    except OSError as e:
        return "", e
    return dst_name, None
