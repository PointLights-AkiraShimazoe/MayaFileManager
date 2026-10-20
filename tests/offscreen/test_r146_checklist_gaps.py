# -*- coding: utf-8 -*-
"""r146: 実機チェックリストのうち、回帰テストが無かった項目を固定する。

ユーザー指示 2026-10-20:
  「実機チェックリストまで出したのに、過去のエラーが再発してるとなると、
    チェックリストが意味をなしません。過去のエラー修正の内容は全て直って
    いるとなるように修正し、その上で現在のチェックリストの内容が解消して
    いるのを全てチェックしてから報告してください。」

ここで固定する項目（docs/r146_実機チェックリスト.md の番号）:
  A-6  本体・道具窓のどれが手前でも、全部からマウスが離れたら畳む
       （道具窓の上にある間は畳まない）
  B-3  1 ファイルが長い時は回転する印が動き続ける
  C-1  削除 → 表示から即消える
  C-7  複製をキャンセルし、その後に削除 → エラーにならない
  E-2  その場編集中、別のウィンドウへ移った時だけ確定する
       （同じウィンドウの中で焦点が外れただけでは確定しない）
  E-3  ゆっくり 2 回クリックで編集に入る。編集中はもう一度クリックしても
       二重に入らない
  E-6  バッチリネーム後にカラムが作り直されない（同じカラムに新しい名前が出る）
"""
import os
import time

from _common import *  # noqa: F401,F403
from _common import app, sm, make_panel, tmpdir, finish, QTest, find_item, columns
from core.compat import (Qt, QWidget, QDialog, QLineEdit, QPoint, QRect,
                         QMessageBox, QProgressDialog, QApplication)

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def settle(ms=150):
    QTest.qWait(ms)
    app.processEvents()


# ═══════════════════════════════════════════════════════════════════
# A-6 道具窓の上では畳まない／全部から離れたら畳む
# ═══════════════════════════════════════════════════════════════════
os.environ["MFM_DIAG"] = "no_native_cursor"       # 座標判定は Qt の矩形で
from ui.compact_mode import CompactController
import ui.compact_mode as _cm


class _Win(QWidget):
    def sync_compact_button(self):
        pass

    def _log_quiet(self, *_a):
        pass

    def is_always_on_top(self):
        return False

    def save_window_geometry(self):
        pass


sm.set(CompactController.ACTIVE_KEY, False, save=False)
sm.set(CompactController.PUCK_POS_KEY, None, save=False)
win = _Win()
win.setGeometry(100, 100, 300, 200)
win.show()
settle()
dlg = QDialog(win)                     # 設定ダイアログ相当（本体の子・別の最上位窓）
dlg.setGeometry(700, 500, 200, 120)
dlg.show()
settle()
ctl = CompactController(win, sm)
ctl._cursor_inside_orig = ctl._cursor_inside
_cm.QCursor.pos = staticmethod(lambda: QPoint(150, 150))   # 本体の上
ctl.set_active(True)
settle()
ctl._timer.stop()
ctl.expand()
settle()
check(ctl.is_expanded(), "前提: 開いている")
check(dlg.isVisible(), "前提: 道具窓も見えている")
# 道具窓の上
_cm.QCursor.pos = staticmethod(lambda: dlg.mapToGlobal(dlg.rect().center()))
ctl._tick()
settle()
check(ctl.is_expanded(), "A-6: 道具窓の上にある間は畳まない")
# 本体の上
_cm.QCursor.pos = staticmethod(lambda: win.mapToGlobal(win.rect().center()))
ctl._tick()
settle()
check(ctl.is_expanded(), "A-6: 本体の上にある間は畳まない")
# 全部の外
_cm.QCursor.pos = staticmethod(lambda: QPoint(-3000, -3000))
ctl._tick()
settle()
check(not ctl.is_expanded() and ctl.is_parked(), "A-6: 本体・道具窓の全部から離れたら畳む")
check(not dlg.isVisible(), "A-6: 畳むと道具窓も一緒に消える")
# ── A-2 / A-3 つまみ側に乗せても開かない／つまみはドラッグできる ────
puck = ctl._puck
check(puck is not None and puck.isVisible(), "前提: パックが出ている")
if puck is not None:
    opened = []
    ctl.expand = lambda *a, **k: opened.append(True)
    _cm.QCursor.pos = staticmethod(lambda: puck.grip.mapToGlobal(puck.grip.rect().center()))
    ctl._tick(); ctl._tick()
    QTest.qWait(puck.btn.HOVER_OPEN_MS + 150)
    app.processEvents()
    check(not opened and not puck.btn._timer.isActive(), "A-2: つまみ側に乗せただけでは開かない")
    _cm.QCursor.pos = staticmethod(lambda: puck.btn.mapToGlobal(puck.btn.rect().center()))
    ctl._tick()
    QTest.qWait(puck.btn.HOVER_OPEN_MS + 150)
    app.processEvents()
    check(bool(opened), "A-2: アイコン側に乗せれば開く")
    del ctl.expand
    # つまみのドラッグでパックが動く（開かない）
    from core.compat import QtGui, QtCore
    opened[:] = []
    ctl.expand = lambda *a, **k: opened.append(True)
    p0 = QPoint(puck.pos())
    g = puck.grip
    c = g.rect().center()
    gp = g.mapToGlobal(c)
    press = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(c), QtCore.QPointF(gp),
                              Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    g.mousePressEvent(press)
    move = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(c.x() + 40, c.y() + 25),
                             QtCore.QPointF(gp.x() + 40, gp.y() + 25), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    g.mouseMoveEvent(move)
    rel = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(c.x() + 40, c.y() + 25),
                            QtCore.QPointF(gp.x() + 40, gp.y() + 25), Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    g.mouseReleaseEvent(rel)
    settle()
    check(puck.pos() != p0, "A-3: つまみのドラッグでパックが動く（%r → %r）" % ((p0.x(), p0.y()), (puck.pos().x(), puck.pos().y())))
    check(not opened, "A-3: つまみのドラッグでは開かない")
    del ctl.expand
