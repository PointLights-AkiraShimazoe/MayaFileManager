# -*- coding: utf-8 -*-
"""r146: コンパクトモードが «押した記憶が無いのに» 解除されない。

ユーザー報告 2026-10-21:
  「押した記憶が無いのにコンパクトモードが解除されている事があるので、
    ボタンを押す以外に解除が有り得るのかを調査してください。」

調査結果（解除の経路）:
  1. ヘッダのコンパクトボタン（意図した操作）
  2. パックの右クリック「通常表示に戻す」（意図した操作）
  3. **パックのアイコンをクリック** ← 乗せれば 250ms で開くのに、その前に
     クリック／アイコン側を掴んで動かそうとして離すと解除されていた
  4. ヘッダのトグルに焦点がある時の Space / Enter（キーボードで切り替わる）
  5. 安全弁: 畳んだのにパックが出せない／起動 3 秒でパックが見えない
     （本体を見失わないための最終手段）

固定すること:
  a) パックのアイコンのクリックは «開く»（解除しない）
  b) ヘッダのトグル（最前面／コンパクト／向き）はキーボードの焦点を持たない
  c) set_active の入／切は理由つきで mfm_compact.log に必ず残る
     （次に起きた時に «誰が切ったか» が分かる）
"""
import os

from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest
from core.compat import Qt, QWidget, QPoint
from ui.compact_mode import CompactController
import ui.compact_mode as _cm
from ui.dcc_header import LeadBlock

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


class _Win(QWidget):
    def sync_compact_button(self):
        pass

    def _log_quiet(self, *_a):
        pass

    def is_always_on_top(self):
        return False

    def save_window_geometry(self):
        pass


# ── c) ログ: 書き先を一時ファイルへ ──────────────────────────────
log_lines = []
_cm._compact_log = lambda msg: log_lines.append(msg)

sm.set(CompactController.ACTIVE_KEY, False, save=False)
sm.set(CompactController.PUCK_POS_KEY, None, save=False)
win = _Win()
win.setGeometry(120, 120, 300, 200)
win.show()
QTest.qWait(80); app.processEvents()
ctl = CompactController(win, sm)
_cm.QCursor.pos = staticmethod(lambda: QPoint(-4000, -4000))
ctl.set_active(True, reason="テスト")
QTest.qWait(80); app.processEvents()
ctl._timer.stop()
puck = ctl._puck
check(puck is not None and puck.isVisible(), "前提: 畳んでパックが出ている")

# ── a) アイコンのクリックは «開く»、解除しない ─────────────────────
puck.btn.click()
QTest.qWait(80); app.processEvents()
check(ctl.is_active(), "a) アイコンをクリックしてもコンパクトは解除されない")
check(ctl.is_expanded(), "a) アイコンのクリックで開く")
check("クリックで通常表示" not in puck.btn.toolTip(), "a) ツールチップも «クリックで解除» を言わない")

# ── c) 入／切のログ ───────────────────────────────────────────────
check(any("set_active(True)" in m and "テスト" in m for m in log_lines),
      "c) 入った記録が理由つきで残る（%r）" % (log_lines[-1:] ,))
ctl.set_active(False, reason="ヘッダのボタン")
check(any("set_active(False)" in m and "ヘッダのボタン" in m for m in log_lines),
      "c) 切った記録が理由つきで残る")
# 理由を渡さなくても呼び出し元が残る
log_lines[:] = []
ctl.set_active(True)
check(log_lines and "reason=" in log_lines[-1] and "test_compact_no_accidental_leave" in log_lines[-1],
      "c) 理由が無ければ呼び出し元を残す（%r）" % (log_lines[-1:],))
ctl.set_active(False, reason="テスト終了")

# 原文の検査: set_active(False) を理由なしで呼ぶ箇所が無い
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
for rel in ("ui/compact_mode.py", "ui/main_window.py"):
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    check("set_active(False)" not in src, "c) %s: 理由なしの set_active(False) が無い" % rel)

# ── b) ヘッダのトグルは焦点を持たない ─────────────────────────────
from core.compat import QLineEdit, QVBoxLayout
host = QWidget()
lay = QVBoxLayout(host)
lb = LeadBlock(host)
edit = QLineEdit(host)
lay.addWidget(lb)
lay.addWidget(edit)
host.show()
host.activateWindow()
QTest.qWait(80); app.processEvents()
for name in ("top_btn", "compact_btn", "dir_btn"):
    b = getattr(lb, name)
    check(b.focusPolicy() == Qt.NoFocus, "b) %s はキーボードの焦点を持たない" % name)
toggled = []
lb.compact_toggled.connect(lambda on: toggled.append(on))
edit.setFocus()
QTest.qWait(30); app.processEvents()
landed = []
for _ in range(6):                   # Tab で回しても、トグルには止まらない
    QTest.keyClick(app.focusWidget() or host, Qt.Key_Tab)
    app.processEvents()
    landed.append(app.focusWidget())
    QTest.keyClick(app.focusWidget() or host, Qt.Key_Space)
    app.processEvents()
check(not any(w in (lb.top_btn, lb.compact_btn, lb.dir_btn) for w in landed),
      "b) Tab で焦点を回してもトグルには止まらない")
check(not toggled, "b) 焦点の持ち主へ Space を送ってもコンパクトが切り替わらない（%r）" % (toggled,))

finish(not fails)
