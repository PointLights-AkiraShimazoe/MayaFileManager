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

        _btn("＋", tr("このエリアの下に新しいエリアを追加", "Add a new area below"),
             lambda _c=False: self.add_below_requested.emit(self))
        self._up_btn = _btn("▲", tr("このエリアを上へ移動", "Move area up"),
                            lambda _c=False: self.move_up_requested.emit(self))
        self._down_btn = _btn("▼", tr("このエリアを下へ移動", "Move area down"),
                              lambda _c=False: self.move_down_requested.emit(self))
        self._close_btn = _btn("✕", tr("このエリアを閉じる", "Close this area"),
                               lambda _c=False: self.remove_requested.emit(self))
        sl.addStretch(1)
        self._title = _VerticalLabel(tr("エリア 1", "Area 1"), strip)
        sl.addWidget(self._title, 0, Qt.AlignHCenter)
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
        self.bookmark_panel = BookmarkPanel(self._bm_mgr, parent=self)
        self.history_panel = HistoryPanel(self._sm, parent=self)
        self._side.addWidget(self.bookmark_panel)
        self._side.addWidget(self.history_panel)
        self._side.setMinimumWidth(160)

        self.browser = BrowserPanel(self._sm, thumb_mgr, parent=self)

        self._split.addWidget(self._side)
        self._split.addWidget(self.browser)
        self._split.setStretchFactor(0, 0)
        self._split.setStretchFactor(1, 1)
        self._split.setSizes([260, 1200])
        body.addWidget(self._split, 1)
        outer.addLayout(body, 1)

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
