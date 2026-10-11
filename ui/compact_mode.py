# -*- coding: utf-8 -*-
"""コンパクトモード（r145、ユーザー指示 2026-10-09）。

狙い
----
1 画面／狭い画面で作業する人が、Maya の任意の場所に «小さい的（パック）» を
置いておき、マウスを乗せるとマネージャーが開いて普段どおり操作でき、
ウィンドウの外へマウスを出すとまた小さくなる、という使い方をできるようにする。

パックの作り（指示 2026-10-09 の反映）
--------------------------------------
    [≡ つまみ][⤢ 開くアイコン]

* **つまみ**: ドラッグで置き場所を変える。ここに乗せても開かない
  （以前はパック全体がホバーで開いたので «掴めない／戻せない» になっていた）。
* **開くアイコン**: 乗せると開く。クリックするとコンパクトモードを抜ける。

本体は «縮める» のではなく «隠す»。レイアウトを畳まないので開閉が速く、
カラム幅やスプリッタの状態も崩れない。

置き場所は **パックの左上 ＝ 本体の左上** で固定する（指示 2026-10-09）。
  開く … パックのいた場所から «右下へ» ウィンドウが広がる
  畳む … ウィンドウが «左上の角へ» 吸い込まれてパックになる
どちらを動かしても関係は崩れない。本体を動かせばパックが付いてくるし、
パックを動かせば次に開く場所が変わる。

畳む判定は «カーソルが本体の外にいるか» のポーリング。Qt の Leave は
子ウィジェットへの出入りでも飛ぶので使えない。外に出たら **すぐ** 畳むが、
次の間だけは畳まない（作業を中断させないため）:

  * メニューやコンボのポップアップが開いている
  * モーダルダイアログが出ている
  * マウスのボタンが押されている（ドラッグ中）
  * 文字入力中（行編集にフォーカスがあり、本体がアクティブ）
  * 本体から開いたツールウィンドウの上にカーソルがいる
"""
from core.diag import swallow as _swallow

from core.compat import (
    Qt, QObject, QTimer, QWidget, QHBoxLayout, QToolButton, QSize,
    QRect, QPoint, QApplication, QLineEdit, QPainter, QColor, QCursor,
)
from core.i18n import tr


