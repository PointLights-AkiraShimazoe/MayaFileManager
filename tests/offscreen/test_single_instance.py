# -*- coding: utf-8 -*-
"""r126: マネージャーの «複数起動» の扱い。

ユーザー質問 2026-10-06:
  「このマネージャーは複数立ち上げていても問題無いものでしょうか？
    複数起動が問題ある場合は、複数起動されない様にしたいです」

調査の結果 «問題あり»。固定すること:

  1) 設定は «自分が変えた分だけ» 書く。2 つ目が無関係な設定を 1 つ
     変えて保存しても、1 つ目の変更（プリセット等）が消えない。
     （従来: 起動時のスナップショットで丸ごと上書き → 実験で消失を確認）
  2) 設定の保存はアトミック。書き込み中に読まれても壊れた JSON を
     掴ませない（掴むと既定値に戻る＝設定が全部消える）。
  3) 単一起動の関所は «1 つ目は取れて 2 つ目は取れない»。
  4) 単一起動が使えない環境では «起動できない» より «複数を許す»。
"""
import json
import os
import tempfile
from _common import *  # noqa: F401,F403
from _common import finish

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


# ── 1) 設定の取り合いで消えない ───────────────────────────────────────
root = tempfile.mkdtemp(prefix="mfm_multi_")
old_root = os.environ.get("MFM_SETTINGS_ROOT")
os.environ["MFM_SETTINGS_ROOT"] = root
try:
    from core.settings_manager import SettingsManager
    A = SettingsManager()
    A.set("ui_language", "ja")
    A.save_quick_nav_presets({"default": [{"label": "案件A", "path": "D:/jobA"}]})

    B = SettingsManager()                       # 2 つ目を «その後» 起動
    A.save_quick_nav_presets({"default": [{"label": "案件A", "path": "D:/jobA"},
                                          {"label": "案件B", "path": "D:/jobB"}]})
    B.set("single_click_action", "none")        # 無関係な設定を 1 つ変えただけ
    B.save()

    C = SettingsManager()                       # ディスクの真実
    presets = C.get_quick_nav_presets().get("default", [])
    check(len(presets) == 2,
          "2 つ目の保存で 1 つ目のプリセットが消えない（%d 件）" % len(presets))
    check(C.get("single_click_action") == "none", "2 つ目の変更も残る")
    check(C.get("ui_language") == "ja", "1 つ目の変更も残る")

    # ── 2) 保存はアトミック（中間状態のファイルを残さない）────────────
    A.set("ui_language", "en")
    leftovers = [f for f in os.listdir(root) if ".tmp" in f]
    check(not leftovers, "一時ファイルを残さない（%r）" % leftovers)
    sp = [os.path.join(root, f) for f in os.listdir(root) if f.endswith(".json")]
    bad = []
    for f in sp:
        try:
            json.load(open(f, encoding="utf-8"))
        except Exception as e:
            bad.append((os.path.basename(f), str(e)))
    check(not bad, "保存後の JSON が常に読める（%r）" % bad)
finally:
    if old_root is None:
        os.environ.pop("MFM_SETTINGS_ROOT", None)
    else:
        os.environ["MFM_SETTINGS_ROOT"] = old_root

# ── 3) 単一起動の関所 ────────────────────────────────────────────────
from core.single_instance import SingleInstance  # noqa: E402

key = "MayaFileManagerTest-%d" % os.getpid()
first = SingleInstance(key)
got1 = first.acquire()
check(got1, "1 つ目は関所を取れる")

second = SingleInstance(key)
got2 = second.acquire()
check(not got2, "2 つ目は関所を取れない（＝起動させない）")

# 2 つ目からの «前に出て» が 1 つ目に届く
raised = []
first.set_second_launch_handler(lambda: raised.append(1))
second.notify_existing()
app.processEvents()
QTest.qWait(300)
app.processEvents()
check(raised, "2 つ目の起動が 1 つ目に伝わる（既存ウィンドウを前面に）")

first.release()
third = SingleInstance(key)
check(third.acquire(), "1 つ目が終われば次が取れる（ロックが残らない）")
third.release()

# ── 4) QtNetwork が無い環境では締め出さない ──────────────────────────
import core.single_instance as _si  # noqa: E402
import builtins  # noqa: E402

_real_import = builtins.__import__


def _no_qtnetwork(name, *a, **k):
    if "QtNetwork" in name:
        raise ImportError("QtNetwork なし（模擬）")
    return _real_import(name, *a, **k)


builtins.__import__ = _no_qtnetwork
try:
    check(_si.SingleInstance("x").acquire(),
          "QtNetwork が無い環境では «起動できない» より «複数を許す»")
finally:
    builtins.__import__ = _real_import

finish(not fails)