ctl.set_active(False)
settle()
os.environ.pop("MFM_DIAG", None)

# ═══════════════════════════════════════════════════════════════════
# パネル（C-1 / C-7 / E-2 / E-3 / E-6 / B-3）
# ═══════════════════════════════════════════════════════════════════
d = tmpdir()
os.makedirs(os.path.join(d, "sub_a"))
for n in ("del_me.ma", "dup_src.ma", "ed_1.ma", "ed_2.ma", "br_1.ma", "br_2.ma", "slow.ma"):
    open(os.path.join(d, n), "w").close()

b = make_panel(1300, 700)
b.navigate_to(d)
settle(700)
check(find_item(b, "del_me.ma")[0] is not None, "前提: カラムに項目が出ている")

# ── C-1 削除 → 表示から即消える ───────────────────────────────────
_orig_warn = QMessageBox.warning
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Yes)
crit = []
_orig_crit = QMessageBox.critical
QMessageBox.critical = staticmethod(lambda *a, **k: (crit.append(a[2] if len(a) > 2 else a), QMessageBox.Ok)[1])
try:
    t0 = time.monotonic()
    b._delete_confirm([os.path.join(d, "del_me.ma")])
    gone_at = None
    for _ in range(40):                     # 最大 4 秒
        settle(100)
        if not os.path.exists(os.path.join(d, "del_me.ma")) and find_item(b, "del_me.ma")[0] is None:
            gone_at = time.monotonic() - t0
            break
    check(gone_at is not None, "C-1: 削除した項目が表示から消える")
    check(gone_at is not None and gone_at < 3.0, "C-1: 消えるまで 3 秒以内（%s）" % (gone_at,))
    check(not crit, "C-1: エラーが出ない（%r）" % (crit,))

    # ── C-7 複製をキャンセルし、その後に削除 → エラーにならない ──────
    src = os.path.join(d, "dup_src.ma")
    state = {"cancelled": None, "err": None}

    def work(cb):
        for i in range(50):
            cb(i, 50)                       # キャンセル済みなら _OpCancelled
            time.sleep(0.05)
        return "done"

    def on_done(res, cancelled, err):
        state["cancelled"], state["err"] = cancelled, err

    b._run_file_op("複製", work, on_done, total=50, busy_paths=[src])
    settle(600)
    dlgs = [x for x in b.findChildren(QProgressDialog) if x.isVisible() and x.windowTitle() == "複製"]
    check(dlgs, "C-7: 進捗ダイアログが出る")
    if dlgs:
        # キャンセルボタンを押したのと同じ（ボタンは canceled を出してから cancel()）
        dlgs[0].canceled.emit()
        dlgs[0].cancel()
    for _ in range(30):
        settle(100)
        if state["cancelled"] is not None:
            break
    check(state["cancelled"] is True and not state["err"],
          "C-7: キャンセルは «キャンセル» として終わる（%r）" % (state,))
    crit[:] = []
    settle(500)
    b._delete_confirm([src])
    for _ in range(40):
        settle(100)
        if not os.path.exists(src):
            break
    check(not os.path.exists(src), "C-7: キャンセルの数秒後の削除が通る")
    check(not crit, "C-7: 削除がエラーにならない（%r）" % (crit,))

    # ── B-3 1 ファイルが長い時は回転する印が動き続ける ─────────────
    def slow_work(cb):
        cb(0, 1)
        time.sleep(1.6)                     # 1 件が長い
        cb(1, 1)
        return "ok"

    b._run_file_op("コピー", slow_work, lambda *a: None, total=1,
                   busy_paths=[os.path.join(d, "slow.ma")])
    settle(700)
    dlgs = [x for x in b.findChildren(QProgressDialog) if x.isVisible() and x.windowTitle() == "コピー"]
    check(dlgs, "B-3: 1 件が長くても 0.4 秒を過ぎたら進捗が出る")
    marks = set()
    if dlgs:
        for _ in range(5):
            try:
                marks.add(dlgs[0].labelText()[-1:])
            except RuntimeError:
                break
            settle(120)
    check(len(marks) >= 2, "B-3: 回転する印が動き続ける（%r）" % (marks,))
    settle(1200)
