# -*- coding: utf-8 -*-
"""アドレス欄（r146、ユーザー指示 2026-10-14）。

  「ファイルパスのテキストフィールドですが、ディレクトリとファイル名で
    フィールドを分けてください。その上で、一番右側にフルパスのコピー
    ボタンを付けてください。」

    [ ディレクトリ ............................ ][ ファイル名 ][ コピー ]

設計の芯
--------
**呼び出し側から見た顔は今までと同じにする。** 既存のパス欄は
``setText(path)`` / ``text()`` / ``returnPressed`` の 3 つだけで
10 箇所以上から使われている。ここを作り替えるのに全部の呼び出しを
書き換えると、1 つ取りこぼすだけで «パス欄が更新されない» という
静かな不具合になる。同じ 3 つを備えた入れ物にして、中で 2 つの欄へ
振り分ける。

振り分けの規則
--------------
* フォルダ → 左だけに入れ、ファイル名欄は空
* ファイル → 左に親フォルダ、右にファイル名
判定は ``os.path.isdir``。直前に同じパスで移動処理が走っているので、
OS のキャッシュに載っていて待たされない。
"""
from core.diag import swallow as _swallow

import os

from core.compat import (
    Qt, Signal, QWidget, QHBoxLayout, QLineEdit, QToolButton, QApplication,
)
from core.i18n import tr


class AddressBar(QWidget):
    """ディレクトリ欄＋ファイル名欄＋フルパスのコピーボタン。"""

    returnPressed = Signal()
    copied = Signal(str)          # コピーしたフルパス（ステータス表示用）

    FILE_MIN_W = 110
    FILE_MAX_W = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mfmAddressBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._raw = None              # 最後に setText された原文
        self._raw_parts = (None, None)

        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText(tr("パスを入力...", "Enter a path..."))
        self.dir_edit.setToolTip(tr("フォルダのパス（Enter で移動）",
                                    "Folder path (Enter to go)"))
        self.dir_edit.returnPressed.connect(self.returnPressed.emit)
        lay.addWidget(self.dir_edit, 1)

        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText(tr("ファイル名", "File name"))
        self.file_edit.setToolTip(tr("選択中のファイル名（Enter でそのファイルへ）",
                                     "Selected file name (Enter to go there)"))
        self.file_edit.setMinimumWidth(self.FILE_MIN_W)
        self.file_edit.setMaximumWidth(self.FILE_MAX_W)
        self.file_edit.returnPressed.connect(self.returnPressed.emit)
        lay.addWidget(self.file_edit, 0)

        self.copy_btn = QToolButton()
        self.copy_btn.setObjectName("mfmAddrCopy")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setToolTip(tr("フルパスをコピー", "Copy the full path"))
        try:
            from ui.dcc_header import glyph_icon
            self.copy_btn.setIcon(glyph_icon("📋"))
        except Exception as _e:
            _swallow(_e, "ui/address_bar.py AddressBar(icon)")
            self.copy_btn.setText("📋")
        self.copy_btn.clicked.connect(lambda _c=False: self.copy_full_path())
        lay.addWidget(self.copy_btn, 0)

    # ── 既存の «パス欄» と同じ顔 ─────────────────────────────────
    def text(self) -> str:
        """フルパス（2 つの欄をつないだもの）。

        **誰も触っていなければ、入れられた文字列をそのまま返す。**
        区切り文字を組み直すと «C:/a/b.ma» が «C:/a\\b.ma» に化けて、
        入れた時と返る時で文字列が変わる。パス欄の値を突き合わせている
        箇所（リネーム後の追従など）がそれで狂うので、原文を優先する。"""
        d = self.dir_edit.text().strip()
        f = self.file_edit.text().strip()
        raw = getattr(self, "_raw", None)
        if raw is not None and (d, f) == getattr(self, "_raw_parts", (None, None)):
            return raw
        if not f:
            return d
        if not d:
            return f
        sep = "/" if ("/" in d and "\\" not in d) else os.sep
        return d.rstrip("/\\") + sep + f

    def setText(self, path: str):
        """フルパスを受け取り、フォルダ／ファイル名へ振り分ける。"""
        path = (path or "").strip()
        self._raw = path
        if not path:
            self.dir_edit.setText("")
            self.file_edit.setText("")
            self._raw_parts = ("", "")
            return
        if self._looks_like_dir(path):
            self.dir_edit.setText(path)
            self.file_edit.setText("")
            self._raw_parts = (path.strip(), "")
            return
        head, tail = os.path.split(path)
        if not tail:                      # 末尾が区切り文字だけ
            self.dir_edit.setText(path)
            self.file_edit.setText("")
            self._raw_parts = (path.strip(), "")
            return
        self.dir_edit.setText(head or path)
        self.file_edit.setText(tail)
        self._raw_parts = ((head or path).strip(), tail.strip())

    @staticmethod
    def _looks_like_dir(path: str) -> bool:
        if path.endswith(("/", "\\")):
            return True
        try:
            return os.path.isdir(path)
        except OSError:
            return False

    # ── コピー ───────────────────────────────────────────────────
    def copy_full_path(self) -> str:
        full = self.text()
        if not full:
            return ""
        try:
            cb = QApplication.clipboard()
            if cb is not None:
                cb.setText(full)
        except Exception as _e:
            _swallow(_e, "ui/address_bar.py copy_full_path")
            return ""
        self.copied.emit(full)
        return full

    # 旧コードが素の QLineEdit として触っても落ちないための受け皿
    def setPlaceholderText(self, s: str):
        self.dir_edit.setPlaceholderText(s)

    def setFocus(self, *a):
        self.dir_edit.setFocus(*a)

    def selectAll(self):
        self.dir_edit.selectAll()
