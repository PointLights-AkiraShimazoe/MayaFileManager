# -*- coding: utf-8 -*-
"""オフスクリーンテスト共通: QApplication 準備・BrowserPanel 生成・カラム探索。"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 【重要】テストは本番設定（~/.maya_file_manager）に触らない（r63）。
# 以前は実設定を共有していたため、test_presets_and_integrations の
# save_quick_nav_presets がユーザーのプリセットを上書きして消した（2026-09-11）。
# SettingsManager は MFM_SETTINGS_ROOT を最優先で参照する。
os.environ["MFM_SETTINGS_ROOT"] = tempfile.mkdtemp(prefix="mfm_test_settings_")

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


def tmpdir():
    return tempfile.mkdtemp()


def finish(ok=True):
    print("ALL OK" if ok else "FAILED")
    app.quit()


def run(first_step, delay=3000, timeout=40000):
    QTimer.singleShot(delay, first_step)
    QTimer.singleShot(timeout, lambda: (print("TIMEOUT"), app.quit()))
    app.exec()
