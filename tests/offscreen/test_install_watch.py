# -*- coding: utf-8 -*-
"""r146（ユーザー報告 2026-10-26）: Maya / Blender の一覧は «PC に入っているもの» が正。

  「新しく Maya 2022 を入れたが、リストに出てこない。Blender も同じく、
    インストールやアンインストールされた場合にリストと整合性が合わない。」

原因: (1) Maya の検出が 2023 未満を切り捨てていた。(2) 起動時に 1 回しか
調べていなかった。

確かめること（Manager を起動したまま、PC 側で入れる／消す）:
  * Maya 2022 が一覧に出る
  * Maya を入れる → 出る、消す → 消える。選んでいた版は選んだまま
  * 選んでいた版を消す → 残っている版に移る（消えた版を指したままにしない）
  * Blender も同じ
  * 何も変わっていなければ一覧を作り直さない
  * Manager がアクティブになった時にも調べ直す"""
import os
import shutil
import sys
from pathlib import Path
from _common import *  # noqa: F401,F403
from _common import app, sm, tmpdir, finish, QTest

from core.compat import Qt
import core.maya_version as _mv
import ui.main_window_dcc as _mwd
from core.blender_version import BlenderInstallation

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def settle(ms=100):
    QTest.qWait(ms)
    app.processEvents()


base = tmpdir()
adsk = os.path.join(base, "Autodesk")
EXE_REL = (os.path.join("bin", "maya.exe") if sys.platform == "win32"
           else os.path.join("bin", "maya"))


def install_maya(ver):
    p = os.path.join(adsk, "Maya%s" % ver, EXE_REL)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()


def uninstall_maya(ver):
    shutil.rmtree(os.path.join(adsk, "Maya%s" % ver))


bl_installed = {}


def install_blender(ver):
    exe = os.path.join(base, "Blender Foundation", "Blender %s" % ver, "blender.exe")
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    open(exe, "w").close()
    bl_installed[ver] = exe


def uninstall_blender(ver):
    shutil.rmtree(os.path.dirname(bl_installed.pop(ver)))


_mv.get_maya_install_roots = lambda: [Path(adsk)]
_mv._registry_maya_locations = lambda: []
_mwd.find_installed_blender_versions = lambda extra=None: [
    BlenderInstallation(v, Path(e)) for v, e in sorted(bl_installed.items())]

for v in ("2022", "2025"):
    install_maya(v)
install_blender("4.2")
install_blender("3.6")

# 0) 2022 も検出される（以前は 2023 未満を切り捨てていた）
vers = [i.version for i in _mv.find_installed_maya_versions()]
check(vers == ["2022", "2025"], "Maya 2022 も検出される: %r" % vers)

sm.save_maya_launch_profiles([])
from ui.main_window import MainWindow
w = MainWindow(settings_manager=sm)
w._install_timer.stop()          # 手で回す
settle(300)


def maya_items():
    mc = w._maya_combo
    return [mc.itemText(i) for i in range(mc.count())]


def bl_items():
    bc = w._blender_combo
    return [bc.itemText(i) for i in range(bc.count())]


def rescan(reason="test"):
    w._install_scan_last = 0.0
    w._rescan_installs_async(reason)
    for _ in range(50):
        settle(50)
        if not w._install_scan_running:
            break
    settle(150)


check(maya_items() == ["Maya 2025", "Maya 2022"], "起動時の一覧に 2022 がある: %r" % maya_items())

# 2025 を選んでおく
w._maya_combo.setCurrentIndex(maya_items().index("Maya 2025"))
settle()
check(w._maya_inst is not None and w._maya_inst.version == "2025", "前提: 2025 を選択")
w._blender_combo.setCurrentIndex(bl_items().index("Blender 3.6"))
settle()
check(w._blender_inst is not None and w._blender_inst.version == "3.6", "前提: Blender 3.6 を選択")

# 1) 何も変わっていなければ作り直さない
pops = []
_orig_pop = w._populate_version_combos
w._populate_version_combos = lambda: (pops.append(1), _orig_pop())[1]
rescan()
check(not pops, "変化が無ければ一覧を作り直さない")

# 2) Maya 2024 を入れる → 出る。選択は 2025 のまま
install_maya("2024")
rescan()
check(maya_items() == ["Maya 2025", "Maya 2024", "Maya 2022"], "入れた Maya が出る: %r" % maya_items())
check(w._maya_combo.currentText() == "Maya 2025" and w._maya_inst.version == "2025",
      "選んでいた版は選んだまま: %r" % w._maya_combo.currentText())

# 3) 選んでいない 2022 を消す → 消える
uninstall_maya("2022")
rescan()
check(maya_items() == ["Maya 2025", "Maya 2024"], "消した Maya が消える: %r" % maya_items())
check(w._maya_inst.version == "2025", "選択は変わらない")

# 4) 選んでいた 2025 を消す → 残っている版に移る
uninstall_maya("2025")
rescan()
check(maya_items() == ["Maya 2024"], "選んでいた版を消すと一覧から消える: %r" % maya_items())
check(w._maya_inst is not None and w._maya_inst.version == "2024",
      "消えた版を指したままにしない: %r" % (w._maya_inst,))

# 5) Blender: 入れる → 出る（選択は維持）、消す → 消える
install_blender("4.4")
rescan()
check(set(bl_items()) == {"Blender 4.4", "Blender 4.2", "Blender 3.6"}, "入れた Blender が出る: %r" % bl_items())
check(w._blender_inst is not None and w._blender_inst.version == "3.6", "Blender の選択は維持")
uninstall_blender("3.6")
rescan()
check("Blender 3.6" not in bl_items(), "消した Blender が消える: %r" % bl_items())
check(w._blender_inst is None or w._blender_inst.version != "3.6", "消えた Blender を指したままにしない")

# 6) Manager がアクティブになった時に調べ直す（別のアプリで入れて戻ってきた）
install_maya("2026")
w._install_scan_last = 0.0
w._on_app_state_for_installs(Qt.ApplicationActive)
for _ in range(50):
    settle(50)
    if not w._install_scan_running:
        break
settle(150)
check("Maya 2026" in maya_items(), "アクティブになった時に反映される: %r" % maya_items())

# 7) アクティブ化の連打では走らせすぎない（走った回数で数える。
#    偽の検出は一瞬で終わるので «走っている最中か» では判定できない）
runs = []
_orig_find = _mwd.find_installed_maya_versions
_mwd.find_installed_maya_versions = lambda *a, **k: (runs.append(1), _orig_find(*a, **k))[1]
w._install_scan_last = 0.0
w._on_app_state_for_installs(Qt.ApplicationActive)
for _ in range(50):
    settle(50)
    if not w._install_scan_running:
        break
first = len(runs)
w._on_app_state_for_installs(Qt.ApplicationActive)
for _ in range(10):
    settle(50)
check(first == 1 and len(runs) == 1, "%s 秒以内の再アクティブ化では走らない（%d → %d 回）"
      % (w.INSTALL_RESCAN_MIN_GAP, first, len(runs)))
_mwd.find_installed_maya_versions = _orig_find

# 8) 起動プロファイルを使っていても、プロファイルの無い版は一覧に出る
sm.save_maya_launch_profiles([{"label": "Maya 2024 (anim)", "version": "2024", "args": ""}])
w._populate_version_combos()
items = maya_items()
check(items[0] == "Maya 2024 (anim)" and "Maya 2026" in items,
      "プロファイルの無い版も出る: %r" % items)
sm.save_maya_launch_profiles([])

w.close()
finish(not fails)
