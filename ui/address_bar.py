# -*- coding: utf-8 -*-
"""アドレス欄（r146、ユーザー指示 2026-10-14）。

  「ファイルパスのテキストフィールドですが、ディレクトリとファイル名で
    フィールドを分けてください。その上で、一番右側にフルパスのコピー
    ボタンを付けてください。」

    [ ディレクトリ ..................|.......... ][ ファイル名 ][ コピー ]
                                     ↑ ここを掴んで幅を変えられる

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
    QSplitter,
)
from core.i18n import tr


class AddressBar(QWidget):
    """ディレクトリ欄＋ファイル名欄＋フルパスのコピーボタン。"""

    returnPressed = Signal()
    copied = Signal(str)          # コピーしたフルパス（ステータス表示用）
    splitChanged = Signal(list)   # [ディレクトリ幅, ファイル名幅]

    DIR_MIN_W = 120               # これ以下には縮めない（パスが読めなくなる）
    FILE_MIN_W = 80
    DEFAULT_SPLIT = (3, 1)        # 既定の比率（ディレクトリ : ファイル名）
    SETTINGS_KEY = "address_bar_split"

    def __init__(self, parent=None, settings_manager=None):
        super().__init__(parent)
        self.setObjectName("mfmAddressBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._raw = None              # 最後に setText された原文
        self._raw_parts = (None, None)
        self._sm = settings_manager

        # ── ディレクトリ欄 ／ ファイル名欄（間のバーで幅を変えられる）──
        #
        # r146（ユーザー指示 2026-10-14）:
        #   「ファイル名とディレクトリ名の間に幅を調整出来るバーをください。」
        #
        # 固定幅（110〜300px）では、長いファイル名の時に足りず、短い時には
        # 無駄に場所を取る。QSplitter にして掴んで動かせるようにし、位置は
        # 設定に覚えさせる（次回起動でも同じ幅）。
        # setChildrenCollapsible(False) が要る。潰せるようにすると、
        # 掴んで端まで動かした時に片方が幅 0 になり «欄が消えた» ように見える。
        self.split = QSplitter(Qt.Horizontal)
        self.split.setObjectName("mfmAddressSplit")
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(6)      # 1px の線では掴めない（当たり判定）

        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText(tr("パスを入力...", "Enter a path..."))
        self.dir_edit.setToolTip(tr("フォルダのパス（Enter で移動）",
                                    "Folder path (Enter to go)"))
        self.dir_edit.setMinimumWidth(self.DIR_MIN_W)
        self.dir_edit.returnPressed.connect(self.returnPressed.emit)
        self.split.addWidget(self.dir_edit)

        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText(tr("ファイル名", "File name"))
        self.file_edit.setToolTip(tr("選択中のファイル名（Enter でそのファイルへ）",
                                     "Selected file name (Enter to go there)"))
        self.file_edit.setMinimumWidth(self.FILE_MIN_W)
        self.file_edit.returnPressed.connect(self.returnPressed.emit)
        self.split.addWidget(self.file_edit)

        self.split.setStretchFactor(0, 1)     # 窓を広げた分はディレクトリ側へ
        self.split.setStretchFactor(1, 0)
        self.split.splitterMoved.connect(lambda *_a: self._on_split_moved())
        lay.addWidget(self.split, 1)
        self._restore_split()

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

    # ── 分割位置（覚える）───────────────────────────────────────
    def split_sizes(self):
        try:
            return [int(x) for x in self.split.sizes()]
        except Exception as _e:
            _swallow(_e, "ui/address_bar.py split_sizes")
            return []

    def set_split_sizes(self, sizes):
        """[ディレクトリ幅, ファイル名幅] を当てはめる。"""
        try:
            sizes = [int(x) for x in (sizes or [])]
        except (TypeError, ValueError):
            return False
        if len(sizes) != 2 or min(sizes) <= 0:
            return False
        try:
            self.split.setSizes(sizes)
            return True
        except Exception as _e:
            _swallow(_e, "ui/address_bar.py set_split_sizes")
            return False

    def _restore_split(self):
        saved = None
        if self._sm is not None:
            try:
                saved = self._sm.get(self.SETTINGS_KEY)
            except Exception as _e:
                _swallow(_e, "ui/address_bar.py _restore_split(get)")
        if not self.set_split_sizes(saved):
            # 既定は «3 : 1»。実寸はまだ決まっていないので比率で入れておく
            # （QSplitter は合計を今の幅へ割り当て直す）。
            d, f = self.DEFAULT_SPLIT
            self.split.setSizes([d * 100, f * 100])

    def _on_split_moved(self):
        sizes = self.split_sizes()
        if len(sizes) != 2:
            return
        self.splitChanged.emit(sizes)
        if self._sm is not None:
            try:
                self._sm.set(self.SETTINGS_KEY, sizes)
            except Exception as _e:
                _swallow(_e, "ui/address_bar.py _on_split_moved(set)")

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
        return self.for_display(d.rstrip("/\\") + os.sep + f)

    @staticmethod
    def for_display(path: str) -> str:
        """見た目を OS の区切り文字に揃える（r146、ユーザー報告 2026-10-14）。

          「起動時にパスが / になってます。一度でも選択すれば \\ になります」

        Qt（QFileSystemModel.filePath）は Windows でも «C:/Work» と / で返し、
        os.path.join は «C:\\Work» と \\ で返す。どちらが入るかは経路次第
        なので、そのまま出すと起動直後だけ / で、クリックすると \\ に化けた。
        Windows ユーザーが見慣れているのは \\ なので、**入口で揃える**。

        POSIX では \\ はファイル名に使える普通の文字なので触らない。
        UNC（\\\\server\\share）は / を含まないのでそのまま通る。
        """
        if os.sep == "\\" and path:
            return path.replace("/", "\\")
        return path

    def setText(self, path: str):
        """フルパスを受け取り、フォルダ／ファイル名へ振り分ける。"""
        path = self.for_display((path or "").strip())
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
