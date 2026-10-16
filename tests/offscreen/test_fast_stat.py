# -*- coding: utf-8 -*-
"""r146: core.fast_stat — UI スレッドで待たない stat。

実機 2026-10-16: 外付け SSD へのジャンクション配下で os.path.isdir() 1 回が
10〜20 秒かかり、右クリック・削除前の手放し・見張りの付け直しのたびに
UI が固まった。猶予（50ms）だけ待って、間に合わなければ仮の答えを返す。
"""
import os
import time
import tempfile
from _common import *  # noqa: F401,F403
from _common import finish
from core import fast_stat as fs

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


d = tempfile.mkdtemp(prefix="mfm_fs_")
sub = os.path.join(d, "folder.v1")       # ドットを含むフォルダ（仮の答えが外れる例）
os.makedirs(sub)
f = os.path.join(d, "scene.ma")
open(f, "w").close()

# ── 1) 速いボリューム: 本当の答え ─────────────────────────────
check(fs.isdir(sub) is True and fs.isdir(f) is False, "速ければ本当の答え（dir/file）")
check(fs.isfile(f) is True and fs.exists(f) is True, "isfile / exists")
check(fs.exists(os.path.join(d, "nope")) is False, "無いものは無い")

# ── 2) 遅いボリュームの真似: 1 回の stat に 1.5 秒 ─────────────
real = fs._stat_kind
fs._stat_kind = lambda p: (time.sleep(1.5), real(p))[1]
fs.clear()
slow = os.path.join(d, "slow_dir.v2")
os.makedirs(slow)
t0 = time.monotonic()
a = fs.isdir(slow)
dt = time.monotonic() - t0
check(dt < 0.5, "UI スレッドは待たされない（%.3f 秒）" % dt)
check(a is False, "間に合わなければ仮の答え（拡張子があるのでファイル扱い）")
time.sleep(2.0)
t0 = time.monotonic()
b = fs.isdir(slow)
check(b is True and time.monotonic() - t0 < 0.1, "裏で取れた本当の答えが次から効く（待たない）")
# 同じパスを連打しても問い合わせは 1 本
fs.clear()
t0 = time.monotonic()
for _ in range(20):
    fs.isdir(slow)
check(time.monotonic() - t0 < 0.2, "連打しても 1 回分しか待たない（%.2f 秒）" % (time.monotonic() - t0))
fs._stat_kind = real

# ── 3) invalidate ─────────────────────────────────────────────
time.sleep(2.0)
fs.clear()
check(fs.isdir(slow) is True, "前提")
os.rmdir(slow)
check(fs.isdir(slow) is True, "TTL 内は古い答え（キャッシュ）")
fs.invalidate(slow)
check(fs.isdir(slow) is False, "invalidate 後は取り直す")

# ── 4) 切り分けスイッチ ────────────────────────────────────────
os.environ["MFM_DIAG"] = "no_fast_stat"
try:
    fs._stat_kind = lambda p: (time.sleep(0.3), real(p))[1]
    t0 = time.monotonic()
    fs.isdir(sub)
    check(time.monotonic() - t0 >= 0.25, "MFM_DIAG=no_fast_stat なら素の stat（待つ）")
finally:
    fs._stat_kind = real
    os.environ.pop("MFM_DIAG", None)

finish(not fails)
