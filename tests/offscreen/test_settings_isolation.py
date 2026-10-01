# -*- coding: utf-8 -*-
"""r63 / r119: テストは «本番設定» に絶対に触らない。

r63 で tests/offscreen/_common.py に退避先を入れたが、**tests/smoke_test.py
には入っていなかった**。run_smoke.bat を回すたびにユーザーの
~/.maya_file_manager へ書き込んでおり、実機のリファレンスプリセットに
「smoke_test」「smoke_test_copy」が残っていた（2026-10-01 報告）。
同じ穴がまた開かないよう、«SettingsManager を作るテスト起点» は全て
MFM_SETTINGS_ROOT を設定していることを機械的に確かめる。
"""
import io
import os
import re
from _common import *  # noqa: F401,F403
from _common import finish, ROOT

from core.settings_manager import SettingsManager

# 1) いま走っているテスト自身が本番を見ていないこと
root = str(SettingsManager._resolve_root())
home_default = os.path.join(os.path.expanduser("~"), ".maya_file_manager")
assert os.environ.get("MFM_SETTINGS_ROOT"), "MFM_SETTINGS_ROOT が未設定"
assert os.path.normcase(root) != os.path.normcase(home_default), root
print("the running test is pointed at a throwaway settings root: OK")

# 2) テストの «起点» ファイルは全て退避先を設定していること
#    起点 = _common を使わずに SettingsManager を作るもの
TEST_DIR = os.path.join(ROOT, "tests")
offenders = []
for base, dirs, files in os.walk(TEST_DIR):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for fn in files:
        if not fn.endswith(".py"):
            continue
        path = os.path.join(base, fn)
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        text = io.open(path, encoding="utf-8", errors="replace").read()
        makes_sm = re.search(r"SettingsManager\s*\(", text)
        if not makes_sm:
            continue
        uses_common = "from _common import" in text or "import _common" in text
        sets_root = "MFM_SETTINGS_ROOT" in text
        if not (uses_common or sets_root):
            offenders.append(rel)
assert not offenders, (
    "本番設定を汚し得るテスト（MFM_SETTINGS_ROOT も _common も使っていない）:\n"
    + "\n".join(offenders))
print("every test entry point isolates its settings root: OK")

# 3) 退避先に書いても本番のファイルが増えないこと
sm2 = SettingsManager()
sm2.save_reference_presets({"isolation_probe": {"references": []}})
probe = os.path.join(root, "state_global.json")
assert os.path.isdir(root), root
# 本番側に probe が現れていないこと（本番が存在する場合のみ確認）
real_state = os.path.join(home_default, "state_global.json")
if os.path.isfile(real_state):
    body = io.open(real_state, encoding="utf-8", errors="replace").read()
    assert "isolation_probe" in body is False or "isolation_probe" not in body, \
        "本番の state_global.json に書き込んでいる"
sm2.save_reference_presets({})
print("writes land in the throwaway root, not the real one: OK")

finish()