class _Grip(QWidget):
    """パックを掴んで動かすためのつまみ。ここではウィンドウを開かない。"""

    WIDTH = 14

    def __init__(self, puck):
        super().__init__(puck)
        self._puck = puck
        self.setFixedWidth(self.WIDTH)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip(tr("ドラッグで置き場所を変える",
                           "Drag to move this puck"))
        self._from = None

    def paintEvent(self, e):
        from core.theme_engine import qss_vars
        p = QPainter(self)
        try:
            c = QColor(qss_vars()["on_surface_dim"])
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            w, h = self.width(), self.height()
            for row in range(3):
                for col in range(2):
                    p.drawRect(w // 2 - 3 + col * 4,
                               h // 2 - 5 + row * 4, 2, 2)
        finally:
            p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._from = e.globalPos() - self._puck.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._from is not None and (e.buttons() & Qt.LeftButton):
            self._puck.move(e.globalPos() - self._from)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._from = None
        self._puck.controller.remember_puck_pos()
        super().mouseReleaseEvent(e)


class _OpenButton(QToolButton):
    """乗せると開く／押すとコンパクトモードを抜けるアイコン。"""

    def __init__(self, puck):
        super().__init__(puck)
        self._puck = puck

    def enterEvent(self, e):
        self._puck.controller.expand()
        super().enterEvent(e)


class _Puck(QWidget):
    """コンパクト状態で表示する小さな的。"""

    HEIGHT = 30

    def __init__(self, controller):
        super().__init__(None,
                         Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self.controller = controller
        self.setObjectName("mfmCompactPuck")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)

        from ui.dcc_header import _lead_icon
        from core.theme_engine import qss_vars
        v = qss_vars()

        self.grip = _Grip(self)
        lay.addWidget(self.grip, 0)

        self.btn = _OpenButton(self)
        self.btn.setObjectName("mfmCompactRestore")
        self.btn.setIcon(_lead_icon("expand", v["on_surface"], 18))
        self.btn.setIconSize(QSize(18, 18))
        self.btn.setFixedSize(26, self.HEIGHT - 4)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setToolTip(tr("乗せると開く / クリックで通常表示に戻る",
                               "Hover to open / click to leave compact mode"))
        self.btn.clicked.connect(
            lambda _c=False: self.controller.set_active(False))
        lay.addWidget(self.btn, 0)

        self._lay = lay
        self._mirrored = False
        self.setFixedSize(_Grip.WIDTH + 26 + 6, self.HEIGHT)
        self.setStyleSheet(
            "#mfmCompactPuck{background:%(plane_header)s;"
            "border:1px solid %(primary)s;border-radius:7px;}"
            "#mfmCompactRestore{background:transparent;border:none;}"
            "#mfmCompactRestore:hover{background:%(state_hover)s;"
            "border-radius:5px;}" % v)


    def set_mirrored(self, on: bool):
        """左右を入れ替える（r145、指示 2026-10-09）。

        右向き（右下・右上）… [つまみ][開くアイコン]
        左向き（左下・左上）… [開くアイコン][つまみ]
        ウィンドウが伸びていく側にアイコンが来るので、視線の流れが合う。"""
        on = bool(on)
        if on == self._mirrored:
            return
        self._mirrored = on
        lay = self._lay
        while lay.count():
            lay.takeAt(0)
        order = (self.btn, self.grip) if on else (self.grip, self.btn)
        for wdg in order:
            lay.addWidget(wdg, 0)
        lay.activate()


class CompactController(QObject):
    """コンパクトモードの入り切りと、展開／再コンパクトの判定。"""

    ACTIVE_KEY = "compact_mode"
    PUCK_POS_KEY = "compact_puck_pos"
    OPEN_DIR_KEY = "compact_open_dir"
    POLL_MS = 60            # 「離れた直後に閉じて」（指示 2026-10-09）
    MARGIN = 4              # 枠のすぐ外は «まだ中» とみなす余白

    def __init__(self, win, settings_manager):
        super().__init__(win)
        self._win = win
        self._sm = settings_manager
        self._puck = None
        self._active = False
        d = settings_manager.get(self.OPEN_DIR_KEY, "dr")
        self._dir = d if d in ("dr", "dl", "ul", "ur") else "dr"
        self._expanded = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._tick)

    # ── 状態 ─────────────────────────────────────────────────────
    def is_active(self) -> bool:
        return self._active

    def is_expanded(self) -> bool:
        return self._expanded

    def set_active(self, on: bool):
        on = bool(on)
        if on == self._active:
            return
        self._active = on
        try:
            self._sm.set(self.ACTIVE_KEY, on)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py set_active(save)")
        if on:
            self.collapse()
            self._timer.start()
        else:
            self._timer.stop()
            # 畳んだ状態から抜ける時は «パックのいた場所» に開く。
            # 開いたまま抜ける時はその場に残す（関係性は保たれている）。
            target = (self._window_pos_for_puck()
                      if (not self._expanded and self._puck is not None)
                      else None)
            self._expanded = False
            if self._puck is not None:
                self._puck.hide()
            w = self._win
            w.showNormal()
            if target is not None:
                w.move(self._clamp(target, w.frameGeometry().size()))
            w.raise_()
            w.activateWindow()
        # 見た目（ヘッダのボタンが光る）を合わせる
        try:
            self._win.sync_compact_button()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py set_active(ui)")

    def toggle(self):
        self.set_active(not self._active)

    # ── パック ───────────────────────────────────────────────────
    def _ensure_puck(self):
        if self._puck is None:
            self._puck = _Puck(self)
            self._puck.set_mirrored(self._dir in ("dl", "ul"))
            pos = self._sm.get(self.PUCK_POS_KEY, None)
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                self._puck.move(int(pos[0]), int(pos[1]))
            else:
                self._puck.move(self._win.frameGeometry().topLeft())
        return self._puck

    def remember_puck_pos(self):
        if self._puck is None:
            return
        try:
            p = self._puck.pos()
            self._sm.set(self.PUCK_POS_KEY, [p.x(), p.y()])
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py remember_puck_pos")

    # ── パックと本体の «位置の関係» ───────────────────────────────
    # r145（ユーザー指示 2026-10-09）:
    #   「コンパクトモードで開いた際にウィンドウの場所を移動しても、
    #     コンパクト状態に戻る先が変わっていません。開くのと閉じるのは
    #     移動してもしなくても相対的には常に同じ関係性を保ってください。
    #     コンパクト状態から開く時は、右下にウィンドウがでるイメージです。」
    #
    # パックの左上 ＝ 本体の左上、で固定する。つまり
    #   開く  … パックのいた場所から «右下へ» ウィンドウが広がる
    #   畳む  … ウィンドウが «左上の角へ» 吸い込まれてパックになる
    # どちらを動かしても関係は崩れない（本体を動かせばパックが付いてくるし、
    # パックを動かせば次に開く場所が変わる）。
    # 向きごとに «揃える角» が変わる。広がっていく向きの «反対側» の角を
    # パックと本体で一致させる。
    #   dr（右下へ広がる）… 左上の角     dl（左下へ）… 右上の角
    #   ul（左上へ）      … 右下の角     ur（右上へ）… 左下の角
    DIRS = ("dr", "dl", "ul", "ur")

    def open_dir(self) -> str:
        return self._dir

    def set_open_dir(self, d: str):
        d = d if d in self.DIRS else "dr"
        if d == self._dir:
            return
        self._dir = d
        try:
            self._sm.set(self.OPEN_DIR_KEY, d)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py set_open_dir(save)")
        if self._puck is not None:
            self._puck.set_mirrored(d in ("dl", "ul"))
        # パックは動かさない。«次に開く向き» が変わるだけ。
        # （開いている最中に変えた時も、畳む先はパックに合わせて決まる）

    def _corner_names(self):
        """(パック側, 本体側) の «揃える角» を取る関数名。"""
        return {
            "dr": ("topLeft", "topLeft"),
            "dl": ("topRight", "topRight"),
            "ul": ("bottomRight", "bottomRight"),
            "ur": ("bottomLeft", "bottomLeft"),
        }[self._dir]

    @staticmethod
    def _top_left_for(corner_name, corner_point, size):
        """«その角» を corner_point に合わせた時の左上。"""
        x, y = corner_point.x(), corner_point.y()
        if "Right" in corner_name:
            x -= max(0, size.width() - 1)
        if "bottom" in corner_name:
            y -= max(0, size.height() - 1)
        return QPoint(x, y)

    @staticmethod
    def _clamp(top_left, size):
        """画面からはみ出さない位置に丸める。"""
        try:
            sc = None
            try:
                sc = QApplication.screenAt(top_left)
            except Exception:
                sc = None
            sc = sc or QApplication.primaryScreen()
            if sc is None:
                return top_left
            av = sc.availableGeometry()
            x = min(max(top_left.x(), av.left()),
                    max(av.left(), av.right() - size.width()))
            y = min(max(top_left.y(), av.top()),
                    max(av.top(), av.bottom() - size.height()))
            return QPoint(x, y)
        except Exception:
            return top_left

    def _puck_pos_for_window(self):
        """今の本体の位置に対応するパックの左上。"""
        pc, wc = self._corner_names()
        g = self._win.frameGeometry()
        return self._top_left_for(pc, getattr(g, wc)(),
                                  self._ensure_puck().size())

    def _window_pos_for_puck(self):
        """今のパックの位置に対応する本体の左上。"""
        pc, wc = self._corner_names()
        g = self._ensure_puck().frameGeometry()
        return self._top_left_for(wc, getattr(g, pc)(),
                                  self._win.frameGeometry().size())

    # ── 畳む／開く ───────────────────────────────────────────────
    def collapse(self):
        """本体を «左上の角» へ畳んでパックだけにする。"""
        if not self._active:
            return
        puck = self._ensure_puck()
        # 本体がまだ見えているうちに、対応するパックの位置を決める
        if self._win.isVisible():
            puck.move(self._clamp(self._puck_pos_for_window(), puck.size()))
        self._expanded = False
        self._win.hide()
        puck.show()
        puck.raise_()
        self.remember_puck_pos()

    def expand(self):
        """パックのいた場所から «右下へ» 本体を開く（パックは隠す）。"""
        if not self._active or self._expanded:
            return
        self._expanded = True
        puck = self._ensure_puck()
        target = self._window_pos_for_puck()
        puck.hide()
        w = self._win
        w.showNormal()
        w.move(self._clamp(target, w.frameGeometry().size()))
        w.raise_()
        w.activateWindow()

    # ── 判定 ─────────────────────────────────────────────────────
    @staticmethod
    def _busy() -> bool:
        """今は畳んではいけない状態か。"""
        app = QApplication.instance()
        if app is None:
            return True
        if app.activePopupWidget() is not None:      # メニュー／コンボの展開中
            return True
        if app.activeModalWidget() is not None:      # モーダルダイアログ
            return True
        if app.mouseButtons() != Qt.NoButton:        # ドラッグ中
            return True
        return False

    def _owned_by_win(self, w) -> bool:
        """本体から開いたツールウィンドウか（その上では畳まない）。"""
        p = w.parent()
        while p is not None:
            if p is self._win:
                return True
            p = p.parent()
        return False

    def _cursor_inside(self) -> bool:
        pos = QCursor.pos()
        m = self.MARGIN
        wins = [self._win]
        for x in QApplication.topLevelWidgets():
            if x is not self._win and x.isVisible() and self._owned_by_win(x):
                wins.append(x)
        for w in wins:
            if not w.isVisible():
                continue
            g = w.frameGeometry()
            if QRect(g.left() - m, g.top() - m,
                     g.width() + 2 * m, g.height() + 2 * m).contains(pos):
                return True
        return False

    def _typing(self) -> bool:
        app = QApplication.instance()
        if app is None or not self._win.isActiveWindow():
            return False
        fw = app.focusWidget()
        return isinstance(fw, QLineEdit) and fw.hasFocus()

    def _tick(self):
        if not self._active or not self._expanded:
            return
        if self._busy() or self._typing() or self._cursor_inside():
            return
        self.collapse()
