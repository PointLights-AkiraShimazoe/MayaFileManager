# -*- coding: utf-8 -*-
"""最前面表示の «お守り役»（r146、ユーザー報告 2026-10-11）。

何を直すものか
--------------
Windows の TOPMOST は «TOPMOST でない窓は、TOPMOST の窓より上に来られない»
という規則で動く。本体だけを最前面にすると、本体から開いた

    * 削除の確認（QMessageBox）
    * 保存ダイアログ・フォルダ選択（QFileDialog）
    * 進捗ダイアログ（QProgressDialog）
    * 自作のエディタ類

が **本体の裏** で開く。モーダルなら入力はすべてそこで止まるので、
ユーザーから見ると «削除も、エクスプローラーで表示も、保存も、何を押しても
無反応»。r145 までの «コンパクトモードに入ると最前面表示も入る» 仕様と
合わさって、気づかないうちにこの状態へ落ちていた。

やり方
------
QApplication にイベントフィルタを 1 つだけ掛け、**新しく見えた最上位
ウィンドウ** を本体と同じ TOPMOST に揃える。SetWindowPos を使うので
ウィンドウは作り直されず、ちらつきも位置飛びも起きない。
最前面表示を切った時は、本体と «自分が上げた窓» をまとめて戻す。
"""
from core.diag import swallow as _swallow

from core.compat import QObject, QTimer, QApplication, QWidget
from core.compat import QtCore


class TopmostKeeper(QObject):

    def __init__(self, win):
        super().__init__(win)
        self._win = win
        self._active = False
        self._raised = []        # 自分が最前面へ上げた窓（弱参照は使わない）

    # ── 入切 ─────────────────────────────────────────────────────
    def set_active(self, on: bool):
        on = bool(on)
        if on == self._active:
            return
        self._active = on
        app = QApplication.instance()
        if app is None:
            return
        if on:
            app.installEventFilter(self)
            for w in self._top_levels():
                self._lift(w)
        else:
            app.removeEventFilter(self)
            for w in list(self._raised):
                self._lift(w, False)
            self._raised = []

    # ── 本体 ─────────────────────────────────────────────────────
    def _top_levels(self):
        out = []
        try:
            for w in QApplication.topLevelWidgets():
                if (w is not self._win and isinstance(w, QWidget)
                        and w.isVisible() and self._owned(w)):
                    out.append(w)
        except Exception as _e:
            _swallow(_e, "ui/topmost_keeper.py _top_levels")
        return out

    def _owned(self, w) -> bool:
        """本体から出た窓か（無関係なアプリの窓まで持ち上げない）。"""
        try:
            if w is self._win:
                return True
            p = w.parent()
            while p is not None:
                if p is self._win:
                    return True
                p = p.parent()
            # 親の無いダイアログ（QMessageBox の静的関数など）も対象にする。
            # **QDialog に限る** のが大事。Maya 組込みで動いている時に
            # «このアプリの最上位窓» を全部拾うと Maya 本体まで最前面にして
            # しまう（ユーザーの作業環境を壊す）。
            from core.compat import QDialog
            return isinstance(w, QDialog)
        except Exception:
            return False

    def _lift(self, w, on=True):
        try:
            from core.win_topmost import set_topmost
            if not set_topmost(w, on):
                return
            if on:
                if all(x is not w for x in self._raised):
                    self._raised.append(w)
            else:
                self._raised = [x for x in self._raised if x is not w]
        except Exception as _e:
            _swallow(_e, "ui/topmost_keeper.py _lift")

    def eventFilter(self, obj, ev):
        try:
            if self._active and ev.type() == QtCore.QEvent.Show \
                    and isinstance(obj, QWidget) and obj.isWindow() \
                    and obj is not self._win and self._owned(obj):
                # Show の最中は HWND がまだ無いことがあるので次の回へ逃がす
                QTimer.singleShot(0, lambda w=obj: self._lift_if_alive(w))
        except Exception as _e:
            _swallow(_e, "ui/topmost_keeper.py eventFilter")
        return False

    def _lift_if_alive(self, w):
        try:
            if w.isVisible():
                self._lift(w)
        except RuntimeError:
            pass                     # 破棄済み
