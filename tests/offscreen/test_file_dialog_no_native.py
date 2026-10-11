# -*- coding: utf-8 -*-
"""r145: ファイル／フォルダ選択でネイティブダイアログを使わない。

ユーザー報告 2026-10-09:
  「フォルダを指定するために「⋯」ボタンを押し、キャンセルすると、
    manager がしばらくフリーズします。maya でも起きていますが、
    Manager で同じ問題は起こさないでほしいです。」

Windows のネイティブ（シェル）ダイアログは、ネットワークドライブやクラウドの
名前空間を列挙しに行くため、閉じる時の後始末で数秒〜十数秒ブロックすることが
ある。Qt 自前のダイアログはシェルを通さないのでこの待ちが起きない。

ここで固定すること:
  1) 共通ヘルパが DontUseNativeDialog を必ず付ける
  2) ui/ のどこにも «素の» QFileDialog.getExistingDirectory /
     getOpenFileName が残っていない（1 箇所でも残ると、そこだけ固まる）
"""
import io
import os
import re

from _common import *  # noqa: F401,F403
from _common import finish

from core.compat import QFileDialog
from ui import dialog_util

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


opt = dialog_util._qt_dialog_opts()
check(bool(opt & QFileDialog.DontUseNativeDialog),
      "共通ヘルパが DontUseNativeDialog を付ける")

opt2 = dialog_util._qt_dialog_opts(QFileDialog.ShowDirsOnly)
check(bool(opt2 & QFileDialog.DontUseNativeDialog)
      and bool(opt2 & QFileDialog.ShowDirsOnly),
      "追加オプションを渡してもネイティブ無効は消えない")

raw = []
pat = re.compile(r"QFileDialog\.(getExistingDirectory|getOpenFileName|getSaveFileName)")
for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "ui")):
    if "__pycache__" in dirpath:
        continue
    for name in sorted(files):
        if not name.endswith(".py") or name == "dialog_util.py":
            continue
        path = os.path.join(dirpath, name)
        for i, line in enumerate(io.open(path, encoding="utf-8"), 1):
            if pat.search(line):
                raw.append("%s:%d" % (name, i))
check(not raw,
      "素の QFileDialog 呼び出しが残っていない（%s）" % (", ".join(raw) or "なし"))

check(callable(dialog_util.pick_directory) and callable(dialog_util.pick_open_file),
      "共通ヘルパがある")

finish(not fails)
