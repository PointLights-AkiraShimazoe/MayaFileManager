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
    QtCore,
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
    """乗せると開く／押すとコンパクトモードを抜けるアイコン。

    r146（ユーザー報告 2026-10-13）: 触れた瞬間に開いていたので、別アプリで
    作業中にマウスが掠めただけでウィンドウが飛び出していた。**少し留まって
    から** 開く（かすめただけでは開かない）。"""

    HOVER_OPEN_MS = 250

    def __init__(self, puck):
        super().__init__(puck)
        self._puck = puck
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.HOVER_OPEN_MS)
        self._timer.timeout.connect(self._open_if_still_here)

    def _open_if_still_here(self):
        try:
            if self.underMouse():
                self._puck.controller.expand()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _OpenButton._open_if_still_here")

    def enterEvent(self, e):
        self._timer.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._timer.stop()
        super().leaveEvent(e)


class _Puck(QWidget):
    """コンパクト状態で表示する小さな的。"""

    HEIGHT = 30

    def __init__(self, controller):
        # r146（ユーザー指示 2026-10-11）:
        #   「コンパクトビューにした時に、タスクバーから消えるのをやめてください。
        #     常に最前面にしていない時に、どこに行ったかがわからなくなります。」
        # Qt.Tool は WS_EX_TOOLWINDOW になるのでタスクバーに出ない。
        # Qt.Window にしてタスクバーへ出す（枠は自前なので FramelessWindowHint）。
        # パック自身は «常に手前»。本体の最前面表示とは独立で、本体の設定を
        # 勝手に書き換えない（r145 はここで最前面表示を道連れにしていた）。
        super().__init__(None,
                         Qt.Window | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self.controller = controller
        self.setWindowTitle(tr("ファイルマネージャー（コンパクト）",
                               "File Manager (compact)"))
        try:
            win = controller.window_widget()
            if win is not None and not win.windowIcon().isNull():
                self.setWindowIcon(win.windowIcon())
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _Puck(icon)")
        self.setObjectName("mfmCompactPuck")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # r146: 出す時にフォーカスを奪わない。奪うと «畳む → パックが出る →
        # アクティブになる → 下の event() が開く → カーソルは外なので畳む»
        # という点滅ループになる（道具窓を開いていると特に起きやすい）。
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # r146（ユーザー報告 2026-10-13「コンパクトの状態で D&D が出来ません」）:
        # パックの上へファイルを引っぱってきたら本体を開いて、そのまま
        # カラムへ落とせるようにする（いわゆるスプリングローデッド）。
        # ホバーの enterEvent は **ドラッグ中には飛ばない** ので、
        # ドラッグ用のイベントを自分で受ける必要がある。
        self.setAcceptDrops(True)
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
        self.btn.setToolTip(tr(
            "乗せると開く / クリックで通常表示に戻る / 右クリックでメニュー",
            "Hover to open / click to leave compact mode / right-click for a menu"))
        self.btn.clicked.connect(
            lambda _c=False: self.controller.set_active(False))
        lay.addWidget(self.btn, 0)

        self._lay = lay
        self._mirrored = False
        self.setFixedSize(_Grip.WIDTH + 26 + 6, self.HEIGHT)
        # r146 後半（ユーザー報告 2026-10-14）:
        #   「タスクバーも、コンパクトと通常が切り替わる度に消えて出てを
        #     繰り返していてみっともないです。」
        # タスクバーのボタンは «ウィンドウ 1 つにつき 1 個» なので、本体と
        # パックが交互に姿を見せると、ボタンも交互に消えて出る。
        # **ボタンは本体のものだけにする。** パックには出さない
        # （WS_EX_TOOLWINDOW）。畳んでもボタンが消えないのは、本体を
        # 隠すのではなく «最小化» するようにしたから（CompactController.collapse）。
        try:
            from core.win_topmost import set_taskbar_button
            set_taskbar_button(self, False)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _Puck(taskbar)")
        self.setStyleSheet(
            "#mfmCompactPuck{background:%(plane_header)s;"
            "border:1px solid %(primary)s;border-radius:7px;}"
            "#mfmCompactRestore{background:transparent;border:none;}"
            "#mfmCompactRestore:hover{background:%(state_hover)s;"
            "border-radius:5px;}" % v)


    def event(self, e):
        """タスクバーのボタンから起こされたら開く（r146）。

        マウスを乗せる以外の «戻し方» がこれしかないので、
        ウィンドウがアクティブになった時点で開く。ただし **畳んだ直後は
        見送る**。畳んだ拍子に回ってきたアクティブ化で開いてしまうと、
        カーソルは窓の外にいるのですぐまた畳まれ、点滅し続ける。"""
        try:
            if e.type() == QtCore.QEvent.WindowActivate \
                    and self.controller.accepts_activation():
                QTimer.singleShot(0, self.controller.expand)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _Puck.event")
        return super().event(e)

    # ── ドラッグしてきた時（r146）───────────────────────────────
    @staticmethod
    def _has_files(e) -> bool:
        try:
            m = e.mimeData()
            return bool(m is not None and m.hasUrls())
        except Exception:
            return False

    def dragEnterEvent(self, e):
        """ファイルを引っぱってきたら本体を開く（パックは引っ込む）。"""
        if not self._has_files(e):
            e.ignore()
            return
        e.acceptProposedAction()
        # 開く時にフォーカスを奪うと Windows のドラッグが途中で切れるので、
        # «前に出すだけ» にする（for_drag=True）。
        QTimer.singleShot(0, lambda: self.controller.expand(for_drag=True))

    def dragMoveEvent(self, e):
        if self._has_files(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        """パックに直接落とされた時も取りこぼさない。

        開いた本体のカラムへ落とすのが本筋だが、開くより先に指を離される
        ことがある。その時は «今いるフォルダ» へ落ちたものとして扱う。"""
        if not self._has_files(e):
            e.ignore()
            return
        e.acceptProposedAction()
        self.controller.expand(for_drag=True)
        self.controller.drop_on_puck(e.mimeData())

    def contextMenuEvent(self, e):
        """パックの右クリック（r146）。

        «コンパクト時に最小化が出来ない» への答えの片方。パックには
        タイトルバーが無いので、最小化・通常表示への復帰をここから出す。"""
        try:
            from core.compat import QMenu
            m = QMenu(self)
            a_open = m.addAction(tr("開く", "Open"))
            a_leave = m.addAction(tr("通常表示に戻す", "Leave compact mode"))
            m.addSeparator()
            a_min = m.addAction(tr("最小化", "Minimize"))
            act = m.exec(e.globalPos()) if hasattr(m, "exec") \
                else m.exec_(e.globalPos())
            if act is a_open:
                self.controller.expand()
            elif act is a_leave:
                self.controller.set_active(False)
            elif act is a_min:
                self.showMinimized()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _Puck.contextMenuEvent")

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
        self._collapsed_at = 0.0     # 畳んだ時刻（直後のアクティブ化を見送る）
        self._was_minimized = False  # 最小化されていたか（最小化を尊重する）
        self._restored_at = 0.0      # 最小化から戻した時刻（少し畳まない）
        self._drag_until = 0.0       # ドラッグで開いた猶予の期限
        self._prev_foreground = 0    # 開く前に前にいたウィンドウ（返す先）
        self._mouse_busy_since = 0.0  # «ボタンが押されている» が続き始めた時刻
        self._tool_windows = []      # 畳む時に一緒に隠した道具窓
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._tick)

    # ── 状態 ─────────────────────────────────────────────────────
    def window_widget(self):
        return self._win

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
            w = self._win
            if target is not None:
                # 出してから動かすと «前の場所に白い枠» が一瞬見える（r146）
                try:
                    w.move(self._clamp(target, w.frameGeometry().size()))
                except Exception as _e:
                    _swallow(_e, "ui/compact_mode.py set_active(move)")
            try:
                w.setAttribute(Qt.WA_ShowWithoutActivating, False)
            except Exception as _e:
                _swallow(_e, "ui/compact_mode.py set_active(activate)")
            w.showNormal()
            w.raise_()
            w.activateWindow()      # ここはユーザーが明示的に押した時だけ通る
            if self._puck is not None:
                self._puck.hide()
            self._show_tool_windows()
        # 見た目（ヘッダのボタンが光る）を合わせる
        try:
            self._win.sync_compact_button()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py set_active(ui)")

    def toggle(self):
        self.set_active(not self._active)

    def close_puck(self):
        """パックを閉じて手放す（r146 後半）。

        パックは本体とは別の最上位ウィンドウなので、本体を閉じても
        残っていると «プロセスが終わらない» ことになる。終了時に本体から
        呼ぶ。"""
        self._timer.stop()
        puck = self._puck
        self._puck = None
        if puck is None:
            return
        try:
            puck.close()
            puck.deleteLater()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py close_puck")

    def drop_on_puck(self, mime):
        """パックに直接落とされたファイルを «今いるフォルダ» へ渡す（r146）。"""
        try:
            win = self._win
            panel = getattr(win, "_browser", None)
            if panel is None or mime is None or not mime.hasUrls():
                return
            import os as _os
            paths = [u.toLocalFile() for u in mime.urls()]
            paths = [_os.path.normpath(p) for p in paths if p]
            dest = panel.current_path()
            if not paths or not dest or not _os.path.isdir(dest):
                return
            from core.compat import QCursor
            QTimer.singleShot(
                0, lambda: panel._on_files_dropped(paths, dest, None,
                                                   QCursor.pos()))
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py drop_on_puck")

    def restore_from_settings(self):
        """起動時に «前回コンパクトだったか» を復元する（r146）。

        r145 は ACTIVE_KEY を保存するだけで **読み戻していなかった**。
        さらにパックの置き場所も画面外のまま復元されるため、モニタ構成が
        変わった後に起動すると «本体も出ない・パックも見えない・タスクバーにも
        無い» という、アプリを完全に見失う状態になり得た
        （ユーザー報告 2026-10-13）。復元は «パックが実際に見えること» を
        確かめてから確定させる。"""
        try:
            want = bool(self._sm.get(self.ACTIVE_KEY, False))
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py restore_from_settings")
            return False
        if not want or self._active:
            return False
        self.set_active(True)
        return self._active

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
            # r146: 覚えている置き場所が «今つながっている画面の外» なら
            # 引き戻す。モニタ構成が変わった後に «パックが見えない＝アプリを
            # 見失う» という事故になる（実機 2026-10-13: 保存値 -2108,-510）。
            self._pull_puck_onto_screen()
        return self._puck

    def _pull_puck_onto_screen(self):
        """パックがどの画面にも載っていなければ主画面の左上寄りへ戻す。"""
        puck = self._puck
        if puck is None:
            return
        try:
            g = puck.frameGeometry()
            for sc in QApplication.screens():
                if sc.availableGeometry().intersected(g).isValid():
                    return
            sc = QApplication.primaryScreen()
            if sc is None:
                return
            av = sc.availableGeometry()
            puck.move(av.left() + 40, av.top() + 40)
            self.remember_puck_pos()
            self._win._log_quiet(
                "compact: パックが画面外だったので戻した %r" % (g,))
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _pull_puck_onto_screen")

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
        """本体を «左上の角» へ畳んでパックだけにする。

        r146（ユーザー指示 2026-10-13）:
          「コンパクトモードでオプションの設定等の UI が開かれていると、
            離れた際に点滅します。一緒に非表示になるようにしてください。」
        設定ダイアログなどの道具窓は本体とは別の最上位ウィンドウなので、
        本体だけ隠すと «本体は消えたのにダイアログだけ浮いている» という
        ちぐはぐな状態になる。畳む時は一緒に引っ込め、開く時に戻す。
        """
        if not self._active:
            return
        puck = self._ensure_puck()
        # 本体がまだ見えているうちに、対応するパックの位置を決める
        if self._win.isVisible():
            puck.move(self._clamp(self._puck_pos_for_window(), puck.size()))
        self._expanded = False
        self._hide_tool_windows()
        # r146 後半: **隠さずに最小化する。**
        #   hide() だと本体のタスクバーのボタンごと消える。代わりにパックの
        #   ボタンが出るので «消えて出て» の点滅になり、しかも «コンパクトの
        #   まま終了» すると «閉じる口» がパックしか無くなる。パックを閉じても
        #   本体は隠れたまま生き残るので、次に起動した時に単一起動ガードが
        #   «既にいる方を前に出す» 経路に入り、**本体とパックが両方出た**
        #   （ユーザー報告 2026-10-14、スクリーンショット）。
        #   最小化なら、ボタンは常に本体のものが 1 つだけ居座り、
        #   右クリック → 閉じる も普通に効く。
        self._win.showMinimized()
        self._return_foreground()
        import time
        self._collapsed_at = time.time()
        puck.show()
        puck.raise_()
        # 出すたびに掛け直す。Qt がネイティブ窓を作り直すと拡張スタイルが
        # 戻ってしまい、またタスクバーに出てしまう（冪等なので安い）。
        try:
            from core.win_topmost import set_taskbar_button
            set_taskbar_button(puck, False)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py collapse(taskbar)")
        self.remember_puck_pos()
        # r146: «畳んだのにパックが出ていない» と、アプリごと見失う
        # （本体は隠れている・パックは見えない）。出せなければ畳むのをやめる。
        if not puck.isVisible():
            self._win._log_quiet("compact: パックを出せなかったので通常表示へ戻す")
            self.set_active(False)

    def _return_foreground(self):
        """畳む時、元いたアプリへフォーカスを返す（r146）。

        ユーザー報告 2026-10-13:
          「Premiere でスペースを押しても効かず、Manager を閉じたら
            反応するようになりました。」
        こちらが前に出てしまうと、畳んだ後のフォーカスは Windows の都合で
        決まり、元のアプリへ戻るとは限らない（手前にいるパックへ行くことも
        ある）。開く前に控えておいた相手へ明示的に返す。"""
        hwnd, self._prev_foreground = getattr(self, "_prev_foreground", 0), 0
        if not hwnd:
            return
        try:
            from core.win_topmost import app_is_foreground, set_foreground
            if app_is_foreground():
                set_foreground(hwnd)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _return_foreground")

    # ── 道具窓（設定・各種エディタ）の同伴 ───────────────────────
    ACTIVATION_GRACE_SEC = 0.6
    RESTORE_GRACE_SEC = 1.5     # 最小化から戻した直後は畳まない
    DRAG_GRACE_SEC = 8.0        # ドラッグで開いた後、落とすまでの猶予
    MOUSE_BUSY_MAX_SEC = 3.0    # «ボタンが押されている» を信じ続ける上限

    def accepts_activation(self) -> bool:
        """パックのアクティブ化で開いてよいか（畳んだ直後は見送る）。"""
        import time
        return (self._active and not self._expanded
                and (time.time() - self._collapsed_at) > self.ACTIVATION_GRACE_SEC)

    def _tool_window_list(self):
        """本体から開いた、今見えている道具窓。"""
        out = []
        try:
            for w in QApplication.topLevelWidgets():
                if w is self._win or w is self._puck:
                    continue
                if w.isVisible() and self._owned_by_win(w):
                    out.append(w)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _tool_window_list")
        return out

    def _hide_tool_windows(self):
        self._tool_windows = []
        for w in self._tool_window_list():
            try:
                w.hide()
                self._tool_windows.append(w)
            except RuntimeError:
                pass                      # 破棄済み
            except Exception as _e:
                _swallow(_e, "ui/compact_mode.py _hide_tool_windows")

    def _show_tool_windows(self):
        kept, self._tool_windows = self._tool_windows, []
        for w in kept:
            try:
                w.show()
                w.raise_()
            except RuntimeError:
                pass                      # 畳んでいる間に閉じられた
            except Exception as _e:
                _swallow(_e, "ui/compact_mode.py _show_tool_windows")

    def expand(self, for_drag: bool = False):
        """パックのいた場所から «右下へ» 本体を開く（パックは隠す）。

        for_drag=True … ドラッグ中に開く時。**フォーカスを奪わない**。
        Windows のドラッグ中に activateWindow() を呼ぶとドラッグが
        途中で切れて «落とせない» になる（r146、ユーザー報告 2026-10-13）。

        r146（ユーザー指示 2026-10-11「真っ白の画面が一瞬入る」）:
        r145 は «出してから動かす» 順だったので、

          1. 前にいた場所にウィンドウが出る（中身はまだ描けていない＝白い）
          2. その後 move で目的の場所へ飛ぶ

        という 2 段になり、白い枠が一瞬見えていた。直し方は 2 つ重ねる。

          * **隠れているうちに位置を決める。** 出した後に動かさない。
        """
        if not self._active or self._expanded:
            return
        self._expanded = True
        puck = self._ensure_puck()
        w = self._win
        target = self._clamp(self._window_pos_for_puck(),
                             w.frameGeometry().size())
        # 1) 隠れているうちに置き場所を決める（出してから動かさない）
        #    r146 後半: 畳んでいる間は «最小化» なので、move() だけでは
        #    効かないことがある（Windows は «戻した時の位置» を別に持つ）。
        #    WINDOWPLACEMENT の rcNormalPosition を書き替える。
        try:
            w.move(target)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py expand(move)")
        try:
            from core.win_topmost import set_restore_pos
            set_restore_pos(w, target.x(), target.y())
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py expand(restore pos)")
        # r146（ユーザー報告 2026-10-13）:
        #   「Premiere でスペースを押しても効かず、Manager を閉じたら
        #     反応するようになりました。アクティブではないとユーザーが
        #     思っている時に、操作が拘束されるのは有りえません。」
        # 原因はここ。ホバーで開くたびに activateWindow() を呼んでいたので、
        # 他アプリで作業中でもフォアグラウンドを奪っていた。奪われた側は
        # キー入力が届かなくなる。**もともとこのアプリが前にいた時だけ**
        # アクティブにし、そうでなければ «出して前面に置く» だけにする。
        # 使いたくなったらユーザーがクリックする。それが自然なフォーカス移動。
        from core.win_topmost import app_is_foreground, foreground_hwnd
        mine = app_is_foreground()
        if not mine:
            # 奪わないつもりでも、Windows の都合で前に出てしまうことがある。
            # «元どこにいたか» を控えておき、畳む時に返す（r146）。
            self._prev_foreground = foreground_hwnd()
        take_focus = (not for_drag) and mine
        try:
            w.setAttribute(Qt.WA_ShowWithoutActivating, not take_focus)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py expand(no-activate)")
        w.showNormal()
        w.raise_()
        if for_drag:
            import time
            self._drag_until = time.time() + self.DRAG_GRACE_SEC
        elif take_focus:
            w.activateWindow()
        puck.hide()
        self._show_tool_windows()

    # ── 白フラッシュ対策 ─────────────────────────────────────────
    # r146 の途中で «描き終わるまで setWindowOpacity(0) にする» を試したが、
    # **撤去した**（ユーザー報告 2026-10-13「描画関連も大分おかしい」）。
    # Windows では不透明度を触った時点でウィンドウが WS_EX_LAYERED になり、
    # 描画経路がまるごと変わる。1.0 に戻しても再描画の取りこぼしが出る形で、
    # «古い表示が残る» 類の不具合を招く。白フラッシュは «隠れているうちに
    # 位置を決めて、出した後は動かさない»（expand の 1 段目）だけで消える。
    # 対症療法を重ねて正常な描画を壊してはいけない。

    # ── 判定 ─────────────────────────────────────────────────────
    def _busy(self) -> bool:
        """今は畳んではいけない状態か。"""
        import time
        app = QApplication.instance()
        if app is None:
            return True
        if app.activePopupWidget() is not None:      # メニュー／コンボの展開中
            return True
        if app.activeModalWidget() is not None:      # モーダルダイアログ
            return True
        if app.mouseButtons() != Qt.NoButton:        # ドラッグ中
            # r146: ここが «開いたまま閉じなくなる» 原因だった。
            # 外部アプリ（エクスプローラー）からのドラッグは OS 側の
            # ドラッグループが持っていくので、Qt はボタンの «離した» を
            # 見ないことがある。そのまま信じ続けると «押しっぱなし» 扱いの
            # まま永遠に畳まれない。ほどほどで見切る。
            now = time.time()
            if not self._mouse_busy_since:
                self._mouse_busy_since = now
            if now - self._mouse_busy_since < self.MOUSE_BUSY_MAX_SEC:
                return True
        else:
            self._mouse_busy_since = 0.0
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
        """文字入力中か（入力中は畳まない）。

        r146: 本体だけでなく «本体から開いた道具窓» で打っている時も対象。
        設定ダイアログに文字を打っている最中にマウスが窓の外にあると、
        それだけで畳まれていた。"""
        app = QApplication.instance()
        if app is None:
            return False
        act = app.activeWindow()
        if act is None:
            return False
        if act is not self._win and not self._owned_by_win(act):
            return False
        fw = app.focusWidget()
        return isinstance(fw, QLineEdit) and fw.hasFocus()

    def _tick(self):
        if not self._active or not self._expanded:
            return
        # r146（ユーザー報告 2026-10-13「コンパクト時に最小化が出来ない」）:
        # 最小化したウィンドウはカーソルの «外» にいるので、r145 はそのまま
        # 畳んでいた。畳むと本体が hide され、タスクバーからも消えるので、
        # ユーザーから見ると «最小化ボタンを押したのに最小化できない»
        # （代わりにパックになる）。最小化は最小化として尊重する。
        if self._win.isMinimized():
            self._was_minimized = True
            return
        if getattr(self, "_was_minimized", False):
            # 最小化から戻ってきた直後。カーソルがまだ窓の外にあるのが普通
            # なので、ここで畳むと «戻した瞬間に消える» ことになる。少し待つ。
            import time
            self._was_minimized = False
            self._restored_at = time.time()
        if getattr(self, "_restored_at", 0.0):
            import time
            if time.time() - self._restored_at < self.RESTORE_GRACE_SEC:
                return
            self._restored_at = 0.0
        if getattr(self, "_drag_until", 0.0):
            import time
            if time.time() < self._drag_until:
                # ドラッグで開いた直後。本体はアクティブではないので
                # «乗っているか» だけで判断すると落とす前に畳んでしまう。
                if self._cursor_inside():
                    self._drag_until = 0.0      # 中に入った＝猶予は役目を終えた
                return
            self._drag_until = 0.0
        if self._busy() or self._typing() or self._cursor_inside():
            return
        self.collapse()
