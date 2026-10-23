# -*- coding: utf-8 -*-
"""r146: コンパクトの置き場所は **物理ピクセル（Win32）** で決める。

ユーザー報告 2026-10-23:
  「画面を跨いでコンパクトの開閉をすると、訳がわからないところに閉じます。」

画面ごとに DPI が違うと、Qt の論理座標は画面ごとに別の物差しになる。本体の
角（本体が属する画面の物差し）をパック（別の窓）の move() に渡すと、同じ数字が
別の物理位置に化ける（A-17 のカーソル判定と同じ原因）。パックの置き場所・
本体の戻し先・退けたかの判定を全部 Win32 の物理ピクセルで行う。

ここで固定すること:
  1) 純粋な計算: 一番近い作業領域（跨ぐ時は重なりの大きい方）、丸め、角
  2) 畳む: パックは本体の角（物理 px）に置かれ、作業領域の中に収まる。
     Qt の論理座標が画面ごとに化けていても関係ない
  3) 開く: 本体は畳む前の矩形（物理 px）へ、パックの移動差分だけずれて戻る
  4) パックのはみ出しは物理 px で一番近い作業領域へ
  5) Windows 以外／ネイティブ窓が無い時は従来の Qt 計算に落ちる
"""
from _common import *  # noqa: F401,F403
from _common import app, sm, finish, QTest
from core.compat import QWidget, QPoint
from core import win_geometry as wg
from ui.compact_mode import CompactController
import ui.compact_mode as _cm

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


# ── 1) 純粋な計算 ──────────────────────────────────────────────────
M1 = (0, 0, 2560, 1400)            # 主画面（作業領域）
M2 = (2560, -200, 6400, 1960)      # 右の 4K（DPI 違い、上にずれている）
mons = [M1, M2]
check(wg.nearest_work_area((100, 100, 160, 130), mons) == M1, "載っている画面を選ぶ")
check(wg.nearest_work_area((2540, 100, 2600, 130), mons) == M2, "跨ぐ時は重なりの大きい方（右）")
check(wg.nearest_work_area((2500, 100, 2560 + 20, 130), mons) == M1, "跨ぐ時は重なりの大きい方（左）")
check(wg.nearest_work_area((7000, 3000, 7060, 3030), mons) == M2, "どこにも載っていなければ一番近い画面")
check(wg.clamp_into((6390, 1950, ), (60, 30), M2) == (6340, 1930), "右下へはみ出したら中へ")
check(wg.clamp_into((-10, -300), (60, 30), M1) == (0, 0), "左上へはみ出したら中へ")
check(wg.corner_of((10, 20, 110, 220), "bottomRight") == (109, 219), "角は排他端 -1")
check(wg.top_left_for("bottomRight", (109, 219), (60, 30)) == (50, 190), "角を合わせた左上")
check(wg.top_left_for("topLeft", (10, 20), (60, 30)) == (10, 20), "左上はそのまま")

# ── 2)〜4) 疑似 Win32: 物理ピクセルの世界を辞書で持つ ───────────────
class _Win(QWidget):
    def sync_compact_button(self):
        pass

    def _log_quiet(self, *_a):
        pass

    def is_always_on_top(self):
        return False

    def save_window_geometry(self):
        pass


world = {}          # widget -> (l, t, r, b) 物理
calls = []


def fake_rect(widget):
    return world.get(widget)


def fake_monitors():
    return list(mons)


def fake_move(widget, x, y):
    l, t, r, b = world.get(widget, (0, 0, 60, 30))
    world[widget] = (x, y, x + (r - l), y + (b - t))
    calls.append(("move", widget, x, y, bool(widget.isVisible())))
    return True


def fake_set_rect(widget, rect):
    world[widget] = tuple(rect)
    calls.append(("rect", widget, rect))
    return True


orig = (wg.is_windows, wg.window_rect_px, wg.monitors_px, wg.move_window_px, wg.set_window_rect_px)
wg.is_windows = lambda: True
wg.window_rect_px = fake_rect
wg.monitors_px = fake_monitors
wg.move_window_px = fake_move
wg.set_window_rect_px = fake_set_rect

