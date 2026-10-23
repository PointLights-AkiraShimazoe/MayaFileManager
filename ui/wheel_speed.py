# -*- coding: utf-8 -*-
"""ホイール 1 ノッチで送る量を «件数に応じて» 決める（r144 → r146 で全ビューへ）。

r144（ユーザー指摘 2026-10-08）: 平坦ビューの **リスト表示** だけ、件数が多い
ほど 1 ノッチの行数を増やしていた（端まで WHEEL_TARGET_NOTCHES 回程度）。
r146（ユーザー指摘 2026-10-23「サムネ表示の時の縦スクロールの遅さが解消して
いません」）: サムネイル表示（IconMode）は Qt 任せ＝ 1 ノッチ 3 行固定だった。
1 行に並ぶ枚数が決まっているので、件数が多いほどノッチが終わらない。
平坦／カラム／本体のサムネ、全部のビューで同じ物差しにする。

  * 件数が少ないうちは OS 設定（wheelScrollLines）どおり
  * 端まで WHEEL_TARGET_NOTCHES 回程度で行けるよう 1 ノッチの «行数» を増やす
  * 行き過ぎは WHEEL_MAX_ROWS で止める
  * Ctrl（サイズ変更）／Shift（横スクロール）／Alt 付きは触らない
"""
from core.compat import QApplication, QAbstractItemView, QListView, Qt
from core.diag import swallow as _swallow

WHEEL_TARGET_NOTCHES = 100
WHEEL_MAX_ROWS = 32


def base_rows() -> int:
    try:
        return max(1, int(QApplication.wheelScrollLines()))
    except Exception:
        return 3


def items_per_row(view) -> int:
    """サムネイル表示で 1 行に並ぶ枚数（リスト表示は 1）。"""
    try:
        if view.viewMode() != QListView.IconMode:
            return 1
        g = view.gridSize()
        sp = int(view.spacing())
        if g.isValid() and g.width() > 0:
            cell = g.width() + sp
        else:
            m = view.model()
            root = view.rootIndex()
            if m is None or m.rowCount(root) <= 0:
                return 1
            r = view.visualRect(m.index(0, 0, root))
            cell = max(1, r.width()) + sp * 2
        w = max(1, view.viewport().width())
        return max(1, w // max(1, cell))
    except Exception as _e:
        _swallow(_e, "ui/wheel_speed.py items_per_row")
        return 1


def total_rows(view) -> int:
    try:
        m = view.model()
        n = m.rowCount(view.rootIndex()) if m is not None else 0
    except Exception:
        n = 0
    per = items_per_row(view)
    return (n + per - 1) // per if per > 1 else n


def rows_per_notch(view) -> int:
    """このビューで 1 ノッチに送る行数。"""
    base = base_rows()
    want = total_rows(view) // max(1, WHEEL_TARGET_NOTCHES)
    return max(base, min(want, WHEEL_MAX_ROWS))


def row_step_px(view) -> int:
    """サムネイル表示の 1 行ぶんの高さ（px）。"""
    try:
        g = view.gridSize()
        sp = int(view.spacing())
        if g.isValid() and g.height() > 0:
            return g.height() + sp
        m = view.model()
        root = view.rootIndex()
        if m is not None and m.rowCount(root) > 0:
            r = view.visualRect(m.index(0, 0, root))
            if r.height() > 0:
                return r.height() + sp * 2
    except Exception as _e:
        _swallow(_e, "ui/wheel_speed.py row_step_px")
    try:
        return max(1, int(view.verticalScrollBar().singleStep()))
    except Exception:
        return 1


def handle_wheel(view, event) -> bool:
    """ビューの縦ホイールを件数に応じた量で処理する。処理したら True。

    リスト表示（ScrollPerItem）: スクロールバーの単位は «行» なので行数ぶん。
    サムネイル表示（IconMode）: Qt は常にピクセル単位なので 行数 × 1 行の高さ。
    それ以外（リスト表示の ScrollPerPixel 等）は Qt に任せる。"""
    try:
        if event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier):
            return False
        dy = int(event.angleDelta().y())
        if not dy:
            return False
        notches = dy / 120.0
        rows = rows_per_notch(view)
        sb = view.verticalScrollBar()
        if view.viewMode() == QListView.IconMode:
            delta = notches * rows * row_step_px(view)
        elif view.verticalScrollMode() == QAbstractItemView.ScrollPerItem:
            delta = notches * rows
        else:
            return False
        sb.setValue(int(round(sb.value() - delta)))
        event.accept()
        return True
    except Exception as _e:
        _swallow(_e, "ui/wheel_speed.py handle_wheel")
        return False
