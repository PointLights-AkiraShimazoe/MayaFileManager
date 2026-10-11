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

finish(not fails)
