# -*- coding: utf-8 -*-
"""r146: アドレス欄をディレクトリ／ファイル名に分け、間に幅調整バーを置く。

ユーザー指示 2026-10-14:
  「ファイルパスのテキストフィールドですが、ディレクトリとファイル名で
    フィールドを分けてください。その上で、一番右側にフルパスのコピー
    ボタンを付けてください。」
  「ファイル名とディレクトリ名の間に幅を調整出来るバーをください。」
ユーザー報告 2026-10-14 / 指示 2026-10-15:
  「起動時にパスが \\ になってます。一度でも選択すれば / になりますが、
    これはバグですね？」
  「そもそも Manager のパスが \\ になっています。MayaFileManager なので、
    全ての状況で / にしてください。」

ここで固定すること:
  1) フォルダは左の欄だけ／ファイルは左＝親・右＝ファイル名
  2) **区切り文字は / に揃える**（Maya へそのまま貼れる形）
  3) 入れた文字列は（区切りを揃えた上で）そのまま返る。組み直して
     «末尾が消える／引用符が落ちる» ようなことはしない
  4) 2 つの欄の間に掴めるバーがあり、幅が設定に残って次回も同じになる
  5) 右端のコピーボタンでフルパスがクリップボードに入る
  6) どちらの欄でも Enter で returnPressed が出る（移動できる）
  7) ブラウザ側が今までどおり setText / text / returnPressed で使える
"""
import os
import tempfile

from _common import *  # noqa: F401,F403
from _common import app, sm, make_panel, finish

from core.compat import QApplication, QSplitter
from ui.address_bar import AddressBar

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def disp(p):
    """表示されるはずの形（OS の区切り文字）。"""
    return AddressBar.for_display(p)


d = tempfile.mkdtemp(prefix="mfm_addr_")
sub = os.path.join(d, "sub")
os.makedirs(sub)
f = os.path.join(d, "scene_a.ma")
open(f, "w").close()

ab = AddressBar()

# ── 1) 振り分け ─────────────────────────────────────────────────
ab.setText(d)
check(ab.dir_edit.text() == disp(d) and ab.file_edit.text() == "",
      "フォルダは左の欄だけ（%r / %r）" % (ab.dir_edit.text(), ab.file_edit.text()))
ab.setText(f)
check(ab.dir_edit.text() == disp(d) and ab.file_edit.text() == "scene_a.ma",
      "ファイルは 親＋ファイル名 に分かれる（%r / %r）"
      % (ab.dir_edit.text(), ab.file_edit.text()))

# ── 2) 区切り文字は OS に揃う ───────────────────────────────────
ab.setText("C:\\a\\b.ma")
check(os.sep not in ("\\",) or "\\" not in ab.dir_edit.text(),
      "Windows では \\ を残さない（%r）" % (ab.dir_edit.text(),))
ab.setText("C:/a/b.ma")
mixed = ab.text()
ab.setText("C:\\a\\b.ma")
if os.sep == "\\":
    check(ab.text() == mixed and "\\" not in ab.text(),
          "/ でも \\ でも «/» で同じ見え方（%r / %r）" % (mixed, ab.text()))
else:
    # POSIX では \ はファイル名に使える «普通の文字» なので触ってはいけない。
    # ここで / に直すと、本当に \ を含む名前のファイルへ行けなくなる。
    check(ab.text() == "C:\\a\\b.ma",
          "POSIX では \\ を書き換えない（%r）" % (ab.text(),))

# ── 3) 入れた文字列が（区切りを揃えた上で）そのまま返る ─────────
for raw in (d, f, '"%s"' % f, d + os.sep, "C:/a/b.ma", "", "no_such/x.ma"):
    ab.setText(raw)
    check(ab.text() == disp(raw),
          "入れた通りに返る %r → %r" % (disp(raw), ab.text()))

# 欄を触ったら組み直す
ab.setText("C:/a/b.ma")
ab.file_edit.setText("c.ma")
check(ab.text() == disp("C:/a/c.ma"), "編集したら組み直す（%r）" % (ab.text(),))

# ── 4) 幅調整バー ───────────────────────────────────────────────
check(isinstance(ab.split, QSplitter), "2 つの欄は QSplitter に載っている")
order = [ab.split.widget(i) for i in range(ab.split.count())]
check(order == [ab.dir_edit, ab.file_edit],
      "バーの左がディレクトリ・右がファイル名")
check(not ab.split.childrenCollapsible(),
      "端まで動かしても欄が消えない（潰せない）")
