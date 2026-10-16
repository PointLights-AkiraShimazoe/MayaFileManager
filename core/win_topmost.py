# -*- coding: utf-8 -*-
"""「すべてのウィンドウの上に表示」を **フラグを使わずに** 実現する（r146）。

なぜフラグを使わないのか
------------------------
Qt の ``setWindowFlag(Qt.WindowStaysOnTopHint, True)`` には副作用が 2 つある。

1. **タイトルバーの部品が消える。**
   ``QWidgetPrivate::adjustFlags()`` は «カスタマイズ系のヒントが 1 つでも
   立っていたら、既定のタイトルバー部品を一切足さない» という作り。
   WindowStaysOnTopHint はそのカスタマイズ系に含まれるので、最前面表示を
   入れた瞬間に ✕ / 最小化 / 最大化 が付かなくなる（Windows では ✕ が
   灰色のまま押せない窓になる。ユーザー報告 2026-10-11）。
2. **ウィンドウが作り直される。** Windows ではネイティブウィンドウごと
   作り直すので、位置・大きさが飛び、切り替えのたびに白い枠が一瞬見える。

さらに致命的なのが «子ダイアログが裏に回る» こと。Windows の TOPMOST は
«TOPMOST でない窓は絶対に TOPMOST の上に来られない» という規則なので、
最前面表示の本体から出した QMessageBox / QFileDialog / 進捗ダイアログが
本体の **裏** に隠れる。モーダルだと入力は全部そこで止まるので、
ユーザーから見ると «削除もエクスプローラー表示も保存も、何を押しても
無反応»（＝全部壊れたように見える）。実際の報告がこれだった。

やり方
------
Win32 の ``SetWindowPos(HWND_TOPMOST)`` を直接呼ぶ。Qt のフラグを触らないので
ウィンドウは作り直されず、タイトルバーの部品もそのまま残る。
«今、本体が最前面か» を覚えておき、**新しく出た最上位ウィンドウにも同じ扱いを
与える**（ui/main_window.py の TopmostKeeper）ことで、ダイアログが裏に
回る問題ごと消える。

Windows 以外では従来どおり Qt のフラグへ落とす（実害のある環境が無いため）。
"""
from core.diag import swallow as _swallow

import sys

GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
SW_HIDE = 0
SW_SHOWNA = 8

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


def is_windows() -> bool:
    return sys.platform.startswith("win")


def set_topmost(widget, on: bool) -> bool:
    """widget（最上位ウィンドウ）の最前面表示を入切する。成功したら True。

    Windows … SetWindowPos。フラグを触らないので作り直しが起きない。
    その他 … Qt のフラグ。可視だった場合は出し直す。
    """
    if widget is None:
        return False
    if is_windows():
        try:
            import ctypes
            hwnd = int(widget.winId())
            if not hwnd:
                return False
            ok = ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(hwnd),
                ctypes.c_void_p(HWND_TOPMOST if on else HWND_NOTOPMOST),
                0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            return bool(ok)
        except Exception as _e:
            _swallow(_e, "core/win_topmost.py set_topmost(win32)")
            return False
    try:
        from core.compat import Qt
        if bool(widget.windowFlags() & Qt.WindowStaysOnTopHint) == bool(on):
            return True
        visible = widget.isVisible()
        geo = widget.geometry()
        widget.setWindowFlag(Qt.WindowStaysOnTopHint, bool(on))
        widget.setGeometry(geo)
        if visible:
            widget.show()
        return True
    except Exception as _e:
        _swallow(_e, "core/win_topmost.py set_topmost(qt)")
        return False