finally:
    QMessageBox.warning = _orig_warn
    QMessageBox.critical = _orig_crit


# ── E-2 / E-3 その場編集 ────────────────────────────────────────────
def editor():
    eds = [e for e in b.findChildren(QLineEdit) if e.isVisible()
           and e.objectName() == "mfmInlineRename"]
    return eds[0] if eds else None


def select_item(name):
    cv, rect, _ = find_item(b, name)
    assert cv is not None, name
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    settle(300)
    return cv, rect


# E-3: 選択済みの項目を «ゆっくり» もう一度クリック → 編集に入る
cv, rect = select_item("ed_1.ma")
interval = QApplication.doubleClickInterval()
QTest.qWait(interval + 150)                 # ダブルクリックにならない間
QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
for _ in range(20):
    settle(100)
    if editor() is not None:
        break
check(editor() is not None, "E-3: ゆっくり 2 回クリックで編集に入る")
ed1 = editor()
# 編集中にもう一度クリックしても二重に入らない（同じ編集欄のまま）
if ed1 is not None:
    QTest.keyClicks(ed1, "typed")
    QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    settle(600)
    eds = [e for e in b.findChildren(QLineEdit) if e.objectName() == "mfmInlineRename" and e.isVisible()]
    check(len(eds) <= 1, "E-3: 編集中にクリックしても編集欄は 1 つ（%d）" % len(eds))

# E-2: 同じウィンドウの中で焦点が外れただけでは確定しない
ed = editor()
if ed is None:
    select_item("ed_1.ma")
    b._rename_inline()
    settle(200)
    ed = editor()
check(ed is not None, "前提: 編集欄が開いている")
if ed is not None:
    ed.selectAll()
    QTest.keyClicks(ed, "same_win.ma")
    ed.editingFinished.emit()              # 焦点が外れた合図（同じウィンドウ内）
    settle(300)
    check(editor() is not None and os.path.exists(os.path.join(d, "ed_1.ma")),
          "E-2: 同じウィンドウ内で焦点が外れただけでは確定しない")
    # 別のウィンドウへ移った → 確定
    other = QWidget()
    other.resize(200, 100)
    other.show()
    other.activateWindow()
    settle(200)
    QApplication.setActiveWindow(other)
    settle(100)
    if QApplication.activeWindow() is not other:
        # オフスクリーンで活性化が効かない時は、判定だけ同じにする
        _orig_aw = QApplication.activeWindow
        QApplication.activeWindow = staticmethod(lambda: other)
    else:
        _orig_aw = None
    try:
        ed.editingFinished.emit()
        settle(500)
    finally:
        if _orig_aw is not None:
            QApplication.activeWindow = _orig_aw
    check(os.path.exists(os.path.join(d, "same_win.ma")) and not os.path.exists(os.path.join(d, "ed_1.ma")),
          "E-2: 別のウィンドウへ移った時は確定する（%r）" % sorted(os.listdir(d)))
    check(editor() is None, "E-2: 確定後は編集欄が残らない")
    other.close()

# ── E-6 バッチリネーム後にカラムが作り直されない ──────────────────
settle(300)
before = [v for v in columns(b)]
os.rename(os.path.join(d, "br_1.ma"), os.path.join(d, "br_1_renamed.ma"))
os.rename(os.path.join(d, "br_2.ma"), os.path.join(d, "br_2_renamed.ma"))
b._resync_dir(d)                           # _on_batch_renamed がやること
seen = False
for _ in range(40):
    settle(100)
    if find_item(b, "br_1_renamed.ma")[0] is not None and find_item(b, "br_1.ma")[0] is None:
        seen = True
        break
check(seen, "E-6: 新しい名前が出て古い名前が消える")
after = [v for v in columns(b)]
same = [v for v in before if any(v is w for w in after)]
check(len(before) > 0 and len(same) == len(before),
      "E-6: カラムが作り直されない（%d/%d が同じまま）" % (len(same), len(before)))

finish(not fails)
