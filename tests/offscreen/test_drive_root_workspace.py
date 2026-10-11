# -*- coding: utf-8 -*-
"""r137: ワークスペースの Root が «ドライブ直下» でも配下が管理下と分かること。

ユーザー報告 2026-10-08:
  「また反映されていない Perforce があります」（ドライブ直下がワークスペースのルートになっている共有ドライブ）

連携ログに答えが出ていた:
  root via clients: 'S:\\' (ws=shimazoe.a_...)        ← S:\ 自体は一致する
  dir 'S:/<共有フォルダ>/...': providers=なし    ← その下は全部ハズレ

原因は «下にあるか» の判定:
  norm("S:\\") は "s:\\" のまま（末尾の区切りが残る）なので、
  startswith(root + os.sep) が "s:\\\\" という存在しない前置きになり、
  ドライブ直下がルートのワークスペースは **配下が必ず外れる**。

同じ書き方をしていたクラウド側（Google ドライブ等）も直した。
"""
import os
from _common import *  # noqa: F401,F403
from _common import finish, run

from core.integrations.base import is_under

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def _n(p):
    """Windows 風の norm（実機と同じ比較を、どの OS でも再現する）。"""
    import ntpath
    return ntpath.normcase(ntpath.normpath(p))


def s1():
    sep = "\\"

    # ── 1) 実機で踏んだ形そのもの ────────────────────────────────────
    root = _n("S:\\")
    deep = _n("S:\\Team Share\\ProjectA\\Assets\\Chars")
    check(_is_under(deep, root, sep),
          "ドライブ直下がルートでも配下を «下» と判定する（%r < %r）" % (deep, root))
    check(_is_under(root, root, sep), "ルート自身も «下» 扱い")
    # 従来の書き方が本当に外していたことも固定しておく（再発の目印）
    check(not deep.startswith(root + sep),
          "従来の startswith(root + sep) は外れる（これが原因だった）")

    # ── 2) 普通のルートは今までどおり ────────────────────────────────
    r2 = _n("C:\\Work\\Projects\\ProjectB")
    check(_is_under(_n("C:\\Work\\Projects\\ProjectB\\Scenes\\a.ma"), r2, sep),
          "通常のワークスペース配下")
    check(_is_under(r2, r2, sep), "通常のルート自身")

    # ── 3) 名前が前方一致するだけの別フォルダを «下» にしない ─────────
    check(not _is_under(_n("C:\\WorkX\\a"), _n("C:\\Work"), sep),
          "前方一致のワナ（WorkX を Work の下にしない）")
    check(not _is_under(_n("T:\\x"), _n("S:\\"), sep), "別ドライブは «下» ではない")
    check(not _is_under("", r2, sep) and not _is_under(r2, "", sep),
          "空文字は常に False")

    # ── 4) 末尾に区切りが付いた普通のルートも扱える ───────────────────
    check(_is_under(_n("C:\\Work\\Projects\\x"), "c:\\work\\projects\\",
                    sep),
          "末尾に区切りが残ったルートでも «下» を見つける")

    finish(not fails)


def _is_under(path_key, root_key, sep):
    """is_under を «Windows の区切り» で評価する。

    本体は os.sep を見るので、Linux のオフスクリーン実行でも実機と同じ
    判定になるよう、ここで区切りを合わせて呼び分ける。"""
    if os.sep == sep:
        return is_under(path_key, root_key)
    # POSIX 実行時: 区切りを / に置き換えて同じ形にする
    return is_under(path_key.replace(sep, "/"), root_key.replace(sep, "/"))


run(s1, delay=200)
