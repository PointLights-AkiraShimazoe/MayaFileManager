# -*- coding: utf-8 -*-
"""r146: 起動時の Maya 連携案内（_maybe_offer_maya_bridge）の挙動。

実機事故 2026-10-24 の再発防止でモジュール方式に変えた。その移行の入口が
この案内なので、ユーザーの実際の経路で固定する:
  1. 旧方式のブロックが userSetup.py に残っている → 移行を促す
  2. 「今後聞かない」を選んでいれば、旧方式（動いてはいる）には黙る
  3. 壊れた版（v0〜v2）だけは「今後聞かない」でも知らせる
  4. モジュール方式が入っていれば何も出さない
  5. 案内を出す時も **他人の userSetup.py には触らない**
また、テストが «開発機の本物の Maya 設定フォルダ» を読まないこと（_common が
MAYA_APP_DIR を隔離している）も確かめる。"""
import os
import tempfile
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest

from core.compat import QMessageBox
from core import maya_bridge as _mb
from ui.main_window import MainWindow

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


# 0) テストは本物の Maya 設定フォルダを見ない
appdir = os.environ.get("MAYA_APP_DIR", "")
check(appdir and "mfm_test_maya_appdir_" in appdir,
      "_common が MAYA_APP_DIR を隔離している: %r" % appdir)
os.makedirs(os.path.join(appdir, "2026"), exist_ok=True)
os.makedirs(os.path.join(appdir, "scripts"), exist_ok=True)
_mb._app_dir_confirmed = None
_mb._app_dir_override = None

OLD_BLOCK = (_mb._US_BEGIN + "\n# mfm-snippet-version: %d\n"
             "def _mfm_open_bridge_port():\n    pass\n" + _mb._US_END + "\n")
USER_BYTES = (u"# -*- coding: cp932 -*-\r\n# スタジオ\r\nprint('existing')\r\n").encode("cp932")
us = _mb.usersetup_path()

shown = []
_orig_exec = QMessageBox.exec_ if hasattr(QMessageBox, "exec_") else None
_orig_exec2 = QMessageBox.exec


def _fake_exec(self, *a, **k):
    shown.append(self.text())
    return 0


QMessageBox.exec = _fake_exec
if _orig_exec is not None:
    QMessageBox.exec_ = _fake_exec

w = MainWindow(settings_manager=sm)
QTest.qWait(200)
app.processEvents()


def _offer():
    del shown[:]
    w._maybe_offer_maya_bridge()
    app.processEvents()
    return list(shown)


# 1) 旧方式（v4）が残っている → 移行を促す（文言は «古い方式»）
with open(us, "wb") as f:
    f.write(USER_BYTES + (OLD_BLOCK % 4).encode("cp932"))
sm.set(MainWindow.BRIDGE_PROMPT_KEY, "", save=False)
got = _offer()
check(len(got) == 1 and "古い方式" in got[0], "旧方式が残っていれば移行を促す: %r" % (got,))
with open(us, "rb") as f:
    check(f.read() == USER_BYTES + (OLD_BLOCK % 4).encode("cp932"),
          "案内を出すだけで userSetup.py には触らない")

# 2) 「今後聞かない」なら旧方式（動いている）には黙る
sm.set(MainWindow.BRIDGE_PROMPT_KEY, "never", save=False)
got = _offer()
check(got == [], "「今後聞かない」で旧方式の移行案内は出ない: %r" % (got,))

# 3) 壊れた版（v2: Maya 側で NameError）は「今後聞かない」でも知らせる
with open(us, "wb") as f:
    f.write(USER_BYTES + (OLD_BLOCK % 2).encode("cp932"))
got = _offer()
check(len(got) == 1, "壊れた版は「今後聞かない」でも知らせる: %r" % (got,))

# 4) モジュール方式が入っていて旧ブロックが無ければ何も出ない
with open(us, "wb") as f:
    f.write(USER_BYTES)
_mb.install_bridge()
sm.set(MainWindow.BRIDGE_PROMPT_KEY, "", save=False)
got = _offer()
check(got == [], "モジュール方式が入っていれば案内しない: %r" % (got,))
with open(us, "rb") as f:
    check(f.read() == USER_BYTES, "モジュール方式のインストールは userSetup.py に触らない")

# 5) 未導入（モジュールも旧方式も無い）→ 導入を促す。「今後聞かない」で黙る
_mb.uninstall_bridge()
got = _offer()
check(len(got) == 1 and "効いていない" in got[0], "未導入なら導入を促す: %r" % (got,))
sm.set(MainWindow.BRIDGE_PROMPT_KEY, "never", save=False)
got = _offer()
check(got == [], "未導入でも「今後聞かない」で黙る: %r" % (got,))

QMessageBox.exec = _orig_exec2
if _orig_exec is not None:
    QMessageBox.exec_ = _orig_exec
w.close()
if fails:
    print("FAILED:", fails)
finish(not fails)
