# -*- coding: utf-8 -*-
"""Perforce バッジの P4V 準拠ポリシー（r53）と fstat 解析の状態マッピング。
p4 CLI 無しで完結: _badge_kind / _badge_tooltip / P4Provider.fetch_status の
出力パースのみを検証する（外部コマンドは呼ばない）。"""
import os
from _common import *  # noqa: F401,F403
from _common import finish, ROOT
from ui.browser_panel import (_badge_kind, _badge_tooltip, _badge_pixmap,
                              _P4_BADGE_PNG, StatusBadgeDelegate)
from core.integrations import p4_provider
from core.integrations.base import (ST_MODIFIED, ST_ADDED, ST_DELETED,
                                    ST_OUTDATED, ST_LOCKED, ST_OTHER_OPEN,
                                    ST_CLEAN, ST_UNTRACKED)

# 1) PNG が全て存在し 24x24 の非空画像であること
for st, stem in _P4_BADGE_PNG.items():
    path = os.path.join(ROOT, "resources", "icons", stem + ".png")
    assert os.path.isfile(path), path
    pm = _badge_pixmap(stem)
    assert pm is not None and pm.width() == 24 and pm.height() == 24, stem
print("p4 badge PNGs present (24x24): OK")

# 2) ポリシー: p4 はフォルダ無印・clean/untracked 無印・その他は PNG
assert _badge_kind("p4", ST_MODIFIED, True) is None
assert _badge_kind("p4", ST_CLEAN, False) is None
assert _badge_kind("p4", ST_UNTRACKED, False) is None
for st in (ST_MODIFIED, ST_ADDED, ST_DELETED, ST_OUTDATED, ST_LOCKED, ST_OTHER_OPEN):
    k = _badge_kind("p4", st, False)
    assert k and k[0] == "png" and k[1] == _P4_BADGE_PNG[st], (st, k)
# Git/SVN/クラウドは従来通り（フォルダにも丸バッジ）
assert _badge_kind("git", ST_CLEAN, True)[0] == "dot"
assert _badge_kind("cloud", "online_only", False)[0] == "dot"
assert _badge_kind("git", "nonsense", False) is None
# ツールチップ: p4 フォルダは空、ファイルは Perforce: ...
assert _badge_tooltip("p4", ST_MODIFIED, True) == ""
assert _badge_tooltip("p4", ST_CLEAN, False).startswith("Perforce:")
assert _badge_tooltip("git", ST_CLEAN, True).startswith("Git:")
print("badge policy (p4 folders/clean/untracked hidden, others PNG): OK")

# 3) fstat -ztag 出力の解析（otherLock > otherOpen、headRev≠haveRev → outdated）
prov = p4_provider.P4Provider()
prov.p4 = "p4"
sample = "\n".join([
    "... clientFile /w/a.ma", "... headRev 3", "... haveRev 3", "... action edit", "",
    "... clientFile /w/b.ma", "... headRev 1", "... haveRev 1", "... action add", "",
    "... clientFile /w/c.ma", "... headRev 2", "... haveRev 2", "... action delete", "",
    "... clientFile /w/d.ma", "... headRev 5", "... haveRev 4", "",
    "... clientFile /w/e.ma", "... headRev 1", "... haveRev 1",
    "... otherOpen0 bob@ws", "... otherLock0 bob@ws", "... otherOpen 1", "",
    "... clientFile /w/f.ma", "... headRev 1", "... haveRev 1",
    "... otherOpen0 bob@ws", "... otherOpen 1", "",
    "... clientFile /w/g.ma", "... headRev 1", "... haveRev 1", "",
])
calls = []
def fake_run(cmd, cwd=None, timeout=None):
    calls.append(cmd)
    return 0, sample, ""
p4_provider.run = fake_run
orig_listdir = os.listdir
os.listdir = lambda d: ["a.ma", "b.ma", "c.ma", "d.ma", "e.ma", "f.ma", "g.ma", "new.ma", "sub"]
try:
    states = prov.fetch_status("/w", "/w")
finally:
    os.listdir = orig_listdir
n = lambda x: p4_provider.norm("/w/" + x)
assert states[n("a.ma")] == ST_MODIFIED and states[n("b.ma")] == ST_ADDED
assert states[n("c.ma")] == ST_DELETED and states[n("d.ma")] == ST_OUTDATED
assert states[n("e.ma")] == ST_LOCKED and states[n("f.ma")] == ST_OTHER_OPEN
assert states[n("g.ma")] == ST_CLEAN
assert states[n("new.ma")] == ST_UNTRACKED and states[n("sub")] == ST_UNTRACKED
assert "otherLock" in calls[0][calls[0].index("-T") + 1]
print("p4 fstat parse (edit/add/delete/outdated/otherLock/otherOpen/clean/untracked): OK")

# 4) デリゲートが is_dir_of_index 無しでも落ちない（後方互換）
d = StatusBadgeDelegate(lambda i: "", None)
assert d._is_dir(None) is False
print("delegate backward-compat: OK")

# 5) r58: 事前チェック（他者ロック→不可 / 他者チェックアウト→確認）と結果通知
info_e = prov.file_info("/w/e.ma"); info_f = prov.file_info("/w/f.ma")
assert info_e and info_e["locked"] and info_e["locks"] == ["bob@ws"], info_e
assert info_f and not info_f["locked"] and info_f["others"] == ["bob@ws"], info_f
acts = prov.actions("/w", ["/w/e.ma"])
co = acts[0]
assert len(co) == 3 and co[2].get("enabled") is False and "bob@ws" in co[2]["tooltip"], co
acts = prov.actions("/w", ["/w/f.ma", "/w/g.ma"])
co = acts[0]
assert len(co) == 3 and "confirm" in co[2] and "bob@ws" in co[2]["confirm"], co
acts = prov.actions("/w", ["/w/g.ma"])
assert len(acts[0]) == 2, acts[0]            # 問題なし → 確認なし
assert any(len(a) == 3 and "confirm" in a[2] and "revert" in a[0].lower()
           for a in acts), "revert には確認が必要"
assert prov.cli_output_is_error("//depot/a.ma - already locked by bob@ws")
assert prov.cli_output_is_error("Can't edit exclusive file already opened")
assert not prov.cli_output_is_error("//depot/error_tex.png#1 - opened for edit")
print("p4 precheck (locked→disabled / other-open→confirm / revert→confirm): OK")

# 結果通知: rc=0 でもエラー文なら失敗として report される
from core.integrations import base as _base
reports = []
prov.report = lambda ok, msg: reports.append((ok, msg))
_base.run = lambda cmd, cwd=None, timeout=None, input_text=None: (
    0, "//depot/x.ma - already locked by bob@ws", "")
cb = prov._cli(["edit", "/w/x.ma"], "/w", "チェックアウト")
cb()
import time as _t
for _ in range(50):
    if reports:
        break
    _t.sleep(0.02)
assert reports and reports[0][0] is False and "already locked" in reports[0][1], reports
reports.clear()
_base.run = lambda cmd, cwd=None, timeout=None, input_text=None: (
    0, "//depot/x.ma#3 - opened for edit", "")
prov._cli(["edit", "/w/x.ma"], "/w", "チェックアウト")()
for _ in range(50):
    if reports:
        break
    _t.sleep(0.02)
assert reports and reports[0][0] is True and "opened for edit" in reports[0][1], reports
print("p4 CLI result reporting (fail on p4 error text / success): OK")
finish()
