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
