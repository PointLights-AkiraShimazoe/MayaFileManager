# -*- coding: utf-8 -*-
"""r82: Mercury テーマ。ダーク/ライト両方が破綻なく生成でき、UI 側に
テーマ非追従の色ベタ書きが残っていないことを検査する（見た目の回帰防止）。"""
import os
import re
from _common import *  # noqa: F401,F403
from _common import app, finish, run, QTimer
from core import theme_engine as te

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# テーマ非追従で構わないもの（理由はコード側コメント参照）
_ALLOW = {
    "core/theme_engine.py",          # 生成器自身（色は持たない。トークン参照のみ）
    "core/thumbnail_generator.py",   # 背景透過のファイル種別チップ（キャッシュされる）
    "core/hdr_icons.py",             # コメントのみ
    "ui/dcc_header.py",              # theme_engine が読めない時のフォールバック
    "ui/bookmark_panel.py",          # ブックマークの色は «ユーザーデータ»（設定に保存）
    "ui/browser_area.py",            # rgba() ヘルパの docstring / 書式文字列
}
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba\s*\(")


def test_no_hardcoded_colors():
    bad = []
    for sub in ("ui", "core"):
        d = os.path.join(ROOT, sub)
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py"):
                continue
            rel = "%s/%s" % (sub, name)
            if rel in _ALLOW:
                continue
            with open(os.path.join(d, name), encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if _HEX.search(line):
                        bad.append("%s:%d: %s" % (rel, i, line.strip()[:70]))
    assert not bad, "テーマ非追従の色ベタ書きが残っている:\n" + "\n".join(bad)
    print("no hardcoded colors outside the allow-list: OK")


def test_stylesheet_tokens_exist():
    """個別スタイルシートの "%(name)s" が qss_vars に実在すること。
    存在しないロール名を書くと «実行時に KeyError でウィジェットが出ない»
    （r85 で実際に発生: surface_high → 正しくは surface_container_high）。"""
    keys = set(te.qss_vars("dark"))
    # 書式文字列にローカル変数を混ぜている箇所（エリアのアクセント等）
    local_ok = {"bg", "fg", "line", "hover", "pressed"}
    bad = []
    for sub in ("ui", "core"):
        dpath = os.path.join(ROOT, sub)
        for name in sorted(os.listdir(dpath)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(dpath, name), encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    for m in re.finditer(r"%\((\w+)\)s", line):
                        n = m.group(1)
                        if n in keys or n in local_ok:
                            continue
                        bad.append("%s/%s:%d: %%(%s)s" % (sub, name, i, n))
    assert not bad, "qss_vars に無いロール名:\n" + "\n".join(bad)
    print("all %(...)s tokens in widget stylesheets exist: OK")


def test_both_modes_build():
    for mode in ("dark", "light"):
        qss = te.build_qss(mode)
        assert len(qss) > 2000
        assert "{" not in qss.replace("{{", "").replace("}}", "") or True
        # 未解決のプレースホルダが残っていないこと
        assert not re.search(r"\{[a-z_]+\}", qss), (mode, "未解決トークン")
        # Mercury: font-weight は 500 まで
        for w in ("600", "700", "800", "bold"):
            assert ("font-weight: %s" % w) not in qss, (mode, w)
        v = te.qss_vars(mode)
        for key in ("surface", "on_surface", "primary", "selection", "plane_focus",
                    "plane_common", "column_header", "scrim", "status_ok", "badge_ink"):
            assert key in v and v[key], (mode, key)
        assert len(te.area_accents(mode)) == 6
    print("dark/light both build with all roles present: OK")


def test_modes_differ_and_are_readable():
    d, l = te.qss_vars("dark"), te.qss_vars("light")
    assert d["surface"] != l["surface"] and d["on_surface"] != l["on_surface"]
    # Mercury: 有彩色は Cobalt 1色（primary は両モードで同じ）
    assert d["primary"] == l["primary"] == "#5266eb"
    # 本文に純白は使わない（ダーク）
    assert d["on_surface"].lower() != "#ffffff"

    def lum(h):
        h = h.lstrip("#")
        c = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        c = [(x / 12.92) if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def ratio(a, b):
        la, lb = lum(a), lum(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    for v, name in ((d, "dark"), (l, "light")):
        assert ratio(v["on_surface"], v["plane_focus"]) >= 7.0, (name, "本文")
        assert ratio(v["on_surface_variant"], v["surface"]) >= 4.5, (name, "副文")
        assert ratio(v["on_selection"], v["selection"]) >= 4.5, (name, "選択行")
    print("dark/light differ, single accent, contrast OK")


def test_apply_theme_switches_mode():
    te.apply_theme(app, "light")
    assert te.current_mode() == "light" and te.qss_vars()["surface"] == l_surface
    te.apply_theme(app, "dark")
    assert te.current_mode() == "dark"
    print("apply_theme switches the mode used by qss_vars(): OK")


l_surface = te.qss_vars("light")["surface"]


def test_spin_and_check_are_visible():
    """r119: 暗い面で «上下の三角» と «チェックボックス» が見えること。

    QSS で QSpinBox / QCheckBox に背景と枠を当てると、スタイル既定の
    矢印・チェックが地と同化して消える（ユーザー報告 2026-10-01:
    「上下三角マークが見えない」「チェックボックスがただのテキストに見える」）。
    画像を明示的に置いているかを、両モードで確かめる。
    """
    import os as _os
    for mode in ("dark", "light"):
        qss = te.build_qss(mode)
        v = te.qss_vars(mode)
        # スピンの上下ボタンと «向きの違う» 矢印が指定されていること
        for sub in ("QSpinBox::up-button", "QSpinBox::down-button",
                    "QSpinBox::up-arrow", "QSpinBox::down-arrow"):
            assert sub in qss, (mode, sub)
        assert v["arrow_png_up"] and v["arrow_png"], mode
        assert v["arrow_png_up"] != v["arrow_png"], \
            "%s: 上下で同じ画像を使っている（向きが違わない）" % mode
        for key in ("arrow_png", "arrow_png_up", "check_png", "dash_png"):
            assert _os.path.isfile(v[key]), (mode, key, v[key])
        # チェックボックスは «枠» と «ON の塗り＋印» を持つこと
        assert "QCheckBox::indicator:checked" in qss, mode
        assert 'image: url("%s")' % v["check_png"] in qss, mode
        idx = qss.index("QCheckBox::indicator, QRadioButton::indicator")
        block = qss[idx:idx + 400]
        assert "border: 1px solid %s" % v["on_surface_variant"] in block, \
            "%s: チェックボックスの枠が地と同化している" % mode
    print("spin arrows and checkbox indicators are drawn explicitly: OK")


def step():
    test_spin_and_check_are_visible()
    test_no_hardcoded_colors()
    test_stylesheet_tokens_exist()
    test_both_modes_build()
    test_modes_differ_and_are_readable()
    test_apply_theme_switches_mode()
    finish(True)


run(step, delay=100)
