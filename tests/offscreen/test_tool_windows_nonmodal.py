# -*- coding: utf-8 -*-
"""r119: «Maya と行き来しながら使う» 道具窓はマネージャーを塞がない。

ユーザー指摘 2026-10-01:
  「Maya に適用で行うとすると、この UI を開いたまま運用することになりますが、
    その場合、この UI が開いていると、マネージャーが操作不能なのは
    使い勝手の面で悪いです。」

対象: リファレンスプリセットエディタ / リファレンスエディタ。
どちらも «適用しながら隣のフォルダを見る» 使い方になるため非モーダル。
多重に開かず、2 回目は前面に出すだけ。
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, run, Qt, ROOT

from ui.preset_editor import ReferencePresetEditor, preset_apply_code


def s1():
    from ui.main_window import MainWindow
    w = MainWindow(settings_manager=sm)
    app.processEvents()

    for attr, opener in (("_preset_editor_dlg", w._open_preset_editor),
                         ("_reference_editor_dlg", w._open_reference_editor)):
        opener()
        app.processEvents()
        dlg = getattr(w, attr)
        assert dlg is not None, attr
        assert not dlg.isModal(), "%s がモーダル（マネージャーを塞ぐ）" % attr
        assert dlg.windowModality() == Qt.NonModal, attr
        assert dlg.isVisible(), attr
        # 2 回目は «同じ窓» を前に出すだけ（多重に開かない）
        opener()
        app.processEvents()
        assert getattr(w, attr) is dlg, "%s が二重に開いた" % attr
        dlg.close()
        app.processEvents()
    print("tool windows are non-modal and open only once: OK")

    # ── 設定・バッチリネーム・クイックナビも止めない ────────────────
    w._open_settings()
    app.processEvents()
    d = w._settings_dlg
    assert d is not None and not d.isModal(), "設定がモーダル"
    d.close(); app.processEvents()
    print("the settings window does not block the manager: OK")

    # ── 適用コード: commandPort の «単一式» 制約を守ること ──────────
    preset = {
        "name": "t",
        "references": [{"path": "C:/a/b.ma", "namespace": "ns", "enabled": True}],
        "constraints": [{"type": "parentConstraint", "source_node": "a",
                         "target_node": "b", "enabled": True}],
        "scripts": [{"phase": "pre", "lang": "python",
                     "content": "print('hi')", "enabled": True}],
    }
    code = preset_apply_code(preset)
    import ast as _ast
    tree = _ast.parse(code, mode="eval")        # 式として成立すること
    assert isinstance(tree, _ast.Expression)
    assert "\\n" not in code.split("'")[0], "式が複数行になっている"
    # パスや日本語が壊れずに埋まること（JSON で渡している）
    assert "C:/a/b.ma" in code and "parentConstraint" in code
    jp = dict(preset); jp["name"] = "キャラA 組み立て"
    assert "キャラA 組み立て" in preset_apply_code(jp)
    print("apply code is a single expression and keeps paths / Japanese: OK")

    # 接続が無ければ «黙って何もしない» ではなく、送らずに知らせる
    dlg = ReferencePresetEditor(sm, bridge_cb=lambda: None)
    sent = []
    from core.compat import QMessageBox
    orig = QMessageBox.information
    QMessageBox.information = staticmethod(lambda *a, **k: sent.append(a))
    try:
        dlg._apply_preset()
    finally:
        QMessageBox.information = orig
    assert sent, "接続が無い時に何も知らせていない"
    dlg.deleteLater()
    print("applying with no connected Maya reports instead of doing nothing: OK")

    # ── 原則: 止める理由が «コードに書かれている» ものだけがモーダル ──
    # r119 のユーザー指示「原則 Manager を止めないようにしてください」。
    # 新しい exec_() がレビューを通らず混ざるのを防ぐ。
    import io as _io
    import re as _re
    ui_dir = os.path.join(ROOT, "ui")
    bad = []
    for fn in sorted(os.listdir(ui_dir)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(ui_dir, fn)
        lines = _io.open(path, encoding="utf-8", errors="replace").read().splitlines()
        for i, line in enumerate(lines):
            if not _re.search(r"\.exec_?\(\)", line):
                continue
            if "QMessageBox" in line or "box.exec" in line or "app.exec" in line \
                    or "drag.exec" in line or line.lstrip().startswith("#"):
                continue
            # 直前数行のコメントに «例外である理由» が書かれていること
            ctx = "\n".join(lines[max(0, i - 8):i])
            if "モーダルのまま" not in ctx:
                bad.append("%s:%d %s" % (fn, i + 1, line.strip()))
    assert not bad, (
        "Manager を止める理由がコードに書かれていないモーダル:\n" + "\n".join(bad))
    print("every remaining modal dialog states why it must block: OK")

    w.close()
    finish(True)


run(s1, delay=400)
