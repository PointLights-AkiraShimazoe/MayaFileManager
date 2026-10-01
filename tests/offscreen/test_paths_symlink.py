# -*- coding: utf-8 -*-
"""_safe_file_path と Qt filePath の一致、シンボリックリンクのブラウジング、戻る。"""
import os
from _common import *  # noqa: F401,F403
from _common import app, make_panel, columns, tmpdir, finish, run, QTimer, _safe_file_path

b = make_panel()
d = tmpdir(); os.makedirs(os.path.join(d, "sub")); open(os.path.join(d, "a.ma"), "w").close()
base = tmpdir(); real = os.path.join(base, "realdir"); os.makedirs(os.path.join(real, "sub1"))
open(os.path.join(real, "f.txt"), "w").close(); link = os.path.join(base, "linkdir"); os.symlink(real, link)
b.navigate_to(d)


def s1():
    fsm = b._fs_model
    mism = []

    def walk(idx, depth=0):
        for r in range(fsm.rowCount(idx)):
            c = fsm.index(r, 0, idx)
            if _safe_file_path(fsm, c) != fsm.filePath(c):
                mism.append((_safe_file_path(fsm, c), fsm.filePath(c)))
            if depth < 2:
                walk(c, depth + 1)
    walk(fsm.index(d))
    assert not mism, mism[:3]
    print("_safe_file_path == filePath: OK")
    # ドライブ表示名の正規化（Windows の Maya Qt を模擬）
    class Idx:
        def __init__(self, n): self.n = n
        def isValid(self): return bool(self.n)
        def parent(self): return Idx(self.n[:-1])
    class Mdl:
        def fileName(self, i): return i.n[-1]
    assert _safe_file_path(Mdl(), Idx(["ローカル ディスク (C:)", "GameStudio"])) == "C:/GameStudio"
    assert _safe_file_path(Mdl(), Idx(["Local Disk (D:)"])) == "D:/"
    print("drive label normalization: OK")
    b.navigate_to(link)
    QTimer.singleShot(1500, s2)


def s2():
    assert b.current_path().replace("\\", "/").endswith("/linkdir"), b.current_path()
    names = set()
    for cv in columns(b):
        m = cv.model(); ri = cv.rootIndex()
        for r in range(m.rowCount(ri)):
            names.add(m.index(r, 0, ri).data())
    assert {"sub1", "f.txt"} <= names, names
    assert b._column_root_for(os.path.join(link, "sub1")).replace("\\", "/").endswith("/linkdir")
    print("symlink browsing + link root: OK")
    b._go_back()
    QTimer.singleShot(1200, s3)


def s3():
    assert not b.current_path().replace("\\", "/").endswith("/linkdir")
    print("back: OK")
    finish()


run(s1)
