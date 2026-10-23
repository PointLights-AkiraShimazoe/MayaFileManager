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
* **開くアイコン**: 乗せる／クリックで開く。抜けるのはヘッダのボタンか右クリックの「通常表示に戻す」（r146: クリックで抜けるのはやめた）。

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
from core.diag import diag_off as _diag_off  # r146 切り分け用（MFM_DIAG=no_native_cursor）

from core.compat import (
    Qt, QObject, QTimer, QWidget, QHBoxLayout, QToolButton, QSize,
    QRect, QPoint, QApplication, QLineEdit, QPainter, QColor, QCursor,
    QtCore,
)
import os
from core.i18n import tr


def _compact_log(msg: str):
    """コンパクトの入／切を常時記録する（r146）。失敗しても何もしない。"""
    try:
        import datetime
        from ui.browser_util import _MFM_LOG_DIR
        with open(os.path.join(_MFM_LOG_DIR, "mfm_compact.log"), "a",
                  encoding="utf-8") as f:
            f.write("[%s pid=%d] %s\n" % (
                datetime.datetime.now().strftime("%m-%d %H:%M:%S"),
                os.getpid(), msg))
    except Exception:
        pass


def _caller_chain(depth: int = 6) -> str:
    """呼び出し元の連なり（compact_mode.py 以外）を短く。"""
    try:
        import traceback
        parts = []
        for fr in reversed(traceback.extract_stack()[:-2]):
            if fr.filename.endswith("compact_mode.py"):
                continue
            parts.append("%s:%d %s" % (os.path.basename(fr.filename), fr.lineno, fr.name))
            if len(parts) >= depth:
                break
        return " < ".join(parts) or "?"
    except Exception:
        return "?"


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
        # 画面の外へ置いてしまったら、一番近い画面の中へ戻す（触れなくなる）
        self._puck.controller._pull_puck_onto_screen()
        self._puck.controller.remember_puck_pos()
        super().mouseReleaseEvent(e)


