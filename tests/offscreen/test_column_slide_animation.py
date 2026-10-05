# -*- coding: utf-8 -*-
"""r120: カラムのスライドアニメーションを設定で切れること（ユーザー指示）。

QColumnView::scrollTo は水平スクロールを «アニメーションで» 動かす。
その間カラムが毎フレーム動き、見えている全セルが描き直されるので、
カラムやファイルが多いとカクつく（ユーザー報告 2026-10-02）。
切った時は目的のカラムへ一気に寄せる（滑らかさは無くなるが描画が要らない）。
"""
import os
from _common import *  # noqa: F401,F403
from _common import (app, make_panel, find_item_wait as find_item,
                     tmpdir, finish, run, Qt, QTest, sm)
from core.compat import QCheckBox

root = tmpdir()
p = root
for d in range(5):
    p = os.path.join(p, "L%d" % d)
    os.makedirs(p)
    for i in range(20):
        open(os.path.join(p, "f%02d.ma" % i), "w").close()

b = make_panel(900, 650)        # 幅を狭くして «必ず横スクロールが要る» 状態に
b.navigate_to(root)
cv = b._column_view


def _settle(ms=350):
    QTest.qWait(ms)
    app.processEvents()


def s1():
    # ── 1) 既定は ON（従来どおり滑らせる）─────────────────────────
    assert sm.get("column_slide_animation", True) is True
    assert cv.slide_animation() is True
    print("the slide animation is on by default: OK")

    # ── 2) 設定ダイアログに入切がある ─────────────────────────────
    # 【重要】_apply() は «ダイアログ上の全項目» を保存するので、スイート共有の
    # 設定（MFM_SETTINGS_ROOT）に対して行うと後続のテストを壊す（r63 の教訓。
    # 実際に test_preset_dirs を巻き込んだ）。この節だけ別の設定置き場を使う。
    import tempfile
    from core.settings_manager import SettingsManager
    saved_root = os.environ.get("MFM_SETTINGS_ROOT")
    os.environ["MFM_SETTINGS_ROOT"] = tempfile.mkdtemp(prefix="mfm_slide_cfg_")
    try:
        sm2 = SettingsManager()
        from ui.settings_dialog import SettingsDialog
        d = SettingsDialog(sm2)
        cbs = [w for w in d.findChildren(QCheckBox) if "カラム" in w.text()]
        assert any("滑ら" in w.text() for w in cbs), \
            ("スライドの入切が設定に無い", [w.text() for w in cbs])
        assert d._col_slide_cb.isChecked() is True, "既定（ON）が読めていない"
        d._col_slide_cb.setChecked(False)
        d._apply()
        _settle(200)
        assert sm2.get("column_slide_animation", True) is False, \
            "設定が保存されていない"
        # 読み直しても残っていること
        assert SettingsManager().get("column_slide_animation", True) is False
        d.deleteLater()
    finally:
        if saved_root is None:
            os.environ.pop("MFM_SETTINGS_ROOT", None)
        else:
            os.environ["MFM_SETTINGS_ROOT"] = saved_root
    print("the settings dialog exposes and saves the switch: OK")

    # ── 3) 切っても «次のカラム» は必ず出る（最重要）────────────────
    # 実機報告 2026-10-03:「Off にすると次のカラムが表示されなくなりました」
    # QColumnView::scrollTo は «スクロール» だけでなく «必要なカラムを作る»
    # 役目も持つ。自前のスクロールで済ませて super() を呼ばなかったため、
    # カラムが一本も増えなくなっていた。切り方はスタイルヒントに変更した。
    b.set_slide_animation(False)
    assert cv.slide_animation() is False
    from ui.browser_column_view import SH_ANIMATION_DURATION
    assert SH_ANIMATION_DURATION is not None, \
        "アニメーション時間のスタイルヒントが見つからない（切れない）"
    assert cv.style().styleHint(SH_ANIMATION_DURATION, None, cv) == 0, \
        "スタイルヒントが 0 になっていない（アニメーションが切れていない）"

    def _has_column_for(path):
        """そのフォルダの «中身» を出しているカラムがあるか。"""
        want = os.path.normcase(os.path.abspath(path))
        for c in cv._live_columns():
            try:
                got = cv._path_for_index(c.rootIndex())
                if got and os.path.normcase(os.path.abspath(got)) == want:
                    return True
            except RuntimeError:
                continue
        return False

    cur = root
    for d in range(5):
        cur = os.path.join(cur, "L%d" % d)
        cvw, _r2, idx2 = find_item(b, "L%d" % d)
        assert cvw is not None, ("切った状態で L%d が出てこない" % d)
        cvw.setCurrentIndex(idx2)
        cvw.clicked.emit(idx2)
        ok = False
        for _w in range(30):
            _settle(100)
            if _has_column_for(cur):
                ok = True
                break
        assert ok, ("フォルダを開いても «次のカラム» が出ない", cur,
                    [cv._path_for_index(c.rootIndex())
                     for c in cv._live_columns()])
    print("with the animation off each click still opens the next column: OK")

    # 一番深いカラムが画面内に入っていること
    hbar = cv.horizontalScrollBar()
    assert hbar.maximum() > 0, "横スクロールが要る状態になっていない"
    cols = cv._column_views_sorted()
    assert cols[-1].x() < cv.viewport().width(), \
        ("一番深いカラムが画面外のまま", cols[-1].x(), cv.viewport().width())
    print("the deepest column is brought into view: OK")

    # ── 4) 入切はその場で効く（再起動不要）────────────────────────
    b.set_slide_animation(True)
    assert cv.slide_animation() is True
    assert cv.style().styleHint(SH_ANIMATION_DURATION, None, cv) != 0, \
        "ON に戻してもアニメーションが切れたまま"
    assert sm.get("column_slide_animation", False) is True
    b.set_slide_animation(False)
    assert cv.slide_animation() is False
    print("the switch takes effect immediately, both ways: OK")

    # ── 5) 切っていても «ファイル» のクリックでは横に動かさない ─────
    # r67 からの約束: 操作対象であるファイルを選んだ時に列が動くと
    # 的が逃げる。アニメーションの入切とは独立に守られること。
    def _find_file_index():
        for fcol in cv._live_columns():
            try:
                m = fcol.model()
                froot = fcol.rootIndex()
                if not froot.isValid():
                    continue
                for r in range(m.rowCount(froot)):
                    i = m.index(r, 0, froot)
                    if str(i.data() or "").endswith(".ma"):
                        return i
            except RuntimeError:
                continue
        return None

    fidx = None
    for _try in range(40):
        fidx = _find_file_index()
        if fidx is not None:
            break
        _settle(100)
    assert fidx is not None and fidx.isValid(), "ファイルが見つからない"
    hv = hbar.value()
    cv.scrollTo(fidx)
    _settle(150)
    assert hbar.value() == hv, "ファイルのクリックで横に動いている"
    print("clicking a file never moves the columns sideways: OK")

    # 共有設定を元に戻す（後続テストへ持ち越さない）
    b.set_slide_animation(True)
    finish(True)


run(s1, delay=900)
