# -*- coding: utf-8 -*-
"""r146: アイコン提供者の type() は I/O もシェル呼び出しもしない。

実機 2026-10-16 16:00（フォルダ移動のあと 40 秒超の停止、Python 側の
スタックは app.exec() のみ）: QFileSystemModel はフォルダの変化のたびに
メインスレッドで getInfo() → iconProvider.type() を 1 ファイルずつ呼ぶ。
既定の type() は Windows のシェルに聞きに行き、外付け／ネットワークの
ボリュームでは 1 件ごとに待たされる。拡張子だけで決める。
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, finish
from core.compat import QFileInfo
from ui.browser_util import QFileIconProvider
from ui.browser_util import _SafeIconProvider

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


p = _SafeIconProvider()
calls = []
orig_type = QFileIconProvider.type
QFileIconProvider.type = lambda self, info: (calls.append(info), orig_type(self, info))[1]
orig_isdir = os.path.isdir
os.path.isdir = lambda path: (calls.append(("isdir", path)), orig_isdir(path))[1]
try:
    t1 = p.type(QFileInfo("/nonexistent/x/scene.ma"))
    t2 = p.type(QFileInfo("/nonexistent/x/folder"))
    t3 = p.type(QFileInfo("/nonexistent/x/a.TGA"))
finally:
    QFileIconProvider.type = orig_type
    os.path.isdir = orig_isdir
check(t1 == "MA File", "拡張子から種類を決める（%r）" % t1)
check(t2 == "File Folder", "拡張子が無ければフォルダ扱い（仮の答え）（%r）" % t2)
check(t3 == "TGA File", "大文字に揃える（%r）" % t3)
check(not [c for c in calls if not isinstance(c, tuple)], "既定の type()（シェル）を呼ばない")
check(not [c for c in calls if isinstance(c, tuple)], "UI スレッドで os.path.isdir を呼ばない（%r）" % calls)
# IconType 列挙にはこれまでどおり答える
check(isinstance(p.type(QFileIconProvider.Folder), str), "列挙型にも文字列を返す")
finish(not fails)
