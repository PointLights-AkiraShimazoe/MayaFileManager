# -*- coding: utf-8 -*-
"""道具窓を «マネージャーを止めずに» 開くための共通処理（r119）。

原則（ユーザー指示 2026-10-01）:
  **止めないと不都合があるもの以外、マネージャーは止めない。**

止めてよい（モーダルのままにする）のは、次のどれかに当てはまるものだけ:
  1. 返り値を待って処理を続ける（上書き確認・保存先の決定・複製の設定）
  2. 決めないと «その操作自体» が進まない（競合の解決）
それ以外 — 設定・各種エディタ・案内 — は非モーダルにする。
«適用しながら隣のフォルダを見る» ができないと使い勝手が悪いため。

多重に開かないこと・閉じたら参照を捨てることまで含めてここで面倒を見る。
"""

from core.compat import Qt
from core.diag import swallow as _swallow


def show_tool_window(owner, attr: str, factory, reuse: bool = True):
    """owner の属性 attr に «非モーダルの道具窓» を 1 つだけ持たせて開く。

    reuse=True … 既に開いていれば前面に出すだけ（二重に開かない）。
                 設定・各種エディタのように «中身が開き直しで変わらない» 窓向け。
    reuse=False … 毎回作り直す。**対象が呼び出しごとに変わる窓** はこちら。
                 例: バッチリネームは «その時の選択» を抱えるので、
                 使い回すと前回の選択が出たままになる（r119）。
    閉じたら owner.<attr> は None に戻る。
    """
    dlg = getattr(owner, attr, None)
    if dlg is not None:
        if reuse:
            try:
                dlg.show()
                dlg.raise_()
                dlg.activateWindow()
                return dlg
            except RuntimeError:
                dlg = None                 # 破棄済み
        else:
            try:
                dlg.close()
            except RuntimeError:
                pass
            setattr(owner, attr, None)
    dlg = factory()
    if dlg is None:
        return None
    try:
        dlg.setModal(False)
        dlg.setWindowModality(Qt.NonModal)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.destroyed.connect(lambda *_a, a=attr: setattr(owner, a, None))
    except Exception as _e:
        _swallow(_e, "ui/dialog_util.py show_tool_window")
    setattr(owner, attr, dlg)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


# ---------------------------------------------------------------------------
# ファイル／フォルダ選択（r145）
# ---------------------------------------------------------------------------
# ユーザー報告 2026-10-09:
#   「フォルダを指定するために「⋯」ボタンを押し、キャンセルすると、
#     manager がしばらくフリーズします。maya でも起きていますが、
#     Manager で同じ問題は起こさないでほしいです。」
#
# 原因は Windows の «ネイティブ» ダイアログ（IFileDialog）。シェルが
# ネットワークドライブやクラウドの名前空間を列挙しに行くので、閉じる時に
# その後始末で数秒〜十数秒ブロックすることがある。Maya で起きるのも同じ理由。
# Qt 自前のダイアログはシェルを通さないので、この待ちが発生しない。
# 見た目は OS 標準と変わるが、«固まらない» 方を取る。

def _qt_dialog_opts(extra=None):
    """ネイティブ無効を必ず含むオプション。

    PySide6 の QFileDialog.Option は «int との |» を受け付けないので、
    既定値は 0 ではなく None にして、足す時だけ合流させる。"""
    from core.compat import QFileDialog
    opt = QFileDialog.DontUseNativeDialog
    try:
        opt |= QFileDialog.DontResolveSymlinks     # 解決で待たない
    except Exception:
        pass
    if extra is not None:
        try:
            opt |= extra
        except Exception:
            pass
    return opt


def pick_directory(parent, title: str, start: str = "", *extra, **_kw) -> str:
    """フォルダを選ぶ。キャンセルしても固まらない（r145）。

    extra には昔の呼び出しが渡していたオプションが来ることがある。
    ネイティブ無効だけは必ず足したうえで合流させる。"""
    from core.compat import QFileDialog
    try:
        opt = _qt_dialog_opts(QFileDialog.ShowDirsOnly)
    except Exception:
        opt = _qt_dialog_opts()
    for e in extra:
        try:
            opt |= e
        except Exception:
            pass
    return QFileDialog.getExistingDirectory(parent, title, start or "", opt)