sm.set(CompactController.ACTIVE_KEY, False, save=False)
sm.set(CompactController.PUCK_POS_KEY, None, save=False)
sm.set(CompactController.PUCK_POS_PX_KEY, None, save=False)
win = _Win()
win.setGeometry(100, 100, 300, 200)      # Qt 論理（本物の画面）
win.show()
QTest.qWait(80); app.processEvents()
ctl = CompactController(win, sm)
_cm.QCursor.pos = staticmethod(lambda: QPoint(-4000, -4000))
puck = ctl._ensure_puck()
# «物理の世界»: 本体は 2 画面を跨ぎ、右画面側の DPI 違いで論理座標とは無関係な場所
world[win] = (2300, 300, 3100, 900)      # 左 260px は主画面、残りは右画面
world[puck] = (0, 0, 60, 30)
try:
    check(ctl._native_geometry_on(), "前提: 物理ピクセル経路が有効")
    check(ctl.is_parked() is False, "退けていない判定も物理 px（本体は画面に載っている）")
    ctl.set_active(True, reason="テスト")
    QTest.qWait(80); app.processEvents()
    pr = world[puck]
    check(pr[:2] == (2300, 300), "2) パックは本体の左上の角（物理 px）に置かれる（%r）" % (pr,))
    pm = [c for c in calls if c[0] == "move" and c[1] is puck]
    check(bool(pm), "2) パックは Win32 で動かした（Qt 座標を混ぜない）")
    # ユーザー報告 2026-10-23「どこに移動しても同じ位置から動かない」:
    # 隠れたまま Win32 で動かすと show() で Qt が前の位置を置き直す。
    # 必ず show() の後に動かす。
    check(pm and all(c[4] for c in pm), "2) Win32 の移動は show() の後（見えている状態）で行う")
    check(ctl._win_rect_px == (2300, 300, 3100, 900), "2) 畳む前の本体の矩形（物理 px）を覚える")
    # 退け: 論理の退け先が画面に載っているふりをしても、物理で外へ出される
    # （fake の world[win] は退け後も同じなので «載っている» と判定され、物理で退く）
    check(any(c[0] == "move" and c[1] is win for c in calls), "退けた結果を物理 px で確かめ、載っていれば物理で退ける")
    # 3) 開く: パックを 40px 右・20px 下へ動かされていたとして戻す
    world[puck] = (2340, 320, 2400, 350)
    world[win] = (-9000, 380, -8200, 980)      # 退けてある（画面外）
    calls[:] = []
    ctl.expand()
    QTest.qWait(80); app.processEvents()
    rects = [c for c in calls if c[0] == "rect" and c[1] is win]
    check(bool(rects), "3) 本体は Win32 で矩形ごと戻す")
    check(rects and rects[-1][2] == (2340, 320, 3140, 920),
          "3) 畳む前の矩形へ、パックの差分（+40, +20）だけずれて戻る（%r）" % (rects[-1][2] if rects else None,))
    # 4) パックのはみ出し: 右画面の右下の外 → 右画面の中へ
    world[puck] = (6390, 1950, 6450, 1980)
    calls[:] = []
    ctl._pull_puck_onto_screen()
    check(world[puck][:2] == (6340, 1930), "4) はみ出したパックは一番近い作業領域の中へ（物理 px）（%r）" % (world[puck],))
    # もう一度開いて、本体を別の場所へ動かしてから畳む → パックもそこへ
    world[puck] = (2300, 300, 2360, 330)
    world[win] = (-9000, 380, -8200, 980)
    ctl.expand()
    QTest.qWait(80); app.processEvents()
    world[win] = (4000, 600, 4800, 1200)            # 右画面の中へ動かした
    calls[:] = []
    ctl.collapse()
    QTest.qWait(80); app.processEvents()
    check(world[puck][:2] == (4000, 600), "2) 本体を動かして畳み直すとパックもそこへ（同じ位置に居座らない）（%r）" % (world[puck],))
    # 角が画面の外（右画面の右端より外）→ パックは引き戻されるが、開く時の
    # 本体は «引き戻した分» ずれない（基準は実際に出した位置）
    world[puck] = (4000, 600, 4060, 630)
    world[win] = (-9000, 380, -8200, 980)
    ctl.expand()
    QTest.qWait(80); app.processEvents()
    world[win] = (6380, 600, 7180, 1200)            # 右端からはみ出している
    calls[:] = []
    ctl.collapse()
    QTest.qWait(80); app.processEvents()
    check(world[puck][:2] == (6340, 600), "2) 角が画面外なら一番近い作業領域の中へ（%r）" % (world[puck],))
    check(ctl._puck_anchor_px == (6340, 600), "2) 開く時の基準は実際に出した位置（引き戻した後）")
    world[win] = (-9000, 380, -8200, 980)
    calls[:] = []
    ctl.expand()
    QTest.qWait(80); app.processEvents()
    rects = [c for c in calls if c[0] == "rect" and c[1] is win]
    check(rects and rects[-1][2] == (6380, 600, 7180, 1200),
          "3) はみ出していた本体も «その位置のまま» 戻る（%r）" % (rects[-1][2] if rects else None,))
    ctl.set_active(False, reason="テスト終了")
finally:
    (wg.is_windows, wg.window_rect_px, wg.monitors_px, wg.move_window_px, wg.set_window_rect_px) = orig

# ── 5) Windows 以外／ネイティブ窓が無い時は Qt に落ちる ─────────────
wg.is_windows = lambda: False
try:
    check(not ctl._native_geometry_on(), "5) Windows 以外は物理 px 経路を使わない")
    check(ctl._is_parked_px() is None and ctl._pull_puck_onto_screen_px() is None,
          "5) 判定できない時は None（呼ぶ側が Qt 計算に落ちる）")
    check(ctl._restore_window_at_puck_px() is False, "5) 戻しも Qt に落ちる")
finally:
    wg.is_windows = orig[0]
# Windows でネイティブ窓が取れない（GetWindowRect 失敗）時も Qt に落ちる
wg.is_windows = lambda: True
wg.window_rect_px = lambda w: None
try:
    check(ctl._is_parked_px() is None, "5) 矩形が取れなければ None")
    check(ctl._restore_window_at_puck_px() is False, "5) 矩形が取れなければ戻しも Qt")
finally:
    wg.is_windows, wg.window_rect_px = orig[0], orig[1]

finish(not fails)
