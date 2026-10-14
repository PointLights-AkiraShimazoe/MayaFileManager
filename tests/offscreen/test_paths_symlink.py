# -*- coding: utf-8 -*-
"""_safe_file_path と Qt filePath の一致、シンボリックリンクのブラウジング、戻る。"""
import os
from _common import *  # noqa: F401,F403
from _common import (app, make_panel, columns, tmpdir, finish, run, QTimer,
                     QTest, _safe_file_path)

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
    assert _safe_file_path(Mdl(), Idx(["ローカル ディスク (C:)", "Work"])) == "C:/Work"
    assert _safe_file_path(Mdl(), Idx(["Local Disk (D:)"])) == "D:/"
    print("drive label normalization: OK")
    b.navigate_to(link)
    QTimer.singleShot(1500, s2)


def _column_names():
    names = set()
    for cv in columns(b):
        m = cv.model(); ri = cv.rootIndex()
        for r in range(m.rowCount(ri)):
            names.add(m.index(r, 0, ri).data())
    return names


def _wait_names(want, timeout_ms=8000):
    """カラムに項目が出るまで待つ。

    カラムの生成は QFileSystemModel のロード待ちで非同期。固定の待ち時間に
    頼ると «経路の途中に項目数の多いフォルダがある時だけ落ちる» 不安定テスト
    になる（実機 2026-10-14: Temp に 3,484 件あって 1.5 秒では間に合わず、
    リンクのカラムがまだ生えていなかった）。
    """
    waited = 0
    names = _column_names()
    while waited < timeout_ms and not want <= names:
        QTest.qWait(100)
        app.processEvents()
        waited += 100
        names = _column_names()
    return names


def s2():
    assert b.current_path().replace("\\", "/").endswith("/linkdir"), b.current_path()
    names = _wait_names({"sub1", "f.txt"})
    assert {"sub1", "f.txt"} <= names, sorted(names)[:20]
    # r146（ユーザー報告 2026-10-13）: リンクへ入っても «左のカラム» を残す。
    # r145 までは経路にリンクがあるだけでそのリンクをルートにしていたので、
    # ジャンクションへ入った瞬間に親から上のカラムが消え、左へ戻れなくなった。
    root = b._column_root_for(os.path.join(link, "sub1")).replace("\\", "/")
    assert not root.endswith("/linkdir"), ("リンクをルートにしてはいけない", root)
    assert link.replace("\\", "/").startswith(root.rstrip("/")), (root, link)
    print("symlink browsing keeps the ancestors: OK")

    # «リンクの親» も同じルートから辿れる＝左のカラムが生きている
    parent = os.path.dirname(link)
    assert b._norm(b._column_root_for(parent)) == b._norm(b._column_root_for(link)), \
        (b._column_root_for(parent), b._column_root_for(link))

    # 退避路: «列挙できない» と登録されたリンクだけ、そのリンクがルートになる
    b._link_own_root.add(b._norm(link))
    root2 = b._column_root_for(os.path.join(link, "sub1")).replace("\\", "/")
    assert root2.endswith("/linkdir"), ("退避路が効いていない", root2)
    b._link_own_root.discard(b._norm(link))
    print("fallback root only when registered: OK")

    # 戻るはリンクの «外» へ出る（履歴は s1 の navigate_to(link) の 1 件だけ）
    b._go_back()
    QTimer.singleShot(1200, s3)


def s3():
    assert not b.current_path().replace("\\", "/").endswith("/linkdir"), \
        b.current_path()
    print("back: OK")
    # 左へ戻る操作（◀）が «リンクの中» からでも親へ抜けられる
    b.navigate_to(link)
    QTimer.singleShot(1200, s4)


def s4():
    assert b.current_path().replace("\\", "/").endswith("/linkdir"), b.current_path()
    b._column_go_up(link)
    app.processEvents()
    QTimer.singleShot(600, s5)


def s5():
    parent = os.path.dirname(link)
    cur = b._norm(b.current_path())
    assert cur in (b._norm(parent), b._norm(link)), (b.current_path(), parent)
    # 肝心なのは «親のカラムが存在すること»（左へ動ける）
    roots = set()
    for cv in columns(b):
        p = b._column_view._path_for_index(cv.rootIndex()) or ""
        if p:
            roots.add(b._norm(p))
    assert b._norm(parent) in roots or b._norm(b._column_root_for(link)) in roots, \
        (sorted(roots), parent)
    print("can move left out of a link: OK")
    finish()


run(s1)