def pick_open_file(parent, title: str, start: str = "", filt: str = "",
                   *extra, **_kw):
    """ファイルを選ぶ。戻り値は (パス, 選ばれたフィルタ)（r145）。"""
    from core.compat import QFileDialog
    opt = _qt_dialog_opts()
    sel = ""
    for e in extra:
        if isinstance(e, str):
            sel = sel or e
            continue
        try:
            opt |= e
        except Exception:
            pass
    return QFileDialog.getOpenFileName(parent, title, start or "", filt or "",
                                       sel, opt)


# ---------------------------------------------------------------------------
# タイトルバーの «閉じる» を必ず出す（r146、ユーザー指示 2026-10-11）
# ---------------------------------------------------------------------------
# 「各ウィンドウの右上の✕マークは、押せないなら完全に消してほしいです。
#   消せないなら、✕は押せる様にすべきです。」
#
# Qt の仕様がここの落とし穴。QWidgetPrivate::adjustFlags() は
# «カスタマイズ系のヒントが 1 つでも立っていたら、既定のタイトルバー部品を
#   一切足さない» という作りになっている。カスタマイズ系には
#   WindowStaysOnTopHint / WindowContextHelpButtonHint / FramelessWindowHint …
# が含まれるので、
#     setWindowFlags(flags & ~Qt.WindowContextHelpButtonHint)   ← ？ボタン消し
#     setWindowFlag(Qt.WindowStaysOnTopHint, True)              ← 最前面表示
# のどちらをやっても «その瞬間に ✕ / 最小化 / 最大化 が足されなくなる»。
# 結果、Windows では ✕ が灰色のまま押せない窓になる。
#
# 対処は «触るなら、欲しい部品は自分で全部立てる»。
_BUTTON_HINTS = ("WindowTitleHint", "WindowSystemMenuHint",
                 "WindowCloseButtonHint")
_MINMAX_HINTS = ("WindowMinimizeButtonHint", "WindowMaximizeButtonHint")


def window_flags_with_buttons(flags, minmax: bool = False, on_top=None):
    """flags に «タイトルバー部品» を立てて返す（？ボタンは落とす）。

    minmax=True … 最小化／最大化も出す（メインウィンドウ向け）。
    on_top=True/False … 最前面表示を同時に入切する（None なら今のまま）。
    """
    # r146: **int で組む。** PySide6 の世代によって Qt.WindowType（enum.Flag）
    # の | と ~ の意味が変わり、6.8.3 では «足したはずの WindowCloseButtonHint が
    # 立っていない» という形で実機だけ失敗した（回帰スイート 2026-10-13）。
    # ビット演算は int でやって、最後に元の型へ戻すのが確実。
    value = int(flags)
    names = _BUTTON_HINTS + (_MINMAX_HINTS if minmax else ())
    for name in names:
        hint = getattr(Qt, name, None)
        if hint is not None:
            value |= int(hint)
    help_hint = getattr(Qt, "WindowContextHelpButtonHint", None)
    if help_hint is not None:
        value &= ~int(help_hint)
    top = getattr(Qt, "WindowStaysOnTopHint", None)
    if on_top is not None and top is not None:
        value = (value | int(top)) if on_top else (value & ~int(top))
    for ctor in (type(flags), getattr(Qt, "WindowFlags", None),
                 getattr(Qt, "WindowType", None)):
        if ctor is None:
            continue
        try:
            return ctor(value)
        except Exception:
            continue
    return value


def apply_window_buttons(win, minmax: bool = False):
    """ダイアログ等に «押せる ✕» を用意する。__init__ の中で 1 回呼ぶ。"""
    try:
        win.setWindowFlags(window_flags_with_buttons(win.windowFlags(), minmax))
    except Exception as _e:
        _swallow(_e, "ui/dialog_util.py apply_window_buttons")
