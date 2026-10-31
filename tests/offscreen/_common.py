# -*- coding: utf-8 -*-
"""オフスクリーンテスト共通: QApplication 準備・BrowserPanel 生成・カラム探索。"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# r146（2026-10-31）: テストが作った一時フォルダは終わったら消す。
# 消していなかったので、Windows の Temp にテストの残骸が 1,800 個以上
# 溜まり（全スイート 1 回で約 300 個）、Temp を読むだけで Qt の収集スレッドが
# 20 秒以上ふさがった。その間はどのフォルダの変化もモデルに届かず、
# test_fs_churn_guard が «書き込み後も一覧が 0 件» で落ちていた。
_TMP_MADE = []


def _mk_tmp(prefix="mfm_tmp_", parent=None):
    d = tempfile.mkdtemp(prefix=prefix, dir=parent)
    _TMP_MADE.append(d)
    return d


def _cleanup_tmp():
    import shutil
    import stat as _stat

    def _onerr(func, path, _exc):
        try:
            os.chmod(path, _stat.S_IWRITE)      # 読み取り専用にしたファイル
            func(path)
        except OSError:
            pass
    for d in reversed(_TMP_MADE):
        try:
            shutil.rmtree(d, onerror=_onerr)
        except Exception:
            pass


import atexit as _atexit
_atexit.register(_cleanup_tmp)
# 【重要】テストは本番設定（~/.maya_file_manager）に触らない（r63）。
# 以前は実設定を共有していたため、test_presets_and_integrations の
# save_quick_nav_presets がユーザーのプリセットを上書きして消した（2026-09-11）。
# SettingsManager は MFM_SETTINGS_ROOT を最優先で参照する。
os.environ["MFM_SETTINGS_ROOT"] = _mk_tmp("mfm_test_settings_")
# r146: Maya の設定フォルダも隔離する。無いと «開発機の本物の userSetup.py» を
# 読んでしまい、そこに旧方式のブロックが残っていると MainWindow が 3.5 秒後に
# 移行ダイアログ（モーダル）を出してテストが固まる（2026-10-24 の全スイートで
# 3 本が TIMEOUT）。テストが実機の Maya 設定に依存してはいけない。
os.environ["MAYA_APP_DIR"] = _mk_tmp("mfm_test_maya_appdir_")

# r146: ACCESS_VIOLATION（0xC0000005）で «出力なし» のまま落ちるテストが
# 間欠的に出る（2026-10-14）。素の mayapy は落ちた場所を何も残さないので、
# faulthandler で C 側のクラッシュ時にスタックを stderr へ吐かせる。
# 回帰スイートは stderr も拾うので、落ちた行がログに残る。
try:
    import faulthandler as _fh
    _fh.enable(file=sys.stderr, all_threads=True)
except Exception:
    pass

from core.compat import QApplication, QTimer, Qt, QListView, QPoint  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
from core.theme_engine import apply_theme  # noqa: E402
apply_theme(app, "dark")
from core import i18n  # noqa: E402
from core.settings_manager import SettingsManager  # noqa: E402
sm = SettingsManager()
sm.set("single_click_action", "none", save=False)
sm.set("column_widths", None, save=False)
# テストは日本語文言で判定する（ユーザー設定の ui_language に依存させない。
# mayapy ではロケール判定が en になり得る）
sm.set("ui_language", "ja", save=False)
i18n.init(sm)
from core.thumbnail_generator import ThumbnailManager  # noqa: E402
from ui.browser_panel import BrowserPanel, _safe_file_path  # noqa: E402,F401


# ---------------------------------------------------------------------------
# QTest 互換シム（r62 / r124 で «部分的にしか無い» 場合にも対応）
#
# - Maya 同梱の PySide6 には QtTest モジュールが «無い» ことがある（r62）。
# - Maya 2027（PySide6 6.8.3）は QtTest を持っているが、**QTest.qWait が無い**。
#   実機の回帰スイートが全部 TIMEOUT になっていた原因
#   （AttributeError: type object 'QTest' has no attribute 'qWait'、2026-10-06）。
#
# そこで «本物があれば本物、無い分だけ自前» という組み方にする。
# 丸ごと有る／無いの二択にすると、今回のような «一部だけ欠けている» 環境で
# また同じ止まり方をする。
# ---------------------------------------------------------------------------
from core.compat import QtCore as _QtCore, QtGui as _QtGui  # noqa: E402
import time as _time_mod  # noqa: E402

try:
    from PySide6.QtTest import QTest as _QTestReal
except Exception:
    try:
        from PySide2.QtTest import QTest as _QTestReal   # noqa: F401
    except Exception:
        _QTestReal = None


class _QTestFallback:
    """本物に無いものだけを補う最小実装。"""

    @staticmethod
    def qWait(ms):
        """ms ミリ秒、イベントを回しながら待つ（本物の QTest::qWait と同じ作り）。

        **sendPostedEvents(DeferredDelete) を忘れないこと。** processEvents だけ
        では deleteLater で予約された破棄が «一切» 処理されない（本物の qWait は
        毎周回でこれを流している）。忘れると、捨てたはずのウィジェットが残って
        いるように見えて、実機だけテストが落ちる（2026-10-06）。
        """
        end = _time_mod.monotonic() + (ms / 1000.0)
        while True:
            QApplication.processEvents()
            QApplication.sendPostedEvents(None, _QtCore.QEvent.DeferredDelete)
            if _time_mod.monotonic() >= end:
                break
            _time_mod.sleep(0.005)
        QApplication.processEvents()
        QApplication.sendPostedEvents(None, _QtCore.QEvent.DeferredDelete)

    @staticmethod
    def _mouse(widget, etype, button, modifiers, pos):
        if pos is None:
            pos = widget.rect().center()
        gpos = widget.mapToGlobal(pos)
        buttons = button if etype != _QtCore.QEvent.MouseButtonRelease else _QtCore.Qt.NoButton
        try:
            ev = _QtGui.QMouseEvent(etype, _QtCore.QPointF(pos), _QtCore.QPointF(gpos),
                                    button, buttons, modifiers)
        except TypeError:  # Qt5 系
            ev = _QtGui.QMouseEvent(etype, pos, gpos, button, buttons, modifiers)
        QApplication.sendEvent(widget, ev)
        QApplication.processEvents()

    @staticmethod
    def mousePress(widget, button, modifiers=_QtCore.Qt.NoModifier, pos=None, delay=-1):
        QTest._mouse(widget, _QtCore.QEvent.MouseButtonPress, button, modifiers, pos)

    @staticmethod
    def mouseRelease(widget, button, modifiers=_QtCore.Qt.NoModifier, pos=None, delay=-1):
        QTest._mouse(widget, _QtCore.QEvent.MouseButtonRelease, button, modifiers, pos)

    @staticmethod
    def mouseMove(widget, pos=None, delay=-1):
        QTest._mouse(widget, _QtCore.QEvent.MouseMove, _QtCore.Qt.NoButton,
                     _QtCore.Qt.NoModifier, pos)

    @staticmethod
    def mouseClick(widget, button, modifiers=_QtCore.Qt.NoModifier, pos=None, delay=-1):
        QTest.mousePress(widget, button, modifiers, pos)
        QTest.mouseRelease(widget, button, modifiers, pos)

    @staticmethod
    def mouseDClick(widget, button, modifiers=_QtCore.Qt.NoModifier, pos=None, delay=-1):
        # 実機と同じ順: press, release, dblclick, release
        QTest.mousePress(widget, button, modifiers, pos)
        QTest.mouseRelease(widget, button, modifiers, pos)
        QTest._mouse(widget, _QtCore.QEvent.MouseButtonDblClick, button, modifiers, pos)
        QTest.mouseRelease(widget, button, modifiers, pos)

    @staticmethod
    def keyClick(widget, key, modifiers=_QtCore.Qt.NoModifier, delay=-1):
        text = key if isinstance(key, str) else ""
        k = ord(key.upper()) if isinstance(key, str) else key
        for et in (_QtCore.QEvent.KeyPress, _QtCore.QEvent.KeyRelease):
            QApplication.sendEvent(widget, _QtGui.QKeyEvent(et, k, modifiers, text))
        QApplication.processEvents()

    @staticmethod
    def keyClicks(widget, text, modifiers=_QtCore.Qt.NoModifier, delay=-1):
        for ch in text:
            QTest.keyClick(widget, ch, modifiers)

    @staticmethod
    def _key(widget, et, key, modifiers):
        text = key if isinstance(key, str) else ""
        k = ord(key.upper()) if isinstance(key, str) else key
        QApplication.sendEvent(widget, _QtGui.QKeyEvent(et, k, modifiers, text))
        QApplication.processEvents()

    @staticmethod
    def keyPress(widget, key, modifiers=_QtCore.Qt.NoModifier, delay=-1):
        QTest._key(widget, _QtCore.QEvent.KeyPress, key, modifiers)

    @staticmethod
    def keyRelease(widget, key, modifiers=_QtCore.Qt.NoModifier, delay=-1):
        QTest._key(widget, _QtCore.QEvent.KeyRelease, key, modifiers)


def _qtest_pick(name):
    """本物にそのメソッドがあれば本物、無ければ自前。"""
    fn = getattr(_QTestReal, name, None) if _QTestReal is not None else None
    if fn is None:
        fn = getattr(_QTestFallback, name)
    return staticmethod(fn)


class QTest:   # noqa: N801
    qWait = _qtest_pick("qWait")
    _mouse = staticmethod(_QTestFallback._mouse)   # 自前専用（本物には無い）
    mousePress = _qtest_pick("mousePress")
    mouseRelease = _qtest_pick("mouseRelease")
    mouseMove = _qtest_pick("mouseMove")
    mouseClick = _qtest_pick("mouseClick")
    mouseDClick = _qtest_pick("mouseDClick")
    keyClick = _qtest_pick("keyClick")
    keyClicks = _qtest_pick("keyClicks")
    keyPress = _qtest_pick("keyPress")
    keyRelease = _qtest_pick("keyRelease")


def flush_deleted():
    """deleteLater で予約された破棄を «今» 片付ける（テスト用）。

    本物の qWait も自前の qWait もこれを流すが、processEvents だけで済ませて
    いる箇所から呼べるように単体でも出しておく。"""
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, _QtCore.QEvent.DeferredDelete)
    QApplication.processEvents()


def make_panel(w=1200, h=700):
    b = BrowserPanel(sm, ThumbnailManager(cache_size=16, thumb_size=64))
    b.resize(w, h)
    b.show()
    return b


def columns(b):
    return sorted([v for v in b._column_view.findChildren(QListView)
                   if getattr(v, "_mfm_header", None) is not None and v.isVisible()
                   and v.rootIndex().isValid()], key=lambda v: v.x())


def find_item(b, name):
    """名前の項目を持つ (column_view, visualRect, index) を返す。"""
    for cv in columns(b):
        m = cv.model()
        ri = cv.rootIndex()
        for r in range(m.rowCount(ri)):
            i = m.index(r, 0, ri)
            if i.data() == name:
                rect = cv.visualRect(i)
                if rect.isValid() and not rect.isEmpty():
                    return cv, rect, i
    return None, None, None


def find_item_wait(b, name, timeout_ms=4000):
    """項目が現れるまで待ってから find_item する。

    カラムの生成は非同期（QFileSystemModel のロード待ち）なので、固定の
    待ち時間だけに頼ると «マシンが混んでいる時だけ落ちる» 不安定テストになる。
    """
    waited = 0
    while waited < timeout_ms:
        cv, rect, idx = find_item(b, name)
        if cv is not None:
            return cv, rect, idx
        QTest.qWait(100)
        app.processEvents()
        waited += 100
    return None, None, None


def tmpdir(prefix="mfm_tmp_", parent=None):
    """テスト用の一時フォルダ（終わったら消える）。"""
    return _mk_tmp(prefix, parent)


def finish(ok=True):
    print("ALL OK" if ok else "FAILED")
    app.quit()


def run(first_step, delay=3000, timeout=40000):
    QTimer.singleShot(delay, first_step)
    QTimer.singleShot(timeout, lambda: (print("TIMEOUT"), app.quit()))
    app.exec()


# r146: BrowserPanel は責務ごとの Mixin に分かれた（ui/browser_*_mixin.py）。
# «browser_panel.py の原文を読んで検査する» テストは、本体だけでなく
# Mixin も含めて見る必要がある。ここでまとめて返す。
PANEL_FILES = ("ui/browser_panel.py",
               "ui/browser_fileops_mixin.py",
               "ui/browser_rename_mixin.py",
               "ui/browser_context_menu_mixin.py")


def panel_source() -> str:
    """BrowserPanel 本体と Mixin の原文を連結したもの。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = []
    for rel in PANEL_FILES:
        with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as f:
            out.append(f.read())
    return "\n".join(out)


def panel_ast():
    """本体と Mixin を 1 つの Module として parse したもの（関数検索用）。"""
    import ast as _ast
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    mod = _ast.Module(body=[], type_ignores=[])
    for rel in PANEL_FILES:
        with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as f:
            mod.body.extend(_ast.parse(f.read()).body)
    return mod
