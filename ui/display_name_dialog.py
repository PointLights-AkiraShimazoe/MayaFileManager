# -*- coding: utf-8 -*-
"""表示名の編集ウィンドウ（r115）。

左に «実体のディレクトリ名»、右に «表示名» の入力欄を並べる。
右が空ならそのディレクトリは実体名のまま表示される。

保存先はそのディレクトリ直下の隠しファイル（core/display_names.py）。
[削除] は確認のうえ **ファイルごと** 消し、表示名機能をそのフォルダで止める。
"""

import os

from core.compat import (
    Qt, Signal, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QScrollArea, QMessageBox, QCheckBox,
)
from core.diag import swallow as _swallow
from core.i18n import tr
from core import display_names


def _tv():
    from core.theme_engine import qss_vars
    return qss_vars()


class DisplayNameDialog(QDialog):
    """表示名の一覧編集。directory 直下のディレクトリが対象。"""

    changed = Signal(str)          # 保存・削除後に対象ディレクトリを通知

    def __init__(self, directory: str, parent=None):
        super().__init__(parent)
        self._dir = directory
        self._rows = []            # [(実体名, QLineEdit)]

        self.setWindowTitle(tr("表示名の変更", "Change Display Names"))
        self.setMinimumSize(560, 420)
        self._apply_theme()
        self._build_ui()
        self._load()
        self._disable_auto_default()

    # ------------------------------------------------------------------
    def _apply_theme(self):
        self.setStyleSheet(
            "QDialog{background:%(surface)s;}"
            "QLabel{color:%(on_surface)s;}"
            "QLabel#hint{color:%(on_surface_dim)s;font-size:%(label_px)spx;}"
            "QLabel#real{color:%(on_surface_variant)s;}"
            "QLineEdit{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:%(r_s)spx;"
            "min-height:22px;padding:0 8px;}"
            "QPushButton{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:%(r_s)spx;"
            "min-height:26px;padding:0 14px;}"
            "QPushButton:hover{background:%(fill_subtle_hover)s;}"
            "QPushButton#primary{background:%(primary)s;color:%(on_primary)s;"
            "border:none;}"
            "QPushButton#danger{color:%(error)s;}"
            % _tv())

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        lay.addWidget(QLabel(self._dir))
        hint = QLabel(tr(
            "右を空にすると実際の名前で表示します。"
            "ファイルのコピーやパスの扱いは常に実際の名前のままです。",
            "Leave the right side empty to show the actual name. "
            "Paths and file operations always use the actual name."))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        head = QHBoxLayout()
        head.addWidget(QLabel(tr("実際の名前", "Actual name")), 1)
        head.addWidget(QLabel(tr("表示名", "Display name")), 1)
        lay.addLayout(head)

        self._body = QWidget()
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidget(self._body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        lay.addWidget(scroll, 1)

        self._enabled_cb = QCheckBox(tr("表示名を有効にする",
                                        "Enable display names"))
        self._enabled_cb.setChecked(True)
        lay.addWidget(self._enabled_cb)

        row = QHBoxLayout()
        self._del_btn = QPushButton(tr("削除", "Delete"))
        self._del_btn.setObjectName("danger")
        self._del_btn.setToolTip(tr("この設定ファイルごと削除します",
                                    "Delete the settings file itself"))
        self._del_btn.clicked.connect(lambda _c=False: self._delete())
        row.addWidget(self._del_btn)
        row.addStretch(1)
        cancel = QPushButton(tr("キャンセル", "Cancel"))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = QPushButton(tr("保存", "Save"))
        ok.setObjectName("primary")
        ok.clicked.connect(self._save)
        row.addWidget(ok)
        lay.addLayout(row)

    def _disable_auto_default(self):
        """Enter で «削除» 等が暴発しないようにする（r106 と同じ理由）。"""
        for b in self.findChildren(QPushButton):
            try:
                b.setAutoDefault(False)
                b.setDefault(False)
            except Exception as _e:
                _swallow(_e, "ui/display_name_dialog.py _disable_auto_default")

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    def _load(self):
        data = display_names.load(self._dir) or {}
        names = data.get("names", {}) if isinstance(data, dict) else {}
        self._enabled_cb.setChecked(bool(data.get("enabled", True))
                                    if data else True)
        self._del_btn.setEnabled(display_names.has_file(self._dir))
        for real in display_names.subdirectories(self._dir):
            row = QHBoxLayout()
            lb = QLabel(real)
            lb.setObjectName("real")
            lb.setToolTip(real)
            ed = QLineEdit(names.get(real, ""))
            ed.setPlaceholderText(tr("（空欄＝実際の名前）", "(empty = actual name)"))
            row.addWidget(lb, 1)
            row.addWidget(ed, 1)
            w = QWidget()
            w.setLayout(row)
            self._body_lay.addWidget(w)
            self._rows.append((real, ed))
        self._body_lay.addStretch(1)
        if not self._rows:
            self._body_lay.insertWidget(0, QLabel(
                tr("このフォルダにサブフォルダがありません。",
                   "This folder has no subfolders.")))

    def collect(self) -> dict:
        return {real: ed.text().strip() for real, ed in self._rows}

    def _save(self):
        display_names.save(self._dir, self.collect(),
                           enabled=self._enabled_cb.isChecked())
        self.changed.emit(self._dir)
        self.accept()

    def _delete(self):
        ret = QMessageBox.warning(
            self, tr("表示名の削除", "Delete display names"),
            tr("表示名の設定を削除します。**元に戻せません。**\n\n"
               "理解したうえで削除しますか？",
               "This will delete the display-name settings. "
               "**This cannot be undone.**\n\nDelete anyway?"),
            QMessageBox.Yes | QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return
        display_names.remove(self._dir)
        self.changed.emit(self._dir)
        self.accept()
