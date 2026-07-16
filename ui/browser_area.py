"""
Browser Area
============
「プリセット行＋ブックマーク／履歴サイドバー＋カラムブラウザ」を1ユニット
（＝メインUIの赤枠部分）として扱う複合ウィジェット。

MainWindow はこれを縦スプリッタに複数積み、追加・削除・並び替えできる。
各エリアの操作（ナビ・ブックマーククリック・プリセット等）は自エリアの
ブラウザにだけ作用する（別エリアに飛ばない）。状態（パス・分割幅）は
get_state()/apply_state() で保存・復元する。
"""

import os

from core.compat import (
    Qt, Signal, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QToolButton, QSplitter, QFrame,
)


class BrowserArea(QWidget):

    # MainWindow へ中継するシグナル
    file_activated = Signal(str)
    directory_changed = Signal(str)
    status_message = Signal(str)
    bookmark_requested = Signal(list)
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

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── エリアヘッダ（番号ラベル＋操作ボタン） ──────────────────
        hdr = QFrame(self)
        hdr.setObjectName("mfmAreaHdr")
        self._hdr = hdr
        self._apply_accent(0)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(4)
        self._title = QLabel("エリア 1", hdr)
        hl.addWidget(self._title)

        def _btn(text, tip, slot):
            b = QToolButton(hdr)
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            hl.addWidget(b)
            return b

        # 操作ボタンは「エリアN」表示のすぐ右隣に置く（右端ではなく）
        _btn("＋", "このエリアの下に新しいエリアを追加",
             lambda _c=False: self.add_below_requested.emit(self))
        self._up_btn = _btn("▲", "このエリアを上へ移動",
                            lambda _c=False: self.move_up_requested.emit(self))
        self._down_btn = _btn("▼", "このエリアを下へ移動",
                              lambda _c=False: self.move_down_requested.emit(self))
        self._close_btn = _btn("✕", "このエリアを閉じる",
                               lambda _c=False: self.remove_requested.emit(self))
        hl.addStretch(1)
        root.addWidget(hdr, 0)

        # ── プリセット（クイックナビ）行 ─────────────────────────────
        self.quick_nav = QuickNavBar(self._sm, parent=self)
        root.addWidget(self.quick_nav, 0)

        # ── 本体: [ブックマーク/履歴] | ブラウザ ──────────────────────
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
        root.addWidget(self._split, 1)

        # ── エリア内配線（操作は自エリアに閉じる） ─────────────────────
        self.quick_nav.navigate_requested.connect(self.browser.navigate_to)
        self.bookmark_panel.navigate_requested.connect(self.browser.navigate_to)
        self.history_panel.navigate_requested.connect(self.browser.navigate_to)
        self.browser.file_activated.connect(self.file_activated.emit)
        self.browser.directory_changed.connect(self.directory_changed.emit)
        self.browser.status_message.connect(self.status_message.emit)
        self.browser.bookmark_requested.connect(self.bookmark_requested.emit)

    # ------------------------------------------------------------------
    # 表示・状態
    # ------------------------------------------------------------------

    # エリアごとの色味（ダークテーマ内で判別できる控えめなアクセント）
    # (ヘッダ背景, タイトル文字色, 下線色)
    _ACCENTS = [
        ("rgba(38, 48, 68, 235)", "#9db8e0", "rgba(90, 130, 200, 160)"),   # 青
        ("rgba(36, 54, 42, 235)", "#a5d6b0", "rgba(90, 170, 110, 160)"),   # 緑
        ("rgba(52, 42, 64, 235)", "#c5b3e6", "rgba(150, 110, 200, 160)"),  # 紫
        ("rgba(62, 52, 34, 235)", "#e6cf9e", "rgba(200, 160, 80, 160)"),   # 琥珀
        ("rgba(34, 56, 56, 235)", "#9fd8d4", "rgba(80, 170, 165, 160)"),   # 青緑
        ("rgba(60, 40, 46, 235)", "#e0a9b6", "rgba(200, 110, 130, 160)"),  # 薔薇
    ]

    def _apply_accent(self, i: int):
        bg, fg, line = self._ACCENTS[i % len(self._ACCENTS)]
        self._hdr.setStyleSheet(
            "#mfmAreaHdr{background:%s;border-bottom:2px solid %s;}"
            "QLabel{color:%s;font-weight:bold;padding:0 6px;}"
            "QToolButton{background:rgba(55,55,55,235);color:#ddd;"
            "border:1px solid rgba(110,110,110,150);border-radius:3px;"
            "min-width:24px;min-height:18px;}"
            "QToolButton:hover{background:rgba(90,90,90,235);}"
            % (bg, line, fg)
        )

    def set_index(self, i: int, count: int):
        """ヘッダの番号表示・色味と、移動/削除ボタンの有効状態を更新する。"""
        self._title.setText("エリア %d" % (i + 1))
        self._apply_accent(i)
        self._up_btn.setEnabled(i > 0)
        self._down_btn.setEnabled(i < count - 1)
        self._close_btn.setEnabled(count > 1)

    def get_state(self) -> dict:
        try:
            return {
                "path": self.browser.current_path(),
                "main_split": [int(v) for v in self._split.sizes()],
                "side_split": [int(v) for v in self._side.sizes()],
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
            path = state.get("path")
            if path and os.path.isdir(path):
                self.browser.navigate_to(path)
        except Exception:
            pass
