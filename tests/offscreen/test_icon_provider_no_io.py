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
import threading
_main = threading.main_thread()


def _isdir_spy(path):
    # 裏のスレッド（アイコンの «フォルダかどうか» を調べる係）が呼ぶ分は
    # 数えない。Windows の実機では icon() の直後にそちらが走り、
    # UI スレッドの違反として誤検出した（2026-10-16）。
    if threading.current_thread() is _main:
        calls.append(("isdir", path))
    return orig_isdir(path)


os.path.isdir = _isdir_spy
try:
    t1 = p.type(QFileInfo("/nonexistent/x/scene.ma"))
    t2 = p.type(QFileInfo("/nonexistent/x/folder"))
    t3 = p.type(QFileInfo("/nonexistent/x/a.TGA"))
    # 拡張子の無い «ファイル» / . を含む «フォルダ» を裏の係が後から
    # 知っても、種類の答えは揺れない（揺れると QFileSystemModel が
    # 再読のたびに dataChanged を出し、カラムの組み直しで固まる。
    # 実機計測 2026-10-16: Temp 7,500 件で 1 回の移動が 2 分）
    with p._lock:
        p._isdir["/nonexistent/x/folder"] = False
        p._isdir["/nonexistent/x/v1.2"] = True
    t2b = p.type(QFileInfo("/nonexistent/x/folder"))
    t4 = p.type(QFileInfo("/nonexistent/x/v1.2"))
finally:
    QFileIconProvider.type = orig_type
    os.path.isdir = orig_isdir
check(t1 == "MA File", "拡張子から種類を決める（%r）" % t1)
check(t2 == "File Folder", "拡張子が無ければフォルダ扱い（%r）" % t2)
check(t3 == "TGA File", "大文字に揃える（%r）" % t3)
check(t2b == t2, "裏で «実はファイル» と分かっても種類は揺れない（%r → %r）" % (t2, t2b))
check(t4 == "2 File", "裏で «実はフォルダ» と分かっても拡張子で決める（%r）" % t4)
check(not [c for c in calls if not isinstance(c, tuple)], "既定の type()（シェル）を呼ばない")
check(not [c for c in calls if isinstance(c, tuple)], "UI スレッドで os.path.isdir を呼ばない（%r）" % calls)
# IconType 列挙にはこれまでどおり答える
check(isinstance(p.type(QFileIconProvider.Folder), str), "列挙型にも文字列を返す")
finish(not fails)
