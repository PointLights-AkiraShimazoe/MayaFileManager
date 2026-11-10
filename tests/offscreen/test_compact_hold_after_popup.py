# -*- coding: utf-8 -*-
"""r146: メニュー／ダイアログが閉じた時にカーソルが外なら、中へ戻るまで畳まない。

ユーザー報告 2026-11-10:
  「Perforce のチェックアウト等の操作直後や、プロパティを開いた直後に UI から
    マウスが外れているとコンパクトに閉じられてしまうので、UI 外にでてしまう
    操作の場合は、一度 UI にマウスが戻ったら、その後マウスが外れて始めて
    コンパクトになるとしてください。」
  「この挙動のみが変わる様にしてください。」

原因: 右クリックメニューは本体の枠の外まで広がる。枠の外の項目を押すと、
メニューが閉じた瞬間にカーソルは «外» にいるので、次の見張りで畳まれていた。

確かめること:
  * メニュー（ポップアップ）が閉じた時に外 → 畳まない。何度見張っても畳まない
  * 中へ戻ったら旗が下りる → その後外へ出れば従来どおり畳む
  * モーダルダイアログが閉じた時に外 → 同じ
  * 閉じた直後の見張りがボタン押下（_busy）に当たっても取りこぼさない
  * メニューが閉じた時に中なら旗は立たない（その後外へ出れば畳む）
  * メニューを使っていなければ、外へ出たら従来どおり即座に畳む
  * 畳んだら旗は持ち越さない
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest
from core.compat import QWidget, QMenu, QDialog
from ui.compact_mode import CompactController

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


class _Framed(QWidget):
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
win = _Framed()
win.setGeometry(150, 150, 300, 200)
win.show()
QTest.qWait(80)
app.processEvents()

ctl = CompactController(win, sm)
ctl.set_active(True)
QTest.qWait(80)
app.processEvents()
ctl._timer.stop()             # 見張りは手で回す
ctl._return_foreground = lambda: None


def reopen():
    ctl._cursor_inside = lambda: True
    ctl.expand()
    QTest.qWait(60)
    app.processEvents()
    ctl._tick()               # 中にいる見張りを 1 回（旗を下ろす）


def inside(v):
    ctl._cursor_inside = (lambda: True) if v else (lambda: False)


reopen()
check(ctl.is_expanded(), "前提: 開いている")

# ── 1) メニューを開く → 外で閉じる → 畳まない ─────────────────────
menu = QMenu(win)
menu.addAction("dummy")
menu.popup(win.mapToGlobal(win.rect().bottomRight()))
QTest.qWait(60)
app.processEvents()
check(app.activePopupWidget() is not None, "前提: メニューが出ている")
inside(False)
ctl._tick()
check(ctl.is_expanded(), "メニューが出ている間は畳まない（従来どおり）")
menu.hide()
QTest.qWait(40)
app.processEvents()
check(app.activePopupWidget() is None, "前提: メニューが閉じた")
ctl._tick()
check(ctl.is_expanded(), "メニューが閉じた時にカーソルが外 → 畳まない")
ctl._tick(); ctl._tick(); ctl._tick()
check(ctl.is_expanded(), "外にいる間は何度見張っても畳まない")

# ── 2) 中へ戻る → 旗が下りる → 外へ出たら畳む ────────────────────
inside(True)
ctl._tick()
check(ctl.is_expanded() and not ctl._hold_until_reenter, "中へ戻ったら旗が下りる")
inside(False)
ctl._tick()
QTest.qWait(40)
app.processEvents()
check(not ctl.is_expanded(), "戻った後に外へ出れば従来どおり畳む")
check(not ctl._hold_until_reenter and not ctl._ui_seen, "畳んだら旗は持ち越さない")

# ── 3) モーダルダイアログ → 外で閉じる → 畳まない ─────────────────
reopen()
dlg = QDialog(win)
dlg.setModal(True)
dlg.open()
QTest.qWait(60)
app.processEvents()
check(app.activeModalWidget() is not None, "前提: モーダルが出ている")
inside(False)
ctl._tick()
check(ctl.is_expanded(), "モーダルが出ている間は畳まない（従来どおり）")
dlg.close()
QTest.qWait(40)
app.processEvents()
ctl._tick()
check(ctl.is_expanded(), "モーダルが閉じた時にカーソルが外 → 畳まない")
inside(True); ctl._tick()
inside(False); ctl._tick()
QTest.qWait(40); app.processEvents()
check(not ctl.is_expanded(), "モーダルの後も、戻ってから外へ出れば畳む")

# ── 4) 閉じた直後の見張りがボタン押下に当たっても取りこぼさない ───
reopen()
menu2 = QMenu(win)
menu2.addAction("dummy")
menu2.popup(win.mapToGlobal(win.rect().bottomRight()))
QTest.qWait(60); app.processEvents()
inside(False)
ctl._tick()
menu2.hide()
QTest.qWait(40); app.processEvents()
_orig_busy = ctl._busy
ctl._busy = lambda: True      # 項目を押したボタンがまだ離れていない
ctl._tick()
ctl._busy = _orig_busy
ctl._tick()
check(ctl.is_expanded(), "閉じた直後がボタン押下中でも、外なら畳まない")
inside(True); ctl._tick()

# ── 5) メニューが閉じた時に中なら旗は立たない ─────────────────────
menu3 = QMenu(win)
menu3.addAction("dummy")
menu3.popup(win.mapToGlobal(win.rect().center()))
QTest.qWait(60); app.processEvents()
inside(True)
ctl._tick()
menu3.hide()
QTest.qWait(40); app.processEvents()
ctl._tick()
check(not ctl._hold_until_reenter, "中で閉じたら旗は立たない")
inside(False); ctl._tick()
QTest.qWait(40); app.processEvents()
check(not ctl.is_expanded(), "中で閉じた後に外へ出れば従来どおり畳む")

# ── 6) メニューを使っていなければ従来どおり即座に畳む ──────────────
reopen()
inside(False)
ctl._tick()
QTest.qWait(40); app.processEvents()
check(not ctl.is_expanded(), "メニュー無しで外へ出たら即座に畳む（従来どおり）")

ctl.set_active(False)
finish(not fails)
