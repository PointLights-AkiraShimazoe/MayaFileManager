# -*- coding: utf-8 -*-
"""r146: 処理中は «その対象だけ» 触れない（ユーザー指示 2026-10-13）。

  「移動中やコピー中にマネージャーが止まらない様にしてください。
    進捗バーは出してください。」
  「（止めるなら）不可にするのは、対象となるファイル・フォルダだけに
    してほしいです。移動元と移動先のもののみ」

進捗ダイアログを非モーダルにしたので、走っている最中でもマネージャーは
触れる。ただし走っている操作が触っている場所（移動元・移動先と、その
上下関係にあるもの）だけは断る。無関係な場所は今までどおり通す。
"""
import io
import os

from _common import *  # noqa: F401,F403
from _common import finish

from core import file_locks as L

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


L.release_all()
base = os.path.abspath(os.path.join(os.sep, "mfm_lock_test"))
shot = os.path.join(base, "work", "shot_010")
token = L.acquire([shot], "コピー")

check(bool(L.conflicts([shot])), "そのものは断る")
check(bool(L.conflicts([os.path.join(shot, "ch", "a.ma")])), "中身も断る")
check(bool(L.conflicts([os.path.join(base, "work")])),
      "親も断る（親を動かすと中身が迷子になる）")
check(not L.conflicts([os.path.join(base, "work", "shot_020")]),
      "隣のフォルダは通す（止めるのは対象だけ）")
check(not L.conflicts([os.path.join(base, "other")]), "無関係な場所は通す")
check(L.conflicts([shot])[0][1] == "コピー", "何で塞がっているかが分かる")

L.release(token)
check(not L.conflicts([shot]), "終われば通る")

# 複数の操作が同時に走っても、それぞれの対象だけが塞がる
t1 = L.acquire([os.path.join(base, "a")], "移動")
t2 = L.acquire([os.path.join(base, "b")], "削除")
check(bool(L.conflicts([os.path.join(base, "a")])) and
      bool(L.conflicts([os.path.join(base, "b")])), "同時に 2 つ塞がる")
L.release(t1)
check(not L.conflicts([os.path.join(base, "a")])
      and bool(L.conflicts([os.path.join(base, "b")])),
      "片方を解放しても、もう片方は塞がったまま")
L.release(t2)
check(not L.busy_paths(), "全部終われば空になる")

# ── 呼び出し側に組み込まれているか ───────────────────────────────
from _common import panel_source
bp = panel_source()
check("def _busy_guard" in bp, "重なりを見る共通処理がある")
check("busy_paths=list(paths) + [target]" in bp,
      "コピー／移動は «移動元と移動先» を予約する")
check("busy_paths=list(targets)" in bp, "削除は対象を予約する")
check("file_locks.release(lock_token)" in bp, "終わったら必ず解放する")
for verb in ('tr("削除", "Delete")', 'tr("名前変更", "Rename")',
             'tr("複製", "Duplicate")', 'tr("高度なコピー", "Advanced Copy")'):
    check(("_busy_guard" in bp) and (verb in bp),
          "%s の入口で確かめる" % verb)
br = io.open(os.path.join(ROOT, "ui", "batch_rename_dialog.py"),
             encoding="utf-8").read()
check("file_locks" in br, "バッチリネームも確かめる")

# 進捗バーは出したまま・非モーダルのまま
check("QProgressDialog" in bp and "setWindowModality(Qt.NonModal)" in bp,
      "進捗バーは出したまま、マネージャーは止めない")

L.release_all()
finish(not fails)
