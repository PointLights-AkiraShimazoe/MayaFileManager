# -*- coding: utf-8 -*-
"""r144: 平坦ビューのホイール速度。

ユーザー指摘 2026-10-08:
  「平坦ビューで多くのファイルが縦に並んだ際、リスト表示と違って
    スクロール速度が遅すぎるので、体感が同じくらいになるように」

件数が少ないうちは OS 設定どおり（＝カラムと同じ）。多くなるほど
1 ノッチの行数を増やし、上限で頭打ちにする。
"""
import os
from _common import *  # noqa: F401,F403
from _common import finish

from core.compat import QApplication, QAbstractItemView, QListView, QStandardItemModel
from ui.flat_column import _DragListView

fails = []
_keep = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


base = max(1, int(QApplication.wheelScrollLines()))


def _view(n):
    v = _DragListView()
    m = QStandardItemModel(n, 1, v)
    v.setModel(m)
    _keep.append(v)
    return v


v0 = _view(0)
check(v0.wheel_rows() == base, "空なら OS 設定どおり（%d）" % v0.wheel_rows())

v1 = _view(50)
check(v1.wheel_rows() == base, "少件数なら OS 設定どおり（%d）" % v1.wheel_rows())

v2 = _view(1000)
check(v2.wheel_rows() == 10, "1000 件なら 10 行/ノッチ（%d）" % v2.wheel_rows())

v3 = _view(100000)
check(v3.wheel_rows() == _DragListView.WHEEL_MAX_ROWS,
      "どれだけ多くても上限で止まる（%d）" % v3.wheel_rows())

check(v2.wheel_rows() >= v1.wheel_rows() >= v0.wheel_rows(),
      "件数が増えるほど速くなる（単調）")

# 行送りモードであること（カラムと基準を揃える）
check(v2.verticalScrollMode() in (QAbstractItemView.ScrollPerItem,
                                  QAbstractItemView.ScrollPerPixel),
      "スクロールモードが設定されている")
check(v2.viewMode() == QListView.ListMode, "既定はリスト表示")

# ── r146: サムネイル表示（IconMode）も件数に応じて速くなる ────────────────
# ユーザー指摘 2026-10-23「サムネ表示の時の縦スクロールの遅さが解消していません」
from core.compat import QSize, QtGui, QtCore, QPoint
QPointF = QtCore.QPointF
from ui import wheel_speed as ws


def _icon_view(n, cell=100, width=520):
    v = _view(n)
    v.setViewMode(QListView.IconMode)
    v.setResizeMode(QListView.Adjust)
    v.setWrapping(True)
    v.setSpacing(4)
    v.setGridSize(QSize(cell, cell))
    v.resize(width, 300)
    v.show()
    QApplication.processEvents()
    return v


vi_small = _icon_view(20)
check(ws.rows_per_notch(vi_small) == base, "サムネ: 少件数なら OS 設定どおり（%d）" % ws.rows_per_notch(vi_small))

vi = _icon_view(2000)
per = ws.items_per_row(vi)
check(per >= 4, "サムネ: 1 行に並ぶ枚数を幅から出す（%d）" % per)
rows_total = ws.total_rows(vi)
check(rows_total == (2000 + per - 1) // per, "サムネ: 総行数は 件数/枚数（%d）" % rows_total)
want = max(base, min(rows_total // ws.WHEEL_TARGET_NOTCHES, ws.WHEEL_MAX_ROWS))
check(ws.rows_per_notch(vi) == want and want > base,
      "サムネ: 件数が多いほど 1 ノッチの行数が増える（%d 行）" % ws.rows_per_notch(vi))


def _wheel(v, notches=-1):
    sb = v.verticalScrollBar()
    before = sb.value()
    pos = QPointF(10, 10)
    gp = v.viewport().mapToGlobal(QPoint(10, 10))
    ev = QtGui.QWheelEvent(pos, QPointF(gp.x(), gp.y()),
                           QPoint(0, 0), QPoint(0, int(120 * notches)),
                           Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    QApplication.sendEvent(v.viewport(), ev)
    QApplication.processEvents()
    return sb.value() - before


vi.verticalScrollBar().setValue(0)
moved = _wheel(vi, -1)
step = ws.row_step_px(vi)
check(step > 0, "サムネ: 1 行の高さが取れる（%d px）" % step)
check(moved == ws.rows_per_notch(vi) * step,
      "サムネ: 1 ノッチで «行数 × 1 行の高さ» だけ進む（%d px = %d 行）" % (moved, ws.rows_per_notch(vi)))
vi_small.verticalScrollBar().setValue(0)
# 少件数は Qt 既定（wheelScrollLines × singleStep）と同じ量
m_small = _wheel(vi_small, -1)
check(m_small <= base * ws.row_step_px(vi_small) + 1, "サムネ: 少件数で速くなりすぎない（%d px）" % m_small)
# Ctrl 付きは触らない（サイズ変更に使う）
vi.verticalScrollBar().setValue(0)
ev = QtGui.QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, -120),
                       Qt.NoButton, Qt.ControlModifier, Qt.ScrollUpdate, False)
check(not ws.handle_wheel(vi, ev), "サムネ: Ctrl 付きは件数スクロールにしない")

# 本体のサムネイル表示（BrowserPanel._thumb_view）とカラムの原文にも入っている
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
for rel in ("ui/browser_panel.py", "ui/browser_column_view.py", "ui/flat_column.py"):
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    check("wheel_speed" in src, "%s: 共通の物差し（wheel_speed）を使う" % rel)

finish(not fails)
