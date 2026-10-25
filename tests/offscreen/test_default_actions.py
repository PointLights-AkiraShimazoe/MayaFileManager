# -*- coding: utf-8 -*-
"""r146（ユーザー指示 2026-10-26）: クリック動作と D&D 動作の既定値。

  クリック（マウスのアイコン）… 何もしない (None)
  D&D（ドラッグのアイコン）  … 開く

以前は既定が場所ごとにばらばらだった（DEFAULT_SETTINGS は "preview"、
メインウィンドウの D&D は "import"、パネルと DCC 側は "none"）。
新規の設定（設定ファイルが空）で MainWindow を開き、2 つのコンボが
指定どおりになることと、コード中の全ての get の第 2 引数が
DEFAULT_SETTINGS と一致していることを確かめる。"""
import io
import os
import re
import tempfile
from _common import *  # noqa: F401,F403
from _common import app, finish, QTest

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


from core.settings_manager import SettingsManager, DEFAULT_SETTINGS

check(DEFAULT_SETTINGS.get("single_click_action") == "none", "既定: クリック＝何もしない")
check(DEFAULT_SETTINGS.get("dnd_action") == "open", "既定: D&D＝開く")

# 新規の設定（空のフォルダ）で MainWindow を開く
os.environ["MFM_SETTINGS_ROOT"] = tempfile.mkdtemp(prefix="mfm_fresh_settings_")
fresh = SettingsManager()
check(fresh.get("single_click_action") == "none" and fresh.get("dnd_action") == "open",
      "新規の設定で読める値が既定どおり")

from ui.main_window import MainWindow
w = MainWindow(settings_manager=fresh)
QTest.qWait(200)
app.processEvents()
click_txt = w._action_combo.currentText()
dnd_txt = w._dnd_combo.currentText()
check("何もしない" in click_txt or "None" in click_txt,
      "クリックのコンボが «何もしない»: %r" % click_txt)
check(dnd_txt.startswith("開く") or dnd_txt.lower().startswith("open"),
      "D&D のコンボが «開く»: %r" % dnd_txt)
check(fresh.get("single_click_action") == "none" and fresh.get("dnd_action") == "open",
      "開いただけで既定値が書き換わらない")
w.close()

# コード中の get の第 2 引数が既定と食い違っていない
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
pat = re.compile(r'get\(\s*"(single_click_action|dnd_action)"\s*,\s*"([a-z]+)"')
bad = []
for d in ("ui", "core"):
    for name in os.listdir(os.path.join(ROOT, d)):
        if not name.endswith(".py"):
            continue
        src = io.open(os.path.join(ROOT, d, name), encoding="utf-8").read()
        for m in pat.finditer(src):
            if DEFAULT_SETTINGS.get(m.group(1)) != m.group(2):
                bad.append("%s/%s: %s の既定が %r" % (d, name, m.group(1), m.group(2)))
check(not bad, "全ての呼び出し側の既定が DEFAULT_SETTINGS と一致: %s" % (bad or "OK"))

finish(not fails)
