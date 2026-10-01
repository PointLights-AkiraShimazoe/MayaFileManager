# -*- coding: utf-8 -*-
"""r95: プリセット毎に «最後に表示していたディレクトリ» を覚え、切替時に復元する。

- エリア毎に独立（エリアA の記憶がエリアB を動かさない）
- 🔗リンクONで他エリアも切り替わった時、そのエリア自身の記憶で移動する
- 状態保存（get_state / apply_state）で再起動後も残る
"""
import os
from _common import *  # noqa: F401,F403
from _common import app, sm, tmpdir, finish, run, QTest
from ui.main_window import MainWindow

sm.save_quick_nav_presets({"default": [{"label": "A", "path": "/tmp"}],
                           "MM": [{"label": "B", "path": "/tmp"}]})
root = tmpdir()
DIRS = {}
for n in ("a_def", "a_mm", "b_def", "b_mm"):
    DIRS[n] = os.path.join(root, n)
    os.makedirs(DIRS[n])

w = MainWindow(sm); w.resize(1400, 900); w.show()
while len(w._areas) < 2:
    w._add_area()


def _settle(ms=400):
    """navigate_to は非同期（カラム生成・モデル読み込み）なので落ち着かせる。"""
    QTest.qWait(ms)
    app.processEvents()


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def s1():
    a0, a1 = w._areas[0], w._areas[1]
    for a in (a0, a1):
        a.quick_nav.set_active_preset("default", notify=False)
        a.quick_nav._link_btn.setChecked(False)

    # default に居る状態でそれぞれ別のディレクトリへ
    a0.browser.navigate_to(DIRS["a_def"]); a1.browser.navigate_to(DIRS["b_def"])
    _settle()

    # エリア0 を MM へ → 記憶が無いので現在地のまま。そこで a_mm へ移動
    a0.quick_nav.set_active_preset("MM")
    _settle()
    a0.browser.navigate_to(DIRS["a_mm"]); _settle()

    # エリア1 は影響を受けていない（リンクOFF＝エリア毎に独立）
    assert _same(a1.browser.current_path(), DIRS["b_def"]), \
        ("他エリアが巻き込まれた", a1.browser.current_path())

    # エリア0 を default へ戻す → a_def が復元されること
    a0.quick_nav.set_active_preset("default")
    _settle()
    assert _same(a0.browser.current_path(), DIRS["a_def"]), \
        ("default の記憶が復元されない", a0.browser.current_path())

    # もう一度 MM → a_mm が復元されること
    a0.quick_nav.set_active_preset("MM")
    _settle()
    assert _same(a0.browser.current_path(), DIRS["a_mm"]), \
        ("MM の記憶が復元されない", a0.browser.current_path())
    print("per-area / per-preset directory memory: OK")

    # ── 状態保存で残る（再起動相当） ─────────────────────────────
    st = a0.get_state()
    assert st.get("preset_paths", {}).get("MM"), ("保存に含まれていない", st)
    a1.apply_state(st)
    _settle()
    # 復元直後は保存された path（=a_mm）。preset 切替では上書きしない
    assert _same(a1.browser.current_path(), DIRS["a_mm"]), \
        ("復元時の path が別物", a1.browser.current_path())
    a1.quick_nav.set_active_preset("default")
    _settle()
    assert _same(a1.browser.current_path(), DIRS["a_def"]), \
        ("保存された記憶から復元されない", a1.browser.current_path())
    print("state round-trip keeps per-preset dirs: OK")

    # ── 🔗リンクON: 連動して切り替わったエリアも «自分の記憶» で移動する ──
    for a in (a0, a1):
        a.quick_nav.set_active_preset("default", notify=False)
    a0.browser.navigate_to(DIRS["a_def"]); a1.browser.navigate_to(DIRS["b_def"])
    _settle()
    a0._preset_paths["MM"] = DIRS["a_mm"]
    a1._preset_paths["MM"] = DIRS["b_mm"]
    a0.quick_nav._link_btn.setChecked(True)      # リンクON（グローバル設定）
    _settle(120)
    a0.quick_nav._preset_combo.setCurrentText("MM")
    _settle()
    assert _same(a0.browser.current_path(), DIRS["a_mm"]), \
        ("リンクON: 操作したエリアが違う", a0.browser.current_path())
    assert _same(a1.browser.current_path(), DIRS["b_mm"]), \
        ("リンクON: 連動先が自分の記憶で移動していない", a1.browser.current_path())
    print("linked areas each restore their own dir: OK")
    finish(True)


run(s1, delay=900)
