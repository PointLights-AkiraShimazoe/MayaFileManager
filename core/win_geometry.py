# -*- coding: utf-8 -*-
"""物理ピクセルでの窓の置き場所（r146）。

ユーザー報告 2026-10-23:
  「画面を跨いでコンパクトの開閉をすると、訳がわからないところに閉じます。」

Qt の論理座標は **画面ごとに DPI（拡大率）が違うと画面ごとに別の物差し** に
なる。窓が 2 つの画面を跨いでいる時、本体の角（本体が属する画面の物差し）を
パック（別の窓＝別の画面に属し得る）の move() に渡すと、同じ数字が別の
物理位置に化ける。A-17（カーソル判定）と同じ原因。

ここでは Win32（GetWindowRect / SetWindowPos / EnumDisplayMonitors）で
**全部を物理ピクセルで** 扱う。Qt の座標は一切混ぜない。
Windows 以外、ネイティブ窓が無い（オフスクリーン等）時は None を返し、
呼ぶ側は従来の Qt 計算に落ちる。

純粋な計算（丸め・一番近い画面）は monitors の一覧を引数に取る関数に
分けてあり、Windows 以外でもテストできる。"""
from core.diag import swallow as _swallow
import sys


def is_windows() -> bool:
    return sys.platform.startswith("win")


# ── 純粋な計算（(left, top, right, bottom) の整数タプル。right/bottom は排他）──

def rect_intersects(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def rect_contains(outer, inner) -> bool:
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and inner[2] <= outer[2] and inner[3] <= outer[3])


def nearest_work_area(rect, monitors):
    """rect に一番近い作業領域。載っている画面があればそれ、無ければ距離最小。
    monitors: [(l, t, r, b), ...]（作業領域）。空なら None。"""
    best, best_d = None, None
    for wa in monitors:
        if rect_intersects(wa, rect):
            # 複数に跨る時は «重なりが大きい» 方
            ox = min(wa[2], rect[2]) - max(wa[0], rect[0])
            oy = min(wa[3], rect[3]) - max(wa[1], rect[1])
            d = -(max(0, ox) * max(0, oy))
        else:
            dx = max(wa[0] - rect[2], rect[0] - wa[2], 0)
            dy = max(wa[1] - rect[3], rect[1] - wa[3], 0)
            d = dx + dy
        if best_d is None or d < best_d:
            best, best_d = wa, d
    return best


def clamp_into(top_left, size, work):
    """左上 top_left・大きさ size の矩形を作業領域 work の中に収めた左上。"""
    x, y = top_left
    w, h = size
    x = min(max(x, work[0]), max(work[0], work[2] - w))
    y = min(max(y, work[1]), max(work[1], work[3] - h))
    return (x, y)


def corner_of(rect, corner: str):
    """矩形の角（'topLeft' / 'topRight' / 'bottomLeft' / 'bottomRight'）。
    right/bottom は «最後のピクセル» ではなく排他端なので -1 する。"""
    l, t, r, b = rect
    x = r - 1 if "Right" in corner else l
    y = b - 1 if "bottom" in corner else t
    return (x, y)


def top_left_for(corner: str, corner_point, size):
    """«その角» を corner_point に合わせた時の左上。"""
    x, y = corner_point
    w, h = size
    if "Right" in corner:
        x -= max(0, w - 1)
    if "bottom" in corner:
        y -= max(0, h - 1)
    return (x, y)


# ── Win32 ───────────────────────────────────────────────────────────

def _hwnd(widget):
    try:
        h = int(widget.winId())
        return h or None
    except Exception:
        return None


def window_rect_px(widget):
    """窓の枠の矩形（物理ピクセル）。取れなければ None。"""
    if not is_windows() or widget is None:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        h = _hwnd(widget)
        if not h:
            return None
        u = ctypes.windll.user32
        if not u.IsWindow(ctypes.c_void_p(h)):
            return None
        rc = wintypes.RECT()
        if not u.GetWindowRect(ctypes.c_void_p(h), ctypes.byref(rc)):
            return None
        return (int(rc.left), int(rc.top), int(rc.right), int(rc.bottom))
    except Exception as _e:
        _swallow(_e, "core/win_geometry.py window_rect_px")
        return None


def monitors_px():
    """全モニタの作業領域 [(l, t, r, b), ...]（物理ピクセル）。取れなければ None。"""
    if not is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        out = []
        PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.POINTER(wintypes.RECT), ctypes.c_double)

        def _cb(hmon, hdc, lprc, lparam):
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if u.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
                w = mi.rcWork
                out.append((int(w.left), int(w.top), int(w.right), int(w.bottom)))
            return 1

        u.EnumDisplayMonitors(None, None, PROC(_cb), 0)
        return out or None
    except Exception as _e:
        _swallow(_e, "core/win_geometry.py monitors_px")
        return None


def move_window_px(widget, x: int, y: int) -> bool:
    """窓の枠の左上を物理ピクセルで置く（大きさ・重なり順・活性は触らない）。"""
    if not is_windows():
        return False
    try:
        import ctypes
        h = _hwnd(widget)
        if not h:
            return False
        SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
        return bool(ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(h), None, int(x), int(y), 0, 0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE))
    except Exception as _e:
        _swallow(_e, "core/win_geometry.py move_window_px")
        return False


def set_window_rect_px(widget, rect) -> bool:
    """窓の枠の矩形（物理ピクセル）をそのまま置く。"""
    if not is_windows():
        return False
    try:
        import ctypes
        h = _hwnd(widget)
        if not h:
            return False
        l, t, r, b = rect
        SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
        return bool(ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(h), None, int(l), int(t), int(r - l), int(b - t),
            SWP_NOZORDER | SWP_NOACTIVATE))
    except Exception as _e:
        _swallow(_e, "core/win_geometry.py set_window_rect_px")
        return False
