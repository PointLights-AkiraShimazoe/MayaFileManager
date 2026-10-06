# -*- coding: utf-8 -*-
"""r124: プロパティの «読み取り専用» スイッチ。

ユーザー指示 2026-10-06:
  「プロパティですが、読み取り専用スイッチだけは表示し、変更出来る様にしてください」
  「複数選択の場合も読み取り専用の変更だけは出来る様にしてください。
    下層フォルダまで含めるかのオプションも作ってください」

確認すること:
  1) is_read_only / set_read_only が付け外しできる（Perforce 同期の
     読み取り専用を «その場で» 外せる、が本題）
  2) 対象の決め方: ファイルはそれ自身。フォルダ自身には付けない
     （Windows が無視するので «付いたふり» をさせない）。
     «下層フォルダまで含める» で配下のファイルを全部拾う
  3) 複数選択でもプロパティがメニューに出る
"""
import os
from _common import *  # noqa: F401,F403
from _common import tmpdir, finish, make_panel

from core.file_operations import is_read_only, set_read_only
import ui.browser_panel as bp
from ui.browser_panel import BrowserPanel

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


root = tmpdir()
f1 = os.path.join(root, "a.ma")
sub = os.path.join(root, "sub")
deep = os.path.join(sub, "deep")
os.makedirs(deep)
f2 = os.path.join(sub, "b.ma")
f3 = os.path.join(deep, "c.ma")
for f in (f1, f2, f3):
    open(f, "w").close()

# 1) 付け外し
check(not is_read_only(f1), "作ったばかりのファイルは書き込み可")
ok, err = set_read_only(f1, True)
check(ok and is_read_only(f1), "読み取り専用を付けられる（%r）" % (err,))
ok, err = set_read_only(f1, False)
check(ok and not is_read_only(f1), "読み取り専用を外せる（%r）" % (err,))

# 2) 対象の決め方
t = BrowserPanel._readonly_targets([f1], False)
check(t == [f1], "ファイル単体はそれ自身（%r）" % (t,))
t = BrowserPanel._readonly_targets([sub], False)
check(t == [], "フォルダ自身には付けない（%r）" % (t,))
t = sorted(BrowserPanel._readonly_targets([sub], True))
check(t == sorted([f2, f3]), "下層フォルダまで含める＝配下のファイル全部（%r）" % (t,))
t = sorted(BrowserPanel._readonly_targets([f1, sub], True))
check(t == sorted([f1, f2, f3]), "ファイルとフォルダの混在も拾う（%r）" % (t,))
t = BrowserPanel._readonly_targets([f1, f1], True)
check(t == [f1], "同じパスを重ねて渡しても 1 回だけ（%r）" % (t,))

# 一括で付けて外せる
for p in BrowserPanel._readonly_targets([sub], True):
    set_read_only(p, True)
check(is_read_only(f2) and is_read_only(f3), "下層まで一括で付けられる")
for p in BrowserPanel._readonly_targets([sub], True):
    set_read_only(p, False)
check(not is_read_only(f2) and not is_read_only(f3), "下層まで一括で外せる")


# 3) 複数選択でもメニューに出る
class _NoExecMenu(bp.QMenu):
    def exec_(self, *a, **k):
        pass

    def exec(self, *a, **k):          # noqa: A003
        pass


from core.compat import QPoint  # noqa: E402

b = make_panel()
_keep.append(b)
_orig = bp.QMenu
bp.QMenu = _NoExecMenu
try:
    texts = []
    holder = []

    def _grab(self, *a, **k):
        holder.append([x.text() for x in self.actions()])
    _NoExecMenu.exec_ = _grab
    _NoExecMenu.exec = _grab
    b._popup_context_menu([f1, f2], QPoint(5, 5))
finally:
    bp.QMenu = _orig
app.processEvents()
texts = holder[0] if holder else []
found = any("プロパティ" in t for t in texts)
# コンソールが cp932 の実機では絵文字（📋 等）を print できずに落ちる。
# 判定に要るのは «出たかどうか» なので、失敗時だけ ASCII に落として出す。
detail = "" if found else "（項目: %s）" % ascii([t for t in texts if t])
check(found, "複数選択でもプロパティが出る" + detail)

finish(not fails)
