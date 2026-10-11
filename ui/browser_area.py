"""
Browser Area
============
「プリセット行＋ブックマーク／履歴サイドバー＋カラムブラウザ」を1ユニット
（＝メインUIの赤枠部分）として扱う複合ウィジェット。

MainWindow はこれを縦スプリッタに複数積み、追加・削除・並び替えできる。
各エリアの操作（ナビ・ブックマーククリック・プリセット等）は自エリアの
ブラウザにだけ作用する（別エリアに飛ばない）。状態（パス・分割幅）は
get_state()/apply_state() で保存・復元する。

レイアウト: エリア操作（番号＋追加/移動/削除）はUI最左の «縦ストリップ»。
プリセット行・本体はその右。エリアごとのアクセントカラーはストリップと
プリセットボタンの両方に適用される。
"""
from core.diag import swallow as _swallow  # r112

import os

from core.compat import (
    Qt, Signal, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QToolButton, QSplitter, QFrame, QPainter, QColor, QRect, QSize,
    QPixmap, QIcon, QSizePolicy, QPen,
)
from core.i18n import tr


class _VerticalLabel(QLabel):
    """縦書き（90度回転）ラベル。エリア番号表示用。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._pen_color = None

    def set_pen_color(self, color: str):
        self._pen_color = color
        self.update()

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.height(), s.width())

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        return QSize(s.height(), s.width())

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            if self._pen_color:
                painter.setPen(QColor(self._pen_color))
            f = self.font()
            f.setBold(True)
            painter.setFont(f)
            painter.rotate(-90)
            painter.drawText(
                QRect(-self.height(), 0, self.height(), self.width()),
                Qt.AlignCenter, self.text())
        finally:
            painter.end()


class _AreaTab(QFrame):
    """縦ストリップに並ぶ «エリアのタブ»（r130）。

    タブ表示モードの時だけ出る。色はエリアのアクセントをそのまま使うので、
    並列表示の時と «どのエリアか» の見分けが変わらない。
    どのタブも縦書きのラベルを出し、選択中は太字＋左のアクセント線を太くする
    （r144: 非選択が無地だと «どのエリアか» が読めないため）。"""

    clicked = Signal(int)

    def __init__(self, index, label, bg, fg, line, active, parent=None):
        super().__init__(parent)
        self._index = int(index)
        self._label = label or ""
        self._bg, self._fg, self._line = bg, fg, line
        self._active = bool(active)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(label)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setMinimumHeight(34)
        self.setFixedWidth(22)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self._index)
        super().mousePressEvent(e)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            r = self.rect().adjusted(0, 1, -1, -1)
            col = QColor(self._bg)
            if not self._active:
                # 非選択は «引っ込んでいる» 見た目にする
                col.setAlphaF(max(0.0, min(1.0, col.alphaF() * 0.55)))
            painter.setPen(Qt.NoPen)
            painter.setBrush(col)
            painter.drawRoundedRect(r, 3, 3)
            # 左のアクセント線（選択中は太く）
            w = 3 if self._active else 2
            painter.setBrush(QColor(self._line))
            painter.drawRect(QRect(r.left(), r.top(), w, r.height()))
            if self._label:
                # r144: 非選択タブも名前を出す（ユーザー指摘 2026-10-08
                # 「アクティブなタブ以外が文字が無い」）。«どのエリアか» が
                # 読めないとタブとして機能しない。選択中は太字＋全不透明、
                # 非選択は少し落とすだけで «読める» ことは保証する。
                fg = QColor(self._fg)
                if not self._active:
                    fg.setAlphaF(max(0.0, min(1.0, fg.alphaF() * 0.78)))
                painter.setPen(fg)
                f = self.font()
                f.setBold(self._active)
                painter.setFont(f)
                painter.rotate(-90)
                painter.drawText(QRect(-r.bottom(), r.left(),
                                       r.height(), r.width()),
                                 Qt.AlignCenter, self._label)
        finally:
            painter.end()


def _view_mode_icon(shows: str, color: str) -> QIcon:
    """モード切替ボタンのアイコンを描く（r130）。

    shows=\"tabs\"  … タブらしい絵（左に縦の短冊が 3 枚、右に 1 枚の板）。
                      **並列表示中**に出して «押すとタブになる» ことを示す。
    shows=\"split\" … 並列らしい絵（板を横線で 3 段に割った形）。
                      **タブ表示中**に出して «押すと並列になる» ことを示す。
    """
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    pt = QPainter(pm)
    try:
        pt.setRenderHint(QPainter.Antialiasing, False)
        c = QColor(color)
        pen = QPen(c)
        pen.setWidth(1)
        pt.setPen(pen)
        if shows == "tabs":
            # 左: 縦に積んだタブ 3 枚 / 右: 中身 1 枚
            for i in range(3):
                y = 2 + i * 4
                rr = QRect(2, y, 4, 3)
                if i == 1:
                    pt.fillRect(rr, c)      # 選択中のタブ
                else:
                    pt.drawRect(rr)
            pt.drawRect(QRect(8, 2, 6, 11))
        else:
            # 板を横線で 3 段に割る（縦積み＝並列）
            pt.drawRect(QRect(2, 2, 12, 11))
            pt.drawLine(2, 6, 14, 6)
            pt.drawLine(2, 10, 14, 10)
    finally:
        pt.end()
    return QIcon(pm)


def _with_alpha(rgba: str, factor: float) -> str:
    """"rgba(r, g, b, a)" の a を factor 倍した色を返す（0..1 にクランプ）。
    エリアのアクセント罫線から hover/pressed の面色を作るのに使う。"""
    try:
        body = rgba[rgba.index("(") + 1:rgba.rindex(")")]
        r, g, b, a = [x.strip() for x in body.split(",")]
        a = max(0.0, min(1.0, float(a) * factor))
        return "rgba(%s, %s, %s, %.3f)" % (r, g, b, a)
    except Exception:
        return rgba


class BrowserArea(QWidget):

    # MainWindow へ中継するシグナル
    file_activated = Signal(str)
    directory_changed = Signal(str)
    status_message = Signal(str)
    bookmark_requested = Signal(list)
    batch_rename_requested = Signal(list)      # r101
    # エリア操作（引数=自分自身）
    add_below_requested = Signal(object)
    remove_requested = Signal(object)
    move_up_requested = Signal(object)
    move_down_requested = Signal(object)
    # r130: エリア表示の切替（並列 ⇄ タブ）とタブの選択
    view_mode_toggle_requested = Signal()
    tab_selected = Signal(int)

    def __init__(self, settings_manager, thumb_mgr, bookmark_mgr, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        self._bm_mgr = bookmark_mgr

        # 循環import回避のため遅延import（main_window は本モジュールを import する）
        from ui.browser_panel import BrowserPanel
        from ui.bookmark_panel import BookmarkPanel
        from ui.main_window import HistoryPanel, QuickNavBar

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 左端: エリア操作の縦ストリップ ──────────────────────────
        strip = QFrame(self)
        strip.setObjectName("mfmAreaStrip")
        strip.setFixedWidth(30)
        self._strip = strip
        sl = QVBoxLayout(strip)
        sl.setContentsMargins(2, 4, 2, 6)
        sl.setSpacing(4)

        def _btn(text, tip, slot):
            b = QToolButton(strip)
            b.setText(text)
            b.setToolTip(tip)
            b.setFixedSize(24, 22)
            b.clicked.connect(slot)
            sl.addWidget(b, 0, Qt.AlignHCenter)
            return b

        self._add_btn = _btn(
            "＋", tr("このエリアの下に新しいエリアを追加", "Add a new area below"),
            lambda _c=False: self.add_below_requested.emit(self))
        self._up_btn = _btn("▲", tr("このエリアを上へ移動", "Move area up"),
                            lambda _c=False: self.move_up_requested.emit(self))
        self._down_btn = _btn("▼", tr("このエリアを下へ移動", "Move area down"),
                              lambda _c=False: self.move_down_requested.emit(self))
        self._close_btn = _btn("✕", tr("このエリアを閉じる", "Close this area"),
                               lambda _c=False: self.remove_requested.emit(self))
        sl.addStretch(1)

        # r130: 並列表示時は従来通り «エリア N» の縦ラベル。
        # タブ表示時はここが «全エリアのタブ列» に差し替わる。
        self._title = _VerticalLabel(tr("エリア 1", "Area 1"), strip)
        sl.addWidget(self._title, 0, Qt.AlignHCenter)
        self._tab_box = QWidget(strip)
        self._tab_lay = QVBoxLayout(self._tab_box)
        self._tab_lay.setContentsMargins(0, 0, 0, 0)
        self._tab_lay.setSpacing(3)
        self._tab_box.hide()
        sl.addWidget(self._tab_box, 8, Qt.AlignHCenter)
        self._tabs = []

        # r130: 表示方式の切替（タブ列の真下）。ユーザー指示 2026-10-08。
        self._mode_btn = QToolButton(strip)
        self._mode_btn.setFixedSize(24, 22)
        self._mode_btn.setAutoRaise(False)
        self._mode_btn.clicked.connect(
            lambda _c=False: self.view_mode_toggle_requested.emit())
        sl.addWidget(self._mode_btn, 0, Qt.AlignHCenter)
        self._view_mode = "split"
        self._update_mode_btn()
        sl.addStretch(1)
        outer.addWidget(strip, 0)

        # ── 右側: プリセット行＋本体 ─────────────────────────────────
        body = QVBoxLayout()
        # 縦ストリップ（Area表示）との間の余白は body 全体で確保する。
        # プリセット行だけに付けると下のパネル群と左端が揃わず見辛い。
        body.setContentsMargins(6, 0, 0, 0)
        body.setSpacing(0)

        self.quick_nav = QuickNavBar(self._sm, parent=self)
        body.addWidget(self.quick_nav, 0)

        self._split = QSplitter(Qt.Horizontal, self)
        self._split.setChildrenCollapsible(False)
        self._split.setHandleWidth(3)

        self._side = QSplitter(Qt.Vertical, self._split)
        # r133: フォルダの開閉を覚えるために設定を渡す
        self.bookmark_panel = BookmarkPanel(self._bm_mgr, parent=self,
                                            settings_manager=self._sm)
        self.history_panel = HistoryPanel(self._sm, parent=self)
        self._side.addWidget(self.bookmark_panel)
        self._side.addWidget(self.history_panel)
        self._side.setMinimumWidth(160)
        # r144: 縦に積んだエリアが «ある高さから縮まない» のは、ここの
        # 子ウィジェットの最小高が積み上がるため。縮めても壊れない下限を
        # 明示して、ウィンドウを小さくできるようにする。
        self._side.setMinimumHeight(60)
        self.bookmark_panel.setMinimumHeight(24)
        self.history_panel.setMinimumHeight(24)
        self._side.setChildrenCollapsible(True)

        self.browser = BrowserPanel(self._sm, thumb_mgr, parent=self)

        self.browser.setMinimumHeight(80)
        self._split.addWidget(self._side)
        self._split.addWidget(self.browser)
        self._split.setStretchFactor(0, 0)
        self._split.setStretchFactor(1, 1)
        self._split.setSizes([260, 1200])
        body.addWidget(self._split, 1)
        outer.addLayout(body, 1)
        # r144: エリア自身の下限。_areas_split は collapsible=False なので、
        # ここを明示しないと «エリアの数 × 自然な最小高» がウィンドウの
        # 下限になってしまう。
        self.setMinimumHeight(120)

        # ── エリア内配線（操作は自エリアに閉じる） ─────────────────────
        # r95: プリセット毎に «最後に表示していたディレクトリ» を覚え、
        # 切り替え時に復元する（エリア毎に独立。状態保存にも含める）
        self._preset_paths = {}
        self._restoring_state = False
        self.quick_nav.preset_changed.connect(self._on_preset_switched)
        self.quick_nav.navigate_requested.connect(self.browser.navigate_to)
        self.bookmark_panel.navigate_requested.connect(self.browser.navigate_to)
        self.history_panel.navigate_requested.connect(self.browser.navigate_to)
        self.browser.file_activated.connect(self.file_activated.emit)
        self.browser.directory_changed.connect(self.directory_changed.emit)
        self.browser.status_message.connect(self.status_message.emit)
        self.browser.bookmark_requested.connect(self.bookmark_requested.emit)
        self.browser.batch_rename_requested.connect(self.batch_rename_requested.emit)

        self._apply_accent(0)

    # ------------------------------------------------------------------
    # 表示・状態
    # ------------------------------------------------------------------

    # エリアごとの色味は design_tokens.json の area_accents（Mercury: 低彩度で
    # 同じトーンの色相違いに揃える）。(背景, 文字色, アクセント線)

    def _apply_accent(self, i: int):
        from core.theme_engine import area_accents, qss_vars
        accents = area_accents()
        bg, fg, line = accents[i % len(accents)]
        hover = _with_alpha(line, 0.5)     # 罫線より薄い面
        pressed = _with_alpha(line, 1.3)   # 罫線より濃い面
        v = dict(qss_vars(), bg=bg, fg=fg, line=line, hover=hover, pressed=pressed)
        # 縦ストリップ（エリア操作）
        self._strip.setStyleSheet(
            "#mfmAreaStrip{background:%(bg)s;border-right:1px solid %(line)s;}"
            "QToolButton{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:%(r_s)spx;}"
            "QToolButton:hover{background:%(hover)s;}"
            % v
        )
        self._title.set_pen_color(fg)
        if getattr(self, "_mode_btn", None) is not None:
            self._update_mode_btn()
        # プリセット行: 視認性の階層では «脇役»（r56）。太字・塗りを廃し、
        # エリアのアクセントは文字色とヘアラインにだけ乗せる（Mercury のピル）。
        # 🔗リンクボタンは状態表示のため自前スタイル（#mfmPresetLink）を優先。
        self.quick_nav.setStyleSheet(
            "QLabel{color:%(on_surface_dim)s;font-size:%(label_px)spx;}"
            "QToolButton{background:%(fill_subtle)s;color:%(fg)s;"
            "border:1px solid %(hairline)s;border-radius:%(r_pill)spx;"
            "padding:1px 12px;min-height:18px;font-size:%(label_px)spx;}"
            "QToolButton:hover{background:%(hover)s;border-color:%(line)s;}"
            "QToolButton:pressed{background:%(pressed)s;}"
            "QComboBox{background:transparent;color:%(fg)s;font-size:%(label_px)spx;"
            "border:1px solid %(hairline)s;border-radius:%(r_pill)spx;"
            "padding:1px 8px;min-height:22px;}"
            % v
        )
        # 🔗 のスタイルは QuickNavBar 側で objectName 指定済み（親のQSSより優先）

    def _on_preset_switched(self, prev: str, new: str):
        """プリセット切替: 直前のプリセットに «今のディレクトリ» を記録し、
        切替先に記録があればそこへ移動する（無ければ現在地のまま）。"""
        if self._restoring_state:
            return
        try:
            cur = self.browser.current_path()
            if prev and cur and os.path.isdir(cur):
                self._preset_paths[prev] = cur
            nxt = self._preset_paths.get(new)
            if nxt and os.path.isdir(nxt) and \
                    os.path.normcase(os.path.abspath(nxt)) != \
                    os.path.normcase(os.path.abspath(cur or "")):
                self.browser.navigate_to(nxt)
        except Exception as _e:
            _swallow(_e, "ui/browser_area.py:235 _on_preset_switched")

    def set_index(self, i: int, count: int):
        """番号表示・色味と、移動/削除ボタンの有効状態を更新する。"""
        self._title.setText(tr("エリア %d", "Area %d") % (i + 1))
        self._apply_accent(i)
        self._up_btn.setEnabled(i > 0)
        self._down_btn.setEnabled(i < count - 1)
        self._close_btn.setEnabled(count > 1)

    # ------------------------------------------------------------------
    # 表示方式（並列 ⇄ タブ）— r130
    # ------------------------------------------------------------------

    def _mode_colors(self):
        from core.theme_engine import qss_vars
        v = qss_vars()
        return v.get("on_surface", "#ddd"), v

    def _update_mode_btn(self):
        """«次に切り替わる側» の絵を出す。

        並列表示中 → タブらしいアイコン / タブ表示中 → 並列らしいアイコン。"""
        shows = "tabs" if self._view_mode == "split" else "split"
        fg, _v = self._mode_colors()
        try:
            self._mode_btn.setIcon(_view_mode_icon(shows, fg))
            self._mode_btn.setIconSize(QSize(16, 16))
        except Exception as _e:
            _swallow(_e, "ui/browser_area.py _update_mode_btn")
        self._mode_btn.setToolTip(
            tr("タブ表示に切り替える", "Switch to tabs")
            if shows == "tabs" else
            tr("並列表示に切り替える", "Switch to stacked areas"))

    def set_view_mode(self, mode: str):
        self._view_mode = "tabs" if mode == "tabs" else "split"
        tabs = self._view_mode == "tabs"
        self._title.setVisible(not tabs)
        self._tab_box.setVisible(tabs)
        # ボタンの意味は変わらない（追加・上へ・下へ・閉じる）が、
        # タブ表示中は «表示中のタブ» に対する操作なので文言を合わせる。
        if tabs:
            self._add_btn.setToolTip(tr("タブを追加", "Add a tab"))
            self._up_btn.setToolTip(tr("このタブを上へ移動", "Move this tab up"))
            self._down_btn.setToolTip(tr("このタブを下へ移動", "Move this tab down"))
            self._close_btn.setToolTip(tr("このタブを削除", "Close this tab"))
        else:
            self._add_btn.setToolTip(
                tr("このエリアの下に新しいエリアを追加", "Add a new area below"))
            self._up_btn.setToolTip(tr("このエリアを上へ移動", "Move area up"))
            self._down_btn.setToolTip(tr("このエリアを下へ移動", "Move area down"))
            self._close_btn.setToolTip(tr("このエリアを閉じる", "Close this area"))
        self._update_mode_btn()

    def set_tabs(self, labels, active: int):
        """タブ列を作り直す。labels は全エリア分の表示名。"""
        from core.theme_engine import area_accents
        accents = area_accents()
        for t in self._tabs:
            try:
                t.setParent(None)
                t.deleteLater()
            except Exception as _e:
                _swallow(_e, "ui/browser_area.py set_tabs(clear)")
        self._tabs = []
        for i, label in enumerate(labels):
            bg, fg, line = accents[i % len(accents)]
            t = _AreaTab(i, label, bg, fg, line, i == active, self._tab_box)
            t.clicked.connect(self.tab_selected.emit)
            self._tab_lay.addWidget(t)
            self._tabs.append(t)

    def get_state(self) -> dict:
        try:
            cur = self.browser.current_path()
            active = self.quick_nav.active_preset()
            paths = dict(self._preset_paths)
            if active and cur and os.path.isdir(cur):
                paths[active] = cur          # 現在のプリセットの分も確定させる
            return {
                "path": cur,
                "main_split": [int(v) for v in self._split.sizes()],
                "side_split": [int(v) for v in self._side.sizes()],
                "preset": active,
                "preset_paths": paths,       # r95: プリセット毎の最後の表示先
            }
        except Exception:
            return {}

    def apply_state(self, state: dict):
        if not isinstance(state, dict):
            return
        try:
            ms = state.get("main_split")
            if ms and len(ms) == 2 and sum(ms) > 0:
                self._split.setSizes([int(v) for v in ms])
            ss = state.get("side_split")
            if ss and len(ss) == 2 and sum(ss) > 0:
                self._side.setSizes([int(v) for v in ss])
            pp = state.get("preset_paths")
            if isinstance(pp, dict):
                self._preset_paths = {str(k): str(v) for k, v in pp.items()
                                      if isinstance(v, str) and v}
            preset = state.get("preset")
            self._restoring_state = True
            try:
                if preset:
                    # 復元時は保存済みの path を優先する（切替復元で上書きしない）
                    self.quick_nav.set_active_preset(preset, notify=False)
            finally:
                self._restoring_state = False
            path = state.get("path")
            if path and os.path.isdir(path):
                self.browser.navigate_to(path)
        except Exception as _e:
            _swallow(_e, "ui/browser_area.py:288 apply_state")
