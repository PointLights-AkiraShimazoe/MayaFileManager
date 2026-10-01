# -*- coding: utf-8 -*-
"""r112: 握り潰しは «実装ミスだけ» を記録する。

想定内のエラー（ファイル I/O・破棄済みウィジェット）は従来どおり黙殺し、
NameError や AttributeError のような «出たら必ずバグ» の型だけを
~/mfm_debug.log へ残す。swallow() は絶対に例外を投げてはならない。
"""
import os
from _common import *  # noqa: F401,F403
from _common import finish, run
from core import diag


def s1():
    diag.reset()

    # 想定内 → 記録しない
    for exc in (OSError("ファイルが無い"), RuntimeError("C++ object deleted"),
                ValueError("変換できない")):
        diag.swallow(exc, "x.py:1 f")
    assert diag.swallowed_count() == 0, ("想定内のエラーを記録した",
                                         diag.swallowed_count())

    # 実装ミス → 記録する
    diag.swallow(NameError("X が未定義"), "x.py:2 g")
    diag.swallow(AttributeError("属性が無い"), "x.py:3 h")
    diag.swallow(TypeError("引数が違う"), "x.py:4 i")
    assert diag.swallowed_count() == 3, diag.swallowed_count()

    # 同じ場所は 1 回だけ（ログが溢れない）
    for _ in range(10):
        diag.swallow(NameError("X が未定義"), "x.py:2 g")
    assert diag.swallowed_count() == 3, ("同じ場所を重複記録した",
                                         diag.swallowed_count())
    print("records only real bugs, once per site: OK")

    # 何を渡しても絶対に投げない（握り潰しの代わりなので致命的）
    class _Weird(Exception):
        def __str__(self):
            raise RuntimeError("__str__ が壊れている")

    diag.swallow(_Weird(), "x.py:5 j")
    diag.swallow(None, "x.py:6 k")
    diag.swallow(NameError("boom"), None)
    print("never raises: OK")

    # 実コードに仕込まれていること（抜き打ち）
    import io, os
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    for rel in ("ui/browser_panel.py", "ui/browser_column_view.py",
                "core/file_operations.py", "main.py"):
        path = os.path.join(root, rel)
        src = io.open(path, encoding="utf-8").read()
        assert "_swallow(_e," in src, ("swallow 経由になっていない", path)
        assert "from core.diag import swallow as _swallow" in src, path
    print("wired into the real modules: OK")
    finish(True)


run(s1, delay=200)
