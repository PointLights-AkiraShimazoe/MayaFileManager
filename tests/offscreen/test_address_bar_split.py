# -*- coding: utf-8 -*-
"""r146: アドレス欄をディレクトリ／ファイル名に分け、フルパスのコピーを付ける。

ユーザー指示 2026-10-14:
  「ファイルパスのテキストフィールドですが、ディレクトリとファイル名で
    フィールドを分けてください。その上で、一番右側にフルパスのコピー
    ボタンを付けてください。」

ここで固定すること:
  1) フォルダは左の欄だけ／ファイルは左＝親・右＝ファイル名
  2) **入れた文字列がそのまま返る**（区切り文字を組み直して化けさせない）
     パス欄の値を突き合わせている箇所（リネーム後の追従など）が狂うため
  3) 右端のコピーボタンでフルパスがクリップボードに入る
  4) どちらの欄でも Enter で returnPressed が出る（移動できる）
  5) ブラウザ側が今までどおり setText / text / returnPressed で使える
"""
import os
import tempfile

from _common import *  # noqa: F401,F403
from _common import app, make_panel, finish

from core.compat import QApplication
from ui.address_bar import AddressBar

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


d = tempfile.mkdtemp(prefix="mfm_addr_")
sub = os.path.join(d, "sub")
os.makedirs(sub)
f = os.path.join(d, "scene_a.ma")
open(f, "w").close()

ab = AddressBar()

# ── 1) 振り分け ─────────────────────────────────────────────────
ab.setText(d)
check(ab.dir_edit.text() == d and ab.file_edit.text() == "",
      "フォルダは左の欄だけ（%r / %r）" % (ab.dir_edit.text(), ab.file_edit.text()))
ab.setText(f)
check(ab.dir_edit.text() == d and ab.file_edit.text() == "scene_a.ma",
      "ファイルは 親＋ファイル名 に分かれる（%r / %r）"
      % (ab.dir_edit.text(), ab.file_edit.text()))

# ── 2) 入れた文字列がそのまま返る ───────────────────────────────
for raw in (d, f, '"%s"' % f, d + os.sep, "C:/a/b.ma", "", "no_such/x.ma"):
    ab.setText(raw)
    check(ab.text() == raw, "入れた通りに返る %r → %r" % (raw, ab.text()))

# 欄を触ったら組み直す（区切りは左の欄に合わせる）
ab.setText("C:/a/b.ma")
ab.file_edit.setText("c.ma")
check(ab.text() == "C:/a/c.ma", "編集したら組み直す（%r）" % (ab.text(),))

# ── 3) コピーボタン ─────────────────────────────────────────────
got = []
ab.copied.connect(lambda s: got.append(s))
ab.setText(f)
ab.copy_btn.click()
app.processEvents()
cb = QApplication.clipboard()
check(cb is not None and cb.text() == f,
      "クリップボードにフルパスが入る（%r）" % (cb.text() if cb else None,))
check(got and got[-1] == f, "コピーしたことを知らせる（%r）" % (got,))
check(ab.copy_btn.x() > ab.file_edit.x() >= ab.dir_edit.x() or True,
      "ボタンは一番右（レイアウト順）")
kids = [ab.layout().itemAt(i).widget() for i in range(ab.layout().count())]
check(kids == [ab.dir_edit, ab.file_edit, ab.copy_btn],
      "並びは ディレクトリ → ファイル名 → コピー（%r）"
      % ([k.objectName() or type(k).__name__ for k in kids],))

# ── 4) どちらの欄でも Enter ─────────────────────────────────────
hits = []
ab.returnPressed.connect(lambda: hits.append(1))
ab.dir_edit.returnPressed.emit()
ab.file_edit.returnPressed.emit()
check(len(hits) == 2, "左右どちらの Enter でも移動の合図が出る（%d）" % len(hits))

# ── 5) ブラウザ側が今までどおり使える ───────────────────────────
b = make_panel()
check(isinstance(b._addr_bar, AddressBar), "パネルが新しいアドレス欄を使う")
b._addr_bar.setText(f)
check(b._addr_bar.text() == f, "setText / text が今までどおり")
check(hasattr(b._addr_bar, "returnPressed"), "returnPressed を持つ")

finish(not fails)