def set_taskbar_button(widget, on: bool = True) -> bool:
    """枠無しウィンドウをタスクバーに出す／出さない（r146）。

    Qt の ``Qt.Tool`` をやめて ``Qt.Window`` にしただけでは足りない。
    Windows は ``Qt.FramelessWindowHint`` の窓を ``WS_POPUP``（タイトルバー
    無し）として作るので、既定ではタスクバーのボタンが **付かない**。
    タスクバーに出すかどうかは拡張スタイルの

        WS_EX_APPWINDOW   … 出す（タイトルバーが無くても出る）
        WS_EX_TOOLWINDOW  … 出さない

    で決まるので、そこを直接書き替える。拡張スタイルの変更は «作り直し» では
    反映されないため、見えている場合はいったん隠して出し直す
    （SW_SHOWNA ＝ 出すがフォーカスは奪わない）。

    Windows 以外は何もしない（False を返す）。"""
    if widget is None or not is_windows():
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        hwnd = int(widget.winId())      # 無ければここでネイティブ窓を作る
        if not hwnd:
            return False
        get_ = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
        set_ = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW
        get_.restype = ctypes.c_ssize_t
        set_.restype = ctypes.c_ssize_t
        set_.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        get_.argtypes = [ctypes.c_void_p, ctypes.c_int]
        h = ctypes.c_void_p(hwnd)
        ex = int(get_(h, GWL_EXSTYLE))
        new = ((ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW) if on \
            else ((ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        if new == ex:
            return True
        visible = bool(widget.isVisible())
        if visible:
            u.ShowWindow(h, SW_HIDE)
        set_(h, GWL_EXSTYLE, ctypes.c_ssize_t(new))
        if visible:
            u.ShowWindow(h, SW_SHOWNA)
        return True
    except Exception as _e:
        _swallow(_e, "core/win_topmost.py set_taskbar_button")
        return False


DWMWA_TRANSITIONS_FORCEDISABLED = 3


def set_window_transitions(widget, enabled: bool) -> bool:
    """最小化／復元のアニメーション（DWM の遷移）を窓単位で入切する（r146）。

    ユーザー報告 2026-10-16（コンパクト中にタスクバーを押すと
    「何か他の UI が画面外へ飛んでいくさまが見える。もう一度押すと戻る」）:
    畳んでいる間、本体は **画面の外** に置いてある。その状態でタスクバーの
    ボタンを押すと Windows が最小化／復元を切り替え、DWM が «タスクバー ⇔
    窓の矩形（画面外）» を結ぶ飛行アニメーションを描く。それが «飛んでいく
    UI» の正体。畳んでいる間だけこの窓の遷移を止める
    （DwmSetWindowAttribute / DWMWA_TRANSITIONS_FORCEDISABLED）。

    Windows 以外は何もしない（False を返す）。"""
    if widget is None or not is_windows():
        return False
    try:
        import ctypes
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        flag = ctypes.c_int(0 if enabled else 1)
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(DWMWA_TRANSITIONS_FORCEDISABLED),
            ctypes.byref(flag), ctypes.sizeof(flag))
        return int(hr) == 0
    except Exception as _e:
        _swallow(_e, "core/win_topmost.py set_window_transitions")
        return False


def set_restore_pos(widget, x: int, y: int) -> bool:
    """**最小化されたウィンドウの «戻る場所»** を決める（r146）。

    コンパクトモードは «畳む＝最小化» にした（タスクバーのボタンを
    消さないため）。開く時は «パックのいた場所» に出したいが、最小化
    されている窓に ``move()`` しても効かないことがある。Windows が
    «戻した時の位置» を別に持っている（WINDOWPLACEMENT.rcNormalPosition）
    ためで、そこを書き替えるのが正しい。

    こうしておくと «出してから動かす» をしないで済むので、白い枠が
    一瞬見える問題も起きない。
    """
    if widget is None or not is_windows():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint),
                        ("showCmd", ctypes.c_uint),
                        ("ptMinPosition", POINT), ("ptMaxPosition", POINT),
                        ("rcNormalPosition", RECT)]

        u = ctypes.windll.user32
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        h = ctypes.c_void_p(hwnd)
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not u.GetWindowPlacement(h, ctypes.byref(wp)):
            return False
        w = wp.rcNormalPosition.right - wp.rcNormalPosition.left
        ht = wp.rcNormalPosition.bottom - wp.rcNormalPosition.top
        wp.rcNormalPosition.left = int(x)
        wp.rcNormalPosition.top = int(y)
        wp.rcNormalPosition.right = int(x) + w
        wp.rcNormalPosition.bottom = int(y) + ht
        return bool(u.SetWindowPlacement(h, ctypes.byref(wp)))
    except Exception as _e:
        _swallow(_e, "core/win_topmost.py set_restore_pos")
        return False


def foreground_hwnd():
    """今フォアグラウンドにあるウィンドウのハンドル（Windows 以外は 0）。"""
    if not is_windows():
        return 0
    try:
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception as _e:
        _swallow(_e, "core/win_topmost.py foreground_hwnd")
        return 0


def app_is_foreground() -> bool:
    """**このアプリ** が今フォアグラウンドか（r146）。

    他のアプリ（Premiere / Maya / ブラウザ…）で作業している最中に、
    こちらが勝手に activateWindow() を呼ぶとフォーカスを奪う。奪われた側は
    «スペースを押しても再生が始まらない» という形で壊れる
    （ユーザー報告 2026-10-13）。奪ってよいのは «もともとこのアプリが前に
    いた時» だけなので、その判定をここで持つ。

    Qt の activeWindow() だけでは足りない（アプリが前にいてもフォーカスを
    持つ widget が無ければ None になる）ので、Windows では
    GetForegroundWindow() と自分の最上位ウィンドウを突き合わせる。
    """
    try:
        from core.compat import QApplication
        app = QApplication.instance()
        if app is None:
            return False
        if app.activeWindow() is not None:
            return True
        if not is_windows():
            return False
        fg = foreground_hwnd()
        if not fg:
            return False
        for w in app.topLevelWidgets():
            try:
                if w.isVisible() and int(w.winId()) == fg:
                    return True
            except Exception:
                continue
        return False
    except Exception as _e:
        _swallow(_e, "core/win_topmost.py app_is_foreground")
        return False


def set_foreground(hwnd) -> bool:
    """別のウィンドウへフォアグラウンドを返す（r146）。

    こちらが前に出てしまった後、元いたアプリ（Premiere / Maya など）へ
    «返す» ための口。Windows は背後のプロセスが勝手に前へ出ることを禁じて
    いるが、**今まさに自分が前にいる** 時に手放すのは許される。
    """
    if not is_windows() or not hwnd:
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        h = ctypes.c_void_p(int(hwnd))
        if not u.IsWindow(h):
            return False
        if int(u.GetForegroundWindow() or 0) == int(hwnd):
            return True
        return bool(u.SetForegroundWindow(h))
    except Exception as _e:
        _swallow(_e, "core/win_topmost.py set_foreground")
        return False
