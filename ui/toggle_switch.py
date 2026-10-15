# -*- coding: utf-8 -*-
"""ON/OFF のスイッチ（r146）。

丸いノブが左右へ動くピル。ON は右側＋トラックがアクセント色、OFF は左側。
QToolButton の «チェック可能» を文字（ON/OFF）で表していた箇所の置き換え用
（ユーザー報告 2026-10-15「高度なコピーの Replace モードがスイッチ表示に
ならない」）。色はテーマトークン（theme_engine.switch_colors / qss_vars）。

API は QCheckBox に寄せる: isChecked() / setChecked() / toggled(bool)。
"""
from core.compat import Qt, QWidget, QPainter, QColor, Signal
from core.i18n import tr


class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self.setFixedSize(40, 20)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        try:
            from core.theme_engine import switch_colors, qss_vars
            c = switch_colors()
            v = qss_vars()
            self._colors = {"track_off": c["track"], "border": c["border"],
                            "knob_off": c["tick"], "track_on": v["primary"],
                            "knob_on": v["on_primary"]}
        except Exception:
            self._colors = None
        self.setToolTip(tr("クリックで ON/OFF", "Click to toggle"))

    # ── QCheckBox 互換 ──────────────────────────────────────────
    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool):
        on = bool(on)
        if on == self._checked:
            return
        self._checked = on
        self.update()
        self.toggled.emit(on)

    def toggle(self):
        self.setChecked(not self._checked)

    # ── 入力 ────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.toggle()
            e.accept()
        else:
            super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.toggle()
            e.accept()
        else:
            super().keyPressEvent(e)

    # ── 描画 ────────────────────────────────────────────────────
    def paintEvent(self, e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            r = self.rect().adjusted(1, 1, -1, -1)
            on = self._checked
            if self._colors is None:
                pal = self.palette()
                track = pal.highlight().color() if on else pal.mid().color()
                border = pal.dark().color()
                knob = pal.highlightedText().color() if on else pal.light().color()
            else:
                c = self._colors
                track = QColor(c["track_on"] if on else c["track_off"])
                border = QColor(c["track_on"] if on else c["border"])
                knob = QColor(c["knob_on"] if on else c["knob_off"])
            p.setPen(border)
            p.setBrush(track)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            d = r.height() - 4
            x = (r.right() - d - 1) if on else (r.left() + 2)
            p.setPen(Qt.NoPen)
            p.setBrush(knob)
            p.drawEllipse(x, r.top() + 2, d, d)
        finally:
            p.end()