class _OpenButton(QToolButton):
    """乗せる／押すと開くアイコン（r146: 押しても抜けない）。

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

    def _cursor_on_me(self) -> bool:
        """カーソルが自分の上にあるか（r146、実機 2026-10-15）。

        ``underMouse()`` は «enter を受け取ったか» に依存するので、
        ウィンドウが **カーソルの下に出てきた** 時は False のままになる。
        畳んだ直後にマウスを乗せても開かなかったのはこれ。座標で見る。"""
        try:
            return self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        except Exception:
            return False

    def _open_if_still_here(self):
        try:
            if self._cursor_on_me():
                self._puck.controller.expand()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _OpenButton._open_if_still_here")

    def arm_if_hovered(self):
        """カーソルが既に乗っていたら、乗せた時と同じように待ち始める。

        パックは «畳んだ場所» に出るので、カーソルの下に現れることがある。
        その時 Qt は enterEvent を出さないので、自分で見て構える。
        r146（ユーザー報告 2026-10-16「畳んだとほぼ同時につまみに
        カーソルを乗せると、再度避けないと開かなくなる」）: 見張りの
        tick からも毎回呼ばれる。enterEvent が来る／来ないに依存せず、
        «乗っていれば開く» を座標だけで保証する。待ち始めは 1 回だけ
        （毎 tick で start し直すと永遠に発火しない）。"""
        if self._cursor_on_me():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

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
        # r146（ユーザー報告 2026-10-15「最前面モードでなくてもコンパクトに
        # すると最前面モードと同様の挙動」）: パックの «常に手前» は
        # **本体の最前面設定に従う**（CompactController.sync_topmost）。
        # 以前はここで WindowStaysOnTopHint を固定していたので、最前面 OFF
        # でもパックだけが他アプリの上に居座っていた。OFF の時に隠れたら
        # タスクバーのボタンで前に出す（notice）。
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
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
        # r146（ユーザー報告 2026-10-15）: «角の黒い余り» を消すために
        # 透過（WA_TranslucentBackground）にしたが、**全体が透けて更に
        # 見えづらくなった**ので撤去した。角を丸めない（四角にする）だけで
        # 余りは出ない。
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
            "乗せる／クリックで開く / 右クリックでメニュー（通常表示に戻す）",
            "Hover or click to open / right-click for a menu (leave compact mode)"))
        # r146（ユーザー報告 2026-10-21）: アイコンの **クリックで解除** を
        # やめる。乗せれば開く（250ms）のに、その前にクリックされたり、
        # アイコン側を掴んで動かそうとして離したりすると «押した記憶が無い
        # のに解除» になっていた。クリックは «開く» に揃える。解除は
        # ヘッダのボタンか右クリックの「通常表示に戻す」から。
        self.btn.clicked.connect(
            lambda _c=False: self.controller.expand())
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
        # 線は «もう少し目立つ様に»（ユーザー指示 2026-10-15）。2px にして
        # 呼びかけ（flash）の時はオレンジへ変える。
        self._qss_v = v
        self.setStyleSheet(self._qss(False))


    # r146 後半（ユーザー報告 2026-10-14）:
    #   「つまみをクリックしたらドラッグ出来ずに通常ウィンドウが開きます。」
    #
    # ここには «ウィンドウがアクティブになったら開く» があった。
    # パックにタスクバーのボタンを出していた頃、そのボタンから戻す唯一の
    # 道だったため。**その前提はもう無い**（ボタンは本体のものだけにした）。
    # 残したままだと、つまみを掴もうとして押した瞬間にパックがアクティブに
    # なり、ドラッグが始まる前に開いてしまう＝パックを動かせない。
    # 戻す道は «アイコンに乗せる / アイコンを押す / ファイルを落とす /
    # 右クリックのメニュー / 本体のタスクバーのボタン» で足りている。
    # **パックのアクティブ化では開かない。**

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
                self.controller.set_active(False, reason="パック右クリック「通常表示に戻す」")
            elif act is a_min:
                # r146（ユーザー報告 2026-10-15「最小化だけ通常ウィンドウが
                # 開くだけになってます」）: パック自身を最小化しても意味が
                # 無い（タスクバーのボタンを持っていない）。アプリとして
                # 最小化する＝本体をタスクバーへ入れ、パックも引っ込める。
                self.controller.minimize_to_taskbar()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _Puck.contextMenuEvent")

    # ── 見た目 ───────────────────────────────────────────────────
    # 呼びかけの色はテーマから取る（signal amber）。ベタ書きしない。
    FLASH_KEY = "warning"
    FLASH_TIMES = 6
    FLASH_MS = 140

    def _qss(self, alert: bool) -> str:
        v = self._qss_v
        edge = v.get(self.FLASH_KEY, v["error"]) if alert else v["primary"]
        width = 3 if alert else 2
        base = ("#mfmCompactPuck{background:%(plane_header)s;"
                "border-radius:0px;}"
                "#mfmCompactRestore{background:transparent;border:none;}"
                "#mfmCompactRestore:hover{background:%(state_hover)s;"
                "border-radius:5px;}" % v)
        # 枠の太さと色だけは状態で変えるので、トークン展開の外で足す
        return base + ("#mfmCompactPuck{border:" + str(width)
                       + "px solid " + edge + ";}")

    def flash(self):
        """«ここに居ます» とユーザーに見せる（r146、ユーザー指示 2026-10-15）。

          「タスクバーをコンパクトでクリックすると通常ウィンドウは一瞬でる
            だけでバグに見えるので、コンパクトのままオレンジ色なりの蛍光色を
            一度ユーザーに視認させるために光らせるでお願いします。」
        """
        t = getattr(self, "_flash_timer", None)
        if t is not None:
            t.stop()
        self.show()
        self.raise_()
        self._flash_left = self.FLASH_TIMES
        t = QTimer(self)
        t.setInterval(self.FLASH_MS)

        def _step():
            self._flash_left -= 1
            self.setStyleSheet(self._qss(self._flash_left % 2 == 1))
            if self._flash_left <= 0:
                t.stop()
                self.setStyleSheet(self._qss(False))
        t.timeout.connect(_step)
        self._flash_timer = t
        self.setStyleSheet(self._qss(True))
        t.start()

    def showEvent(self, e):
        super().showEvent(e)
        # カーソルの下に出てきた時も «乗せた» 扱いで構える（r146）
        try:
            QTimer.singleShot(0, self.btn.arm_if_hovered)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _Puck.showEvent")

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
    PUCK_POS_PX_KEY = "compact_puck_pos_px"   # r146: 物理ピクセルでも覚える
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
        self._drag_seen = False      # 自分のドラッグ（QDrag.exec）を見ていたか
        self._collapsed_in_drag = False  # そのドラッグの最中に畳んだか
        self._tool_windows = []      # 畳む時に一緒に隠した道具窓
        self._win_rect = None        # 畳む前の本体の «中身の矩形»
        self._puck_anchor = None     # その時のパックの位置（差分の起点）
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

    def set_active(self, on: bool, reason: str = ""):
        on = bool(on)
        if on == self._active:
            return
        # r146（ユーザー報告 2026-10-21「押した記憶が無いのにコンパクトモードが
        # 解除されている事がある」）: 入／切は **理由つきで必ず記録** する
        # （~/.maya_file_manager/logs/mfm_compact.log、常時）。次に起きた時に
        # 「誰が切ったか」がログで分かるようにする。
        _compact_log("set_active(%s) reason=%s" % (on, reason or _caller_chain()))
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
            reopen = (not self._expanded and self._puck is not None)
            self._expanded = False
            w = self._win
            if reopen:
                # 出してから動かすと «前の場所に白い枠» が一瞬見える（r146）
                self._restore_window_at_puck()
            try:
                w.setAttribute(Qt.WA_ShowWithoutActivating, False)
            except Exception as _e:
                _swallow(_e, "ui/compact_mode.py set_active(activate)")
            w.showNormal()
            w.raise_()
            w.activateWindow()      # ここはユーザーが明示的に押した時だけ通る
            self.pull_window_onto_screen()   # 画面の外に残さない（r146）
            if self._puck is not None:
                self._puck.hide()
            self._show_tool_windows()
        # 見た目（ヘッダのボタンが光る）を合わせる
        try:
            self._win.sync_compact_button()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py set_active(ui)")

    def toggle(self):
        self.set_active(not self._active, reason="toggle")

    def notice(self):
        """«ここに居ます» をユーザーに見せる（開かない。r146）。

        ユーザー指示 2026-10-15:
          「タスクバーをコンパクトでクリックすると通常ウィンドウは一瞬でる
            だけでバグに見えるので、コンパクトのままオレンジ色なりの蛍光色を
            一度ユーザーに視認させるために光らせるでお願いします。」
        本体を開くと、カーソルはタスクバーにあるので次の監視ですぐ畳まれ、
        «一瞬出て消える» になる。開かずにパックを光らせて知らせる。"""
        if not self._active or self._expanded:
            return
        puck = self._ensure_puck()
        self._park_window()          # 起こされて表に出ていたら退け直す
        try:
            # 最前面 OFF で他の窓に隠れていても、タスクバーから呼ばれた時は
            # 前に出す（ユーザーが明示的に呼んだので奪ってよい）
            puck.show()
            puck.raise_()
            puck.flash()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py notice")

    def minimize_to_taskbar(self):
        """アプリとして最小化する（パックも引っ込める。r146）。"""
        if self._puck is not None:
            try:
                self._puck.hide()
            except Exception as _e:
                _swallow(_e, "ui/compact_mode.py minimize_to_taskbar(puck)")
        try:
            self._win.showMinimized()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py minimize_to_taskbar(win)")

    def restore_from_taskbar(self):
        """最小化から戻された時（コンパクト中）。パックを出して知らせる。"""
        if not self._active or self._expanded:
            return
        self._park_window()
        self.notice()

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
        # A-12（ユーザー指示 2026-10-16）: 起動時は本体を見せず、
        # «直接コンパクトが出て光る»
        if self._active and self._puck is not None:
            try:
                self._puck.flash()
            except Exception as _e:
                _swallow(_e, "ui/compact_mode.py restore_from_settings(flash)")
        return self._active

    # ── パック ───────────────────────────────────────────────────
    def _ensure_puck(self):
        if self._puck is None:
            self._puck = _Puck(self)
            self._puck_topmost = None
            self._puck.set_mirrored(self._dir in ("dl", "ul"))
            self.sync_topmost()
            pos = self._sm.get(self.PUCK_POS_KEY, None)
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                self._puck.move(int(pos[0]), int(pos[1]))
                # r146: 物理ピクセルの保存値があればそちらを正とする
                # （画面ごとに DPI が違うと論理座標は起動ごとに化け得る）
                try:
                    ppx = self._sm.get(self.PUCK_POS_PX_KEY, None)
                    if (self._native_geometry_on() and isinstance(ppx, (list, tuple))
                            and len(ppx) == 2):
                        from core.win_geometry import move_window_px
                        move_window_px(self._puck, int(ppx[0]), int(ppx[1]))
                except Exception as _e:
                    _swallow(_e, "ui/compact_mode.py _ensure_puck(px)")
            else:
                self._puck.move(self._win.frameGeometry().topLeft())
            # r146: 覚えている置き場所が «今つながっている画面の外» なら
            # 引き戻す。モニタ構成が変わった後に «パックが見えない＝アプリを
            # 見失う» という事故になる（実機 2026-10-13: 保存値 -2108,-510）。
            self._pull_puck_onto_screen()
        return self._puck

    def puck_topmost(self):
        """今パックに掛かっている «常に手前»（None＝パック未作成）。"""
        return getattr(self, "_puck_topmost", None)

    def sync_topmost(self):
        """パックの «常に手前» を本体の最前面設定に合わせる（r146）。

        本体の設定が ON ならパックも手前に、OFF なら普通の窓（他の窓に
        隠れ得る）。ユーザー報告 2026-10-15「最前面モードでなくても
        コンパクトにすると最前面モードと同様の挙動」への対応。"""
        puck = self._puck
        if puck is None:
            return
        try:
            fn = getattr(self._win, "is_always_on_top", None)
            on = bool(fn()) if callable(fn) else False
        except Exception:
            on = False
        if getattr(self, "_puck_topmost", None) == on:
            return
        self._puck_topmost = on
        try:
            from core.win_topmost import set_topmost
            set_topmost(puck, on)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py sync_topmost")

    def _pull_puck_onto_screen(self):
        """パックが画面に «丸ごと» 載っていなければ、一番近い画面の中へ
        最小の移動で入れる（r146、ユーザー指示 2026-10-16）。

        以前は «どこにも載っていなければ主画面の左上» だったので、
        サブ画面のすぐ外に出ただけで主画面へ飛んでいた。パックは
        «触れる» ことが全てなので、はみ出し（半分だけ見えている）も直す。"""
        puck = self._puck
        if puck is None:
            return
        r = self._pull_puck_onto_screen_px()
        if r is not None:
            return
        try:
            g = puck.frameGeometry()
            for sc in QApplication.screens():
                if sc.availableGeometry().contains(g):
                    return
            target = self._clamp(puck.pos(), puck.size())
            if target == puck.pos():
                return
            puck.move(target)
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
            from core.win_geometry import window_rect_px
            pr = window_rect_px(self._puck) if self._native_geometry_on() else None
            self._sm.set(self.PUCK_POS_PX_KEY, [pr[0], pr[1]] if pr else None)
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

    # ── 物理ピクセル（Win32）での置き場所（r146、ユーザー報告 2026-10-23）──
    #
    # 「画面を跨いでコンパクトの開閉をすると、訳がわからないところに閉じます」
    # 画面ごとに DPI が違うと、Qt の論理座標は画面ごとに別の物差しになる。
    # 本体の角（本体が属する画面の物差し）をパック（別の窓）の move() に渡すと、
    # 同じ数字が別の物理位置に化ける（A-17 のカーソル判定と同じ原因）。
    # パックの置き場所・本体の戻し先・退けたかの判定を全部 Win32 の物理
    # ピクセルで行い、Qt の座標は混ぜない。Windows 以外／ネイティブ窓が無い
    # （オフスクリーン）時は None を返して従来の Qt 計算に落ちる。
    # MFM_DIAG=no_native_geometry で切れる（切り分け用）。
    def _native_geometry_on(self) -> bool:
        try:
            from core.win_geometry import is_windows
            return is_windows() and not _diag_off("no_native_geometry")
        except Exception:
            return False

    def _place_puck_at_window(self) -> bool:
        """本体の角にパックを置く。物理ピクセルで置けたら True（Qt は触らない）。"""
        puck = self._ensure_puck()
        if self._native_geometry_on():
            try:
                from core.win_geometry import (window_rect_px, monitors_px,
                                               move_window_px, corner_of,
                                               top_left_for, nearest_work_area,
                                               clamp_into)
                wr, pr, mons = window_rect_px(self._win), window_rect_px(puck), monitors_px()
                if wr and pr and mons and pr[2] > pr[0] and pr[3] > pr[1]:
                    pc, wc = self._corner_names()
                    size = (pr[2] - pr[0], pr[3] - pr[1])
                    tl = top_left_for(pc, corner_of(wr, wc), size)
                    wa = nearest_work_area((tl[0], tl[1], tl[0] + size[0], tl[1] + size[1]), mons)
                    if wa is not None:
                        tl = clamp_into(tl, size, wa)
                    if move_window_px(puck, tl[0], tl[1]):
                        self._win_rect_px = wr
                        self._puck_anchor_px = tl
                        _compact_log("collapse(px): win=%r -> puck=%r work=%r" % (wr, tl, wa))
                        return True
            except Exception as _e:
                _swallow(_e, "ui/compact_mode.py _place_puck_at_window")
        self._win_rect_px = None
        self._puck_anchor_px = None
        puck.move(self._clamp(self._puck_pos_for_window(), puck.size()))
        _compact_log("collapse(qt): win=%r -> puck=%r" % (self._win.frameGeometry(), puck.pos()))
        return False

    def _restore_window_at_puck_px(self) -> bool:
        """畳む前の本体の矩形（物理ピクセル）へ、パックの移動差分だけずらして戻す。"""
        rect = getattr(self, "_win_rect_px", None)
        anchor = getattr(self, "_puck_anchor_px", None)
        if not (self._native_geometry_on() and rect and anchor and self._puck is not None):
            return False
        try:
            from core.win_geometry import window_rect_px, set_window_rect_px
            pr = window_rect_px(self._puck)
            if not pr:
                return False
            dx, dy = pr[0] - anchor[0], pr[1] - anchor[1]
            target = (rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy)
            if set_window_rect_px(self._win, target):
                _compact_log("expand(px): puck=%r anchor=%r -> win=%r" % (pr[:2], anchor, target))
                return True
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _restore_window_at_puck_px")
        return False

    def _pull_puck_onto_screen_px(self):
        """パックが画面に丸ごと載っていなければ一番近い作業領域へ（物理ピクセル）。
        判定できたら True/False（動かしたか）、できなければ None。"""
        if not self._native_geometry_on() or self._puck is None:
            return None
        try:
            from core.win_geometry import (window_rect_px, monitors_px, move_window_px,
                                           rect_contains, nearest_work_area, clamp_into)
            pr, mons = window_rect_px(self._puck), monitors_px()
            if not pr or not mons:
                return None
            if any(rect_contains(m, pr) for m in mons):
                return False
            size = (pr[2] - pr[0], pr[3] - pr[1])
            wa = nearest_work_area(pr, mons)
            if wa is None:
                return None
            tl = clamp_into((pr[0], pr[1]), size, wa)
            if tl != (pr[0], pr[1]) and move_window_px(self._puck, tl[0], tl[1]):
                self.remember_puck_pos()
                _compact_log("pull_puck(px): %r -> %r work=%r" % (pr, tl, wa))
                return True
            return False
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _pull_puck_onto_screen_px")
            return None

    def _is_parked_px(self):
        """本体がどの画面にも載っていないか（物理ピクセル）。判定できなければ None。"""
        if not self._native_geometry_on():
            return None
        try:
            from core.win_geometry import window_rect_px, monitors_px, rect_intersects
            wr, mons = window_rect_px(self._win), monitors_px()
            if not wr or not mons:
                return None
            return not any(rect_intersects(wr, m) for m in mons)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _is_parked_px")
            return None

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
    def _nearest_screen(rect):
        """rect（または点）に一番近い画面（r146、ユーザー指示 2026-10-16）。

        「コンパクトが起動時にどのディスプレイにも入っていない場合は、
          一番近いところに強制的にズレるという仕様が必要」
        載っている画面があればそれ。無ければ、各画面の作業領域までの
        距離（x・y の足りない分の和）が最小の画面。主画面に飛ばさない。"""
        try:
            if isinstance(rect, QPoint):
                rect = QRect(rect, QSize(1, 1))
            best, best_d = None, None
            for sc in QApplication.screens():
                av = sc.availableGeometry()
                if av.intersects(rect):
                    return sc
                dx = max(av.left() - rect.right(), rect.left() - av.right(), 0)
                dy = max(av.top() - rect.bottom(), rect.top() - av.bottom(), 0)
                d = dx + dy
                if best_d is None or d < best_d:
                    best, best_d = sc, d
            return best or QApplication.primaryScreen()
        except Exception:
            return QApplication.primaryScreen()

    @classmethod
    def _clamp(cls, top_left, size):
        """«一番近い画面» からはみ出さない位置に丸める。

        r146: 以前は screenAt(top_left) が外れると主画面に丸めていたので、
        サブ画面の端から少し出ただけで主画面まで飛んでいた。"""
        try:
            sc = cls._nearest_screen(QRect(top_left, size))
            if sc is None:
                return top_left
            av = sc.availableGeometry()
            # QRect の right/bottom は «含む» 端なので +1
            x = min(max(top_left.x(), av.left()),
                    max(av.left(), av.right() + 1 - size.width()))
            y = min(max(top_left.y(), av.top()),
                    max(av.top(), av.bottom() + 1 - size.height()))
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

    def _restore_window_at_puck(self):
        """畳む前に覚えた «中身の矩形» へ本体を戻す（r146 後半）。

        パックを動かされていたら、その差分だけずらして戻す。
        覚えていない時（起動直後にコンパクトを復元した等）だけ、
        従来どおり角を合わせる計算に落ちる。"""
        w = self._win
        rect = getattr(self, "_win_rect", None)
        anchor = getattr(self, "_puck_anchor", None)
        puck = self._puck
        # r146（ユーザー報告 2026-10-23「画面を跨いでコンパクトの開閉をすると、
        # 訳がわからないところに閉じます」）: 画面ごとに DPI が違うと Qt の
        # 論理座標は画面ごとに別の物差しになる。本体とパックは別の窓なので、
        # 同じ数字が別の物理位置に化ける。**物理ピクセル（Win32）で戻す。**
        if self._restore_window_at_puck_px():
            return
        try:
            if rect is not None and anchor is not None and puck is not None:
                d = puck.pos() - anchor
                tl = QPoint(rect.left() + d.x(), rect.top() + d.y())
                # r146（ユーザー報告 2026-10-16）: ここで 1 画面に丸めない。
                # ディスプレイを跨いで置いてある本体が、開くたびに片方の
                # 画面へ押し込まれていた。覚えた矩形をそのまま戻す
                # （どの画面にも載らない時だけ pull_window_onto_screen が拾う）。
                w.setGeometry(QRect(tl, rect.size()))
                return
            target = self._clamp(self._window_pos_for_puck(),
                                 w.frameGeometry().size())
            w.move(target)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _restore_window_at_puck")

    # ── 畳む／開く ───────────────────────────────────────────────
    def collapse(self, during_drag: bool = False):
        """本体を «左上の角» へ畳んでパックだけにする。

        during_drag=True … 自分が始めたドラッグ（QDrag.exec ＝ OLE
        DoDragDrop）の最中に畳む時。**フォアグラウンドを動かさない。**
        r146（ユーザー報告 2026-10-16「コンパクトモードのとき、manager 外
        への D&D が死んでいます。コンパクトに戻しつつも、生きたままに
        してください」）: 畳む時に元のアプリへフォアグラウンドを返すと
        （_return_foreground）、OLE のドラッグはマウスの捕捉を失って
        その場で終わる。本体を画面の外へ置く・道具窓を隠す・パックを
        出す（WA_ShowWithoutActivating）は OLE に影響しないので、それだけ
        行い、フォアグラウンドは **落とし終えてから** 落とし先へ返す
        （_tick → _after_drag_collapsed）。

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
        # r146（ユーザー報告 2026-10-15「コンパクトで再起動すると、必ず画面の
        # 一番左上にでてくる。元々閉じた場所に出て欲しい。」）:
        # 起動時は本体を «出す前に» 画面の外へ退けてある。その状態で
        # «本体の角» からパックの位置を決めると、画面外の座標になり、
        # _clamp で主画面の左上へ引き戻されて、しかもそれが保存されていた。
        # 退けてある時は触らない（覚えている置き場所をそのまま使う）。
        if self._win.isVisible() and not self.is_parked():
            self._place_puck_at_window()
        # r146 後半（ユーザー報告 2026-10-14）:
        #   「コンパクト⇔通常を繰り返すだけで、どんどん UI が左上に
        #     上がっていきます。閉じる起点と開く起点が合っていない証拠では
        #     ないでしょうか。」
        # そのとおり。原因は «枠» と «中身» の取り違えで、しかもこの 2 つの
        # 対応は Qt の版と «今見えているか» で意味が変わる（move() が枠の
        # 位置を指すか中身の位置を指すかが揺れる）。**変換に頼らない。**
        # 畳む時に «中身の矩形» をそのまま覚え、開く時はそれを戻す。
        # パックを動かされていたら、その差分だけずらす。
        # これなら枠の厚みが何 px でも 1 px もずれない。
        try:
            # 退けてある最中（起動時の pre_park）は、覚えた矩形を上書きしない
            # （今の geometry は画面の外。それを戻すと開いた瞬間に画面外へ出る）
            if not self.is_parked():
                self._win_rect = QRect(self._win.geometry())
            self._puck_anchor = QPoint(puck.pos())
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py collapse(remember)")
            self._win_rect = None
            self._puck_anchor = None
        # r146（ユーザー報告 2026-10-16「タスクバーから右クリックで閉じた
        # 場合、画面左上に出ます」）: 畳んでいる間に終了すると、本体の
        # 保存位置が «画面の外（退け先）» になり、次の起動で Qt が画面の
        # 左上へ引き戻す→そこにパックが置かれる。退ける **前** に本体の
        # 位置を保存しておき、閉じる時は退けた位置で上書きしない。
        if not self.is_parked():
            try:
                fn = getattr(self._win, "save_window_geometry", None)
                if callable(fn):
                    fn()
            except Exception as _e:
                _swallow(_e, "ui/compact_mode.py collapse(save geometry)")
        self._expanded = False
        self._hide_tool_windows()
        self._park_window()
        if during_drag:
            self._collapsed_in_drag = True     # 返す先は落とし終えてから決める
        else:
            self._return_foreground()
        import time
        self._collapsed_at = time.time()
        puck.show()
        puck.raise_()
        # r146（ユーザー指示 2026-10-16）: 出す時は必ず «丸ごと見える» 場所に。
        # 本体の角が画面の外にある状態で畳んでも、パックは一番近い画面の中へ。
        self._pull_puck_onto_screen()
        # 開く時の差分の基準は «実際に出した位置»（引き戻した後）
        try:
            self._puck_anchor = QPoint(puck.pos())
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py collapse(anchor)")
        # 出すたびに掛け直す。Qt がネイティブ窓を作り直すと拡張スタイルが
        # 戻ってしまい、またタスクバーに出てしまう（冪等なので安い）。
        try:
            from core.win_topmost import set_taskbar_button
            set_taskbar_button(puck, False)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py collapse(taskbar)")
        self.sync_topmost()
        self.remember_puck_pos()
        # r146: «畳んだのにパックが出ていない» と、アプリごと見失う
        # （本体は隠れている・パックは見えない）。出せなければ畳むのをやめる。
        if not puck.isVisible():
            self._win._log_quiet("compact: パックを出せなかったので通常表示へ戻す")
            self.set_active(False, reason="collapse: パックを出せなかった（安全弁）")
            self.pull_window_onto_screen()

    # ── 畳んでいる間の本体の置き場所（r146 後半）────────────────────
    #
    # 「畳む」をどう実装するかで 3 回つまずいたので、理由を全部残す。
    #
    #   hide()           … タスクバーのボタンごと消える。代わりにパックの
    #                      ボタンが出るので «消えて出て» の点滅になる。
    #                      さらに «コンパクトのまま終了» すると閉じる口が
    #                      パックしか無く、閉じても本体が隠れたまま残って
    #                      プロセスが生き延びる → 次の起動が «既にいる方を
    #                      前に出す» 経路に入り、本体とパックが両方出た。
    #   showMinimized()  … ボタンは残るが、**開く時が最悪**。Windows の
    #                      復帰アニメーションが走り、しかも最小化から戻す
    #                      操作は必ずフォアグラウンドを奪う。
    #                      «パックのいた場所にすっと開く / フォーカスを
    #                      奪わない» という指示を両方壊す。
    #   画面の外へ置く    … ボタンは «見えているウィンドウ» のものとして
    #                      居座り続け、開くのは move() 1 回で済む。
    #                      アニメーションもフォーカスの奪い合いも無い。
    #
    # 3 つ目を採る。
    PARK_MARGIN = 400          # 仮想画面の左端からこれだけ外へ置く

    def _virtual_rect(self):
        try:
            scr = QApplication.primaryScreen()
            if scr is not None:
                return scr.virtualGeometry()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _virtual_rect")
        return QRect(0, 0, 1920, 1080)

    def _park_pos(self):
        """画面の «外» の置き場所（どのモニタにも載らない座標）。"""
        vr = self._virtual_rect()
        try:
            w = max(1, self._win.frameGeometry().width())
        except Exception:
            w = 800
        return QPoint(vr.left() - w - self.PARK_MARGIN, vr.top() + 80)

    def pull_window_onto_screen(self):
        """本体が画面の外にいたら、必ず画面の中へ引き戻す（r146）。

        ユーザー報告 2026-10-15:
          「起動時に画面外に出て戻ってこなくなりました。」
        畳んでいる間は画面の外に置くので、**戻す道が 1 つでも失敗すると
        アプリを見失う**。最後の砦として、見える場所へ引き戻す口を持つ。"""
        w = self._win
        try:
            self._set_win_transitions(True)
            if w.isMinimized():
                w.showNormal()
            if not w.isVisible():
                w.show()
            vr = self._virtual_rect()
            if vr.intersects(w.frameGeometry()):
                return False
            scr = QApplication.primaryScreen()
            av = scr.availableGeometry() if scr is not None else vr
            w.move(av.left() + 40, av.top() + 40)
            w.raise_()
            return True
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py pull_window_onto_screen")
            return False

    PRE_PARK_SAFETY_MS = 3000   # 退けたまま復元が終わらなければ引き戻す

    def pre_park(self):
        """起動時、本体を **出す前に** 画面の外へ置く（r146 後半）。

        ユーザー報告 2026-10-16（A-12）:
          「一瞬本体がでてからコンパクトになります。直接コンパクトが出て
            光るという挙動にしてください。」
        出してから退けると、起動のたびに本体が一瞬ちらついて消える。
        前回コンパクトのまま終了していた時だけ通る道。

        2026-10-15 に一度撤去した（「起動時に画面外に出て戻ってこない」）。
        戻すにあたり **必ず戻れる** ようにする:
          * 開く時に使う «本体の矩形» は、退ける前（復元したジオメトリ）を
            ここで覚える（collapse は退け中なら上書きしない）
          * PRE_PARK_SAFETY_MS 後にパックが出ていなければ、コンパクトを
            諦めて本体を画面の中へ引き戻す（_pre_park_safety）"""
        try:
            self._win_rect = QRect(self._win.geometry())
            pos = self._sm.get(self.PUCK_POS_KEY, None)
            self._puck_anchor = (QPoint(int(pos[0]), int(pos[1]))
                                 if isinstance(pos, (list, tuple)) and len(pos) == 2
                                 else None)
            self._win.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self._set_win_transitions(False)
            self._win.move(self._park_pos())
            self._pre_parked = True
            QTimer.singleShot(self.PRE_PARK_SAFETY_MS, self._pre_park_safety)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py pre_park")

    def _pre_park_safety(self):
        """起動時に退けたまま、パックが出ていなければ引き戻す（見失わない）。

        ユーザー報告 2026-10-16（A-12 再）「コンパクトを展開すると何故か
        コンパクトモードのボタンが勝手に OFF にされる」: 安全弁が «パックが
        見えているか» だけで判定していたので、3 秒以内にユーザーが開く
        （＝パックは隠れる）と失敗扱いにしてコンパクトを解除していた。
        介入するのは **本体が退けたままで、誰も見えていない** 時だけ:
          * コンパクト有効で開いている → 正常
          * コンパクト有効で畳んでいてパックが見える → 正常
          * それ以外（畳んでいるのにパックが無い／無効なのに退けたまま）→ 介入"""
        try:
            puck = self._puck
            if self._active:
                if self._expanded:
                    return
                if puck is not None and puck.isVisible():
                    return
            elif not self.is_parked():
                return
            self._win._log_quiet("compact: 起動時の復元が終わらないので本体を画面へ戻す")
            if self._active:
                self.set_active(False, reason="_pre_park_safety: 起動 3 秒でパックが見えない（安全弁）")
            self.pull_window_onto_screen()
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _pre_park_safety")

    def _park_window(self):
        """本体を画面の外へ退ける（隠さない・最小化しない）。"""
        w = self._win
        try:
            # r146（ユーザー報告 2026-10-16）: 画面の外に居る間は、この窓の
            # 最小化／復元アニメーションを止める。タスクバーのボタンを押す
            # たびに «画面外の矩形 ⇔ タスクバー» を結ぶ飛行が見えていた。
            self._set_win_transitions(False)
            if w.isMinimized():
                # 最小化されたまま畳みに来た時だけ、いったん普通に戻す
                w.setAttribute(Qt.WA_ShowWithoutActivating, True)
                w.showNormal()
            if not w.isVisible():
                w.setAttribute(Qt.WA_ShowWithoutActivating, True)
                w.show()
            w.move(self._park_pos())
            # r146: 論理座標の退け先が、DPI の違う画面の上に化けることがある。
            # 物理ピクセルで «どの画面にも載っていない» を確かめ、載って
            # いれば物理ピクセルで左の外へ出す。
            if self._is_parked_px() is False:
                from core.win_geometry import (window_rect_px, monitors_px,
                                               move_window_px)
                wr, mons = window_rect_px(w), monitors_px()
                if wr and mons:
                    left = min(m[0] for m in mons)
                    top = min(m[1] for m in mons)
                    move_window_px(w, left - (wr[2] - wr[0]) - self.PARK_MARGIN * 2,
                                   top + 80)
                    _compact_log("park(px): 論理の退け先が画面に載っていたので物理で退けた %r"
                                 % (window_rect_px(w),))
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _park_window")

    def _set_win_transitions(self, enabled: bool):
        """本体の最小化／復元アニメーションの入切（畳んでいる間だけ切る）。"""
        if getattr(self, "_win_transitions", None) == bool(enabled):
            return
        self._win_transitions = bool(enabled)
        try:
            from core.win_topmost import set_window_transitions
            set_window_transitions(self._win, bool(enabled))
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _set_win_transitions")

    def is_parked(self) -> bool:
        """本体が画面の外に退けてあるか（＝畳んでいる）。"""
        try:
            w = self._win
            if not w.isVisible() or w.isMinimized():
                return True
            px = self._is_parked_px()
            if px is not None:
                return px
            return not self._virtual_rect().intersects(w.frameGeometry())
        except Exception:
            return False

    def _after_drag_collapsed(self):
        """ドラッグの最中に畳んだ後、落とし終えた時の後始末（r146）。

        フォアグラウンドがまだこちら（画面の外の本体）にあれば、落とした
        場所＝カーソルの下の他アプリの窓へ返す。そこが取れなければ、開く
        前に前にいたアプリへ返す。本体は開かない・活性化しない。"""
        self._collapsed_in_drag = False
        try:
            from core.win_topmost import (app_is_foreground, set_foreground,
                                          foreign_window_at_cursor)
            if not app_is_foreground():
                self._prev_foreground = 0
                return
            target = foreign_window_at_cursor() or getattr(self, "_prev_foreground", 0)
            self._prev_foreground = 0
            if target:
                set_foreground(target)
        except Exception as _e:
            _swallow(_e, "ui/compact_mode.py _after_drag_collapsed")

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
        # r146（ユーザー報告 2026-10-16「畳まれた後に開いた場合、設定関連の
        # UI だけ中央に戻る」）: 隠す前に «中身の矩形» を覚える。QDialog は
        # 動かされた印（WA_Moved）が無いと show のたびに親の中央へ置き直す。
        # 本体と同じく、変換に頼らず setGeometry で元の場所へ戻す。
        self._tool_windows = []
        for w in self._tool_window_list():
            try:
                rect = QRect(w.geometry())
                w.hide()
                self._tool_windows.append((w, rect))
            except RuntimeError:
                pass                      # 破棄済み
            except Exception as _e:
                _swallow(_e, "ui/compact_mode.py _hide_tool_windows")

    def _show_tool_windows(self):
        kept, self._tool_windows = self._tool_windows, []
        for item in kept:
            w, rect = item if isinstance(item, tuple) else (item, None)
            try:
                if rect is not None:
                    w.setAttribute(Qt.WA_Moved, True)   # 中央へ置き直させない
                w.show()
                if rect is not None and w.geometry() != rect:
                    w.setGeometry(rect)
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
        self._collapsed_in_drag = False   # 開いたら «ドラッグ中に畳んだ» は終わり
        if not self._active or self._expanded:
            return
        self._expanded = True
        puck = self._ensure_puck()
        w = self._win
        # 1) 出す前に置き場所を決める（出してから動かさない）
        self._restore_window_at_puck()
        self._set_win_transitions(True)
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
        # 畳んでいる間も «見えている»（画面の外に置いてあるだけ）ので、
        # ここでやるのは位置を戻すことだけ。show() をやり直すと、
        # Windows が «新しく出た窓» として扱って前へ出してしまう。
        if w.isMinimized() or not w.isVisible():
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
        # r146（ユーザー報告 2026-10-16「ディスプレイを跨いでいると、別
        # ディスプレイにマウスが移動するとコンパクトに戻る」）: 画面ごとに
        # DPI が違うと Qt の論理座標は画面ごとに別物になり、跨いだ窓の
        # 向こう側が «外» に見える。Windows では物理ピクセルで判定する。
        if not _diag_off("no_native_cursor"):
            try:
                from core.win_topmost import cursor_inside_any
                r = cursor_inside_any(wins, m)
                if r is not None:
                    return bool(r)
            except Exception as _e:
                _swallow(_e, "ui/compact_mode.py _cursor_inside(native)")
        for w in wins:
            if not w.isVisible():
                continue
            g = w.frameGeometry()
            if QRect(g.left() - m, g.top() - m,
                     g.width() + 2 * m, g.height() + 2 * m).contains(pos):
                return True
        return False

    # r146（ユーザー指示 2026-10-15）: «文字入力中は畳まない» は撤去した。
    # 「マネージャー関連のウィンドウのどれがアクティブでも、全てから
    #   マウスが離れているとコンパクトにしてください」という指示と
    # ぶつかるため（Maya の起動設定を開いたまま離れても畳まれなかった）。
    # 判断は _cursor_inside() 一本に絞る。

    def _tick(self):
        if not self._active:
            return
        # 自分のドラッグ（QDrag.exec）の進行中／終わった直後を先に見る。
        # 畳んだ後は下の «畳んでいる間» で return するので、ここで見ないと
        # 落とし終えた後始末（フォアグラウンドを落とし先へ返す）が走らない。
        try:
            from ui.browser_util import mfm_drag_active
            dragging = mfm_drag_active()
        except Exception:
            dragging = False
        if not dragging and self._drag_seen:
            self._drag_seen = False
            if self._collapsed_in_drag:
                self._after_drag_collapsed()
                return
        if not self._expanded:
            # 畳んでいる間: パックのアイコンにカーソルが乗っていれば開く
            # （enterEvent 任せにしない。クラス _OpenButton.arm_if_hovered）
            try:
                puck = self._puck
                if puck is not None and puck.isVisible():
                    puck.btn.arm_if_hovered()
            except Exception as _e:
                _swallow(_e, "ui/compact_mode.py _tick(hover)")
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
        # r146（ユーザー報告 2026-10-16「コンパクトモードのとき、manager 外
        # への D&D が死んでいます」）: **自分が始めたドラッグ** の最中は、
        # カーソルが外へ出たら畳むが、フォアグラウンドには触らない
        # （collapse(during_drag=True)）。落とし終えたら、落とし先へ
        # フォアグラウンドを返して畳んだままにする（本体は開かない。
        # 「ドロップした後は manager のアクティブな状況は残さず
        #   コンパクト化したままで OK」）。
        if dragging:
            self._drag_seen = True
            if not self._cursor_inside():
                self.collapse(during_drag=True)
            return
        if getattr(self, "_drag_until", 0.0):
            import time
            if time.time() < self._drag_until:
                # ドラッグで開いた直後。本体はアクティブではないので
                # «乗っているか» だけで判断すると落とす前に畳んでしまう。
                if self._cursor_inside():
                    self._drag_until = 0.0      # 中に入った＝猶予は役目を終えた
                return
            self._drag_until = 0.0
        # r146（ユーザー指示 2026-10-15）:
        #   「マネージャー関連のウィンドウのどれがアクティブでも、
        #     全てからマウスが離れているとコンパクトにしてください。」
        # _typing()（文字入力中は畳まない）はこれと真っ向からぶつかるので
        # **外した**。判断の基準は «マウスがどこにあるか» 1 本にする
        # （_cursor_inside は本体と道具窓を全部見ている）。
        if self._busy() or self._cursor_inside():
            return
        self.collapse()