check(ab.split.handleWidth() >= 4,
      "バーは掴める太さがある（%d）" % ab.split.handleWidth())

sm.set(AddressBar.SETTINGS_KEY, None, save=False)
ab2 = AddressBar(settings_manager=sm)
ab2.resize(600, 28)
app.processEvents()
ok = ab2.set_split_sizes([380, 180])
check(ok, "幅を当てはめられる")
ab2._on_split_moved()
saved = sm.get(AddressBar.SETTINGS_KEY)
check(isinstance(saved, list) and len(saved) == 2 and min(saved) > 0,
      "動かした幅が設定に残る（%r）" % (saved,))

ab3 = AddressBar(settings_manager=sm)
ab3.resize(600, 28)
app.processEvents()
check(ab3.split_sizes() == saved,
      "次に作った時も同じ幅（%r / %r）" % (ab3.split_sizes(), saved))

# 壊れた値で落ちない
for bad in (None, [], [0, 0], ["a", "b"], [100]):
    check(ab3.set_split_sizes(bad) is False, "壊れた幅は受け付けない %r" % (bad,))

# ── 5) コピーボタン ─────────────────────────────────────────────
got = []
ab.copied.connect(lambda s: got.append(s))
ab.setText(f)
ab.copy_btn.click()
app.processEvents()
cb = QApplication.clipboard()
check(cb is not None and cb.text() == disp(f),
      "クリップボードにフルパスが入る（%r）" % (cb.text() if cb else None,))
check(got and got[-1] == disp(f), "コピーしたことを知らせる（%r）" % (got,))
kids = [ab.layout().itemAt(i).widget() for i in range(ab.layout().count())]
check(kids == [ab.split, ab.copy_btn],
      "並びは 分割欄 → コピー（%r）"
      % ([k.objectName() or type(k).__name__ for k in kids],))

# ── 6) どちらの欄でも Enter ─────────────────────────────────────
hits = []
ab.returnPressed.connect(lambda: hits.append(1))
ab.dir_edit.returnPressed.emit()
ab.file_edit.returnPressed.emit()
check(len(hits) == 2, "左右どちらの Enter でも移動の合図が出る（%d）" % len(hits))

# ── 6b) ディレクトリ欄での Enter は «その欄だけ» が行き先 ───────────
# ユーザー報告 2026-10-19「Windows のパスをテキストフィールドに入れても、
# ドライブ違いのパスを入れてエンターしても、ディレクトリが飛べません」:
# ファイルを選んだ後（右欄に scene.ma が残る）に左欄へ別のフォルダを
# 打って Enter すると、text() が «新フォルダ/scene.ma» を返して弾かれていた。
other = tempfile.mkdtemp()
ab.setText(f)                                  # 右欄にファイル名が入る
check(ab.file_edit.text() != "", "前提: ファイル名欄に前の選択が残っている")
seen = []
ab.returnPressed.connect(lambda: seen.append(ab.text()))
ab.dir_edit.setText(other)
ab.dir_edit.returnPressed.emit()
check(seen and seen[-1] == disp(other),
      "左欄で Enter → 行き先は左欄のフォルダだけ（%r）" % (seen[-1] if seen else None,))
check(ab.file_edit.text() == "", "左欄で Enter するとファイル名欄は空になる")
# 別ドライブ風の表記でも同じ（存在しなくても text() はそのまま返す）
ab.dir_edit.setText("Z:\\other\\place")
ab.dir_edit.returnPressed.emit()
check(seen[-1].replace("\\", "/") == "Z:/other/place",
      "ドライブ違いのパスもそのまま行き先になる（%r）" % (seen[-1],))
# 右欄（ファイル名）で Enter した時は «左のフォルダ/ファイル名»
ab.setText(f)
ab.file_edit.setText("other.ma")
ab.file_edit.returnPressed.emit()
check(seen[-1] == disp(os.path.join(os.path.dirname(f), "other.ma")),
      "右欄で Enter → 左のフォルダ/ファイル名（%r）" % (seen[-1],))

# ── 7) ブラウザ側が今までどおり使える ───────────────────────────
b = make_panel()
check(isinstance(b._addr_bar, AddressBar), "パネルが新しいアドレス欄を使う")
b._addr_bar.setText(f)
check(b._addr_bar.text() == disp(f), "setText / text が今までどおり")
check(hasattr(b._addr_bar, "returnPressed"), "returnPressed を持つ")

finish(not fails)
