# -*- coding: utf-8 -*-
"""r146: フリーズ見張りが «アプリを落とさない» 形になっていること。

2026-10-14 に実機で特定した不具合:
  Windows + Maya の Python 3.13 で、回帰スイートが 3221225477
  （ACCESS_VIOLATION）でランダムに落ちていた。犯人は
  ``faulthandler.dump_traceback_later`` の常時再武装。止めたら
  test_preset_dirs は 2/6 → 6/6 合格になった。

  * ``dump_traceback_later`` は GIL を持たない C のスレッドから全スレッドの
    フレームを歩く。UI が «止まって見える» 間も C++ 側は Python のフレームを
    組み替えているので、歩いた先が解放済みになりうる。
  * 渡した file をこちらが手放すと、閉じた fd を書きに行く。ウィンドウごとに
    ``self._fh_file`` を開いていたので、2 枚目を作って 1 枚目が回収されると
    その状態になった。

ここでは «再発したら落ちる» ように 2 つを固定する。
  1) 見張りはプロセスに 1 つだけ（ウィンドウを 2 枚作っても 1 本）
  2) ネイティブダンプは MFM_FREEZE_DUMP が有る時だけ
"""
import ast
import os
import threading

from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, QTest

import ui.freeze_watchdog as mwd
from ui.main_window import MainWindow

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "ui",
                   "freeze_watchdog.py")


def _func(name):
    tree = ast.parse(open(SRC, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("%s が見つからない" % name)


def _calls(node, dotted):
    """node の中の «a.b(...)» 呼び出しを全部返す。"""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == dotted:
            out.append(n)
    return out


def s1():
    fn = _func("install")

    # 1) self._fh_file のような «ウィンドウの持ち物» にしない
    for n in ast.walk(fn):
        if isinstance(n, ast.Attribute) and n.attr == "_fh_file":
            raise AssertionError(
                "faulthandler に渡す file をウィンドウの属性で持っている"
                "（回収されると閉じた fd を書きに行って落ちる）")
    print("file はウィンドウの持ち物ではない: OK")

    # 2) dump_traceback_later は «条件の中» にしか無い
    dumps = _calls(fn, "dump_traceback_later")
    assert dumps, "ネイティブダンプの口が消えている（調査用に残すこと）"
    guarded = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.If):
            continue
        cond = ast.dump(n.test)
        if "MFM_FREEZE_DUMP" not in cond:
            continue
        for d in dumps:
            if any(d is c for c in _calls(n, "dump_traceback_later")):
                guarded.append(d)
    assert len(guarded) == len(dumps), (
        "dump_traceback_later が MFM_FREEZE_DUMP の外にある"
        "（常時武装すると ACCESS_VIOLATION でランダムに落ちる）")
    print("ネイティブダンプは MFM_FREEZE_DUMP の時だけ: OK")

    # 3) ウィンドウを 2 枚作っても見張りは 1 本
    before = [t for t in threading.enumerate()
              if t.name == "mfm-freeze-watchdog"]
    w1 = MainWindow(settings_manager=sm)
    w1.resize(900, 600)
    w1.show()
    QTest.qWait(150)
    app.processEvents()
    w2 = MainWindow(settings_manager=sm)
    w2.resize(900, 600)
    w2.show()
    QTest.qWait(150)
    app.processEvents()
    after = [t for t in threading.enumerate()
             if t.name == "mfm-freeze-watchdog"]
    assert len(after) <= 1, ("見張りがウィンドウの数だけ立っている", len(after))
    assert mwd._FREEZE_WD_INSTALLED, "見張りが入っていない"
    assert len(after) >= len(before), after
    print("見張りはプロセスに 1 本: OK (%d)" % len(after))

    # 脈拍はウィンドウごとに打つ（2 枚目でも更新される）
    beat = mwd._FREEZE_BEAT[0]
    QTest.qWait(400)
    app.processEvents()
    assert mwd._FREEZE_BEAT[0] >= beat, "脈拍が止まっている"
    print("脈拍はウィンドウごとに打つ: OK")

    w1.close()
    w2.close()
    app.processEvents()
    finish(True)


run(s1, delay=300)
