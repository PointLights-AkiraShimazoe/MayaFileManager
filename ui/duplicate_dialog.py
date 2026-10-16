# -*- coding: utf-8 -*-
"""
複製ダイアログ（r67、Ctrl+D）
=============================

- 名前モード: 複製物の名前を1つ入力（既定 «元名_Copy»）。複数選択時は各項目に
  同じ規則（_Copy）を適用するため入力欄は無効。
- Replace モード: Search / Replace で元名を置換した名前にする（複数選択向け）。
  「サブフォルダ以下にも適用」ON なら複製したフォルダの中身の名前にも同じ置換。
- 見た目は MayaFileManager のテーマ（theme_engine の QSS）に従う。
"""

import os
from typing import List, Tuple

from core.compat import (
    Qt, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QToolButton, QPushButton, QCheckBox, QStackedWidget, QWidget, QTextEdit,
)
from core.i18n import tr
from core.file_operations import default_copy_name, apply_replace_name


class DuplicateDialog(QDialog):
    def __init__(self, paths: List[str], parent=None, dest_dir: str = ""):
        """dest_dir を渡すと «別フォルダへの高度なコピー» になる（r146）。

        D&D の «高度なコピー» から使う。落とし先には同名が無いのが普通なので、
        その時は «_Copy» を足さず元の名前のまま置く（足すのは衝突した時だけ）。"""
        super().__init__(parent)
        self._paths = [p for p in paths if p]
        self._dest_dir = os.path.normpath(dest_dir) if dest_dir else ""
        self.setWindowTitle(tr("高度なコピー", "Advanced Copy") if self._dest_dir
                            else tr("複製", "Duplicate"))
        self.setMinimumWidth(520)
        from ui.dialog_util import apply_window_buttons as _awb
        _awb(self)   # r146: ✕ を必ず押せる状態にする
        self._build()
        self._update_preview()

    # ── UI ─────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # モード切替（r146: 本物のスイッチ。文字だけの ON/OFF ボタンだった）
        # r146（ユーザー指示 2026-10-16）: 呼び名は «一括リネーム»（複製しながら
        # Search→Replace で名前を付け替える）。ON/OFF の文字と説明文は出さない。
        from ui.toggle_switch import ToggleSwitch
        mode_row = QHBoxLayout()
        self._mode_label = QLabel(tr("一括リネーム", "Batch rename"))
        self._mode_label.setToolTip(tr(
            "ON にすると、Search→Replace で名前を付け替えながら複製する\n"
            "（サブフォルダ以下の名前にも適用できる）",
            "When on, duplicate while renaming by Search→Replace\n"
            "(can also apply inside subfolders)"))
        mode_row.addWidget(self._mode_label)
        self._mode_btn = ToggleSwitch(False)
        self._mode_btn.setToolTip(self._mode_label.toolTip())
        self._mode_btn.toggled.connect(self._on_mode)
        mode_row.addWidget(self._mode_btn)
        mode_row.addStretch(1)
        n = len(self._paths)
        mode_row.addWidget(QLabel(tr("対象: %d 件", "Items: %d") % n))
        root.addLayout(mode_row)

        # r146（ユーザー報告 2026-10-16「Replace モードのスイッチを押すと、
        # スイッチの位置がズレる」）: 行を show/hide で出し入れしていたので
        # ダイアログの高さが伸び縮みし、縮んだ分の余白が上の行にも配られて
        # スイッチが下へずれていた。2 つのモードを QStackedWidget の
        # ページにして高さを «大きい方» で固定し、余白は下の stretch に集める。
        self._pages = QStackedWidget()
        page_name = QWidget()
        grid = QGridLayout(page_name)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        page_rep = QWidget()
        grid_r = QGridLayout(page_rep)
        grid_r.setContentsMargins(0, 0, 0, 0)
        grid_r.setHorizontalSpacing(8)
        grid_r.setVerticalSpacing(6)
        # 名前モード
        self._name_label = QLabel(tr("複製物の名前:", "Name:"))
        self._name_edit = QLineEdit()
        if n == 1:
            # r146: 別フォルダへの «高度なコピー» は、落とし先に同名が無ければ
            # 元の名前のまま置く（_Copy を足す理由が無い）。ここを既定の
            # default_copy_name のままにしていたので、名前欄の値が優先されて
            # «_Copy» が付いたままだった（回帰スイート 2026-10-13）。
            self._name_edit.setText(self._plain_name(self._paths[0], set()))
        else:
            self._name_edit.setText(tr("（複数選択: 各項目に _Copy を付けます）",
                                       "(multiple: _Copy is appended to each)"))
            self._name_edit.setEnabled(False)
        self._name_edit.textChanged.connect(self._update_preview)
        grid.addWidget(self._name_label, 0, 0)
        grid.addWidget(self._name_edit, 0, 1)
        grid.setRowStretch(1, 1)             # 余った高さは下に逃がす
        # Replace モード
        self._search_label = QLabel("Search:")
        self._search_edit = QLineEdit()
        self._replace_label = QLabel("Replace:")
        self._replace_edit = QLineEdit()
        for e in (self._search_edit, self._replace_edit):
            e.textChanged.connect(self._update_preview)
        grid_r.addWidget(self._search_label, 0, 0)
        grid_r.addWidget(self._search_edit, 0, 1)
        grid_r.addWidget(self._replace_label, 1, 0)
        grid_r.addWidget(self._replace_edit, 1, 1)
        self._recursive_cb = QCheckBox(tr("サブフォルダ以下の名前にも適用",
                                          "Also apply inside subfolders"))
        self._recursive_cb.setToolTip(tr(
            "複製したフォルダの中のファイル/フォルダ名にも同じ Search→Replace を適用",
            "Apply the same Search→Replace to names inside duplicated folders"))
        grid_r.addWidget(self._recursive_cb, 2, 1)
        # ラベル列の幅を両ページで揃える（Search: と 複製物の名前: の長い方）
        lw = max(self._name_label.sizeHint().width(),
                 self._search_label.sizeHint().width(),
                 self._replace_label.sizeHint().width())
        grid.setColumnMinimumWidth(0, lw)
        grid_r.setColumnMinimumWidth(0, lw)
        self._pages.addWidget(page_name)
        self._pages.addWidget(page_rep)
        root.addWidget(self._pages)

        self._recursive_cb.toggled.connect(self._update_preview)
        # r146（ユーザー指示 2026-10-16）: 名前が変わるものは **全部** 出す。
        # 行数が増えても困らないよう、スクロールできる読み取り専用の欄にする。
        # リネーム後の名前は緑（テーマの success）。
        self._preview = QTextEdit()
        self._preview.setObjectName("mfmDupPreview")
        self._preview.setReadOnly(True)
        self._preview.setFrameShape(QTextEdit.NoFrame)
        self._preview.setLineWrapMode(QTextEdit.NoWrap)
        self._preview.setMinimumHeight(96)
        self._preview.setMaximumHeight(320)
        self._preview.setStyleSheet("QTextEdit#mfmDupPreview{background:transparent;}")
        root.addWidget(self._preview, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(tr("キャンセル", "Cancel"))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        self._ok = QPushButton(tr("複製", "Duplicate"))
        self._ok.setDefault(True)
        self._ok.clicked.connect(self.accept)
        btns.addWidget(self._ok)
        root.addLayout(btns)

        self._on_mode(False)
        # 名前欄は拡張子を除いて選択（Explorer 準拠）
        if n == 1 and self._name_edit.isEnabled():
            name = self._name_edit.text()
            stem = name if os.path.isdir(self._paths[0]) else os.path.splitext(name)[0]
            self._name_edit.setSelection(0, len(stem))
            self._name_edit.setFocus()

    def _on_mode(self, replace_mode: bool):
        self._pages.setCurrentIndex(1 if replace_mode else 0)
        if replace_mode:
            self._search_edit.setFocus()
        self._update_preview()

    # ── 結果 ────────────────────────────────────────────────────────────
    def specs(self) -> List[Tuple[str, str]]:
        """[(src, dst), ...]（同じ親フォルダ内）。"""
        out = []
        taken = set()
        replace_mode = self._mode_btn.isChecked()
        search, repl = self._search_edit.text(), self._replace_edit.text()
        for p in self._paths:
            parent = self._dest_dir or os.path.dirname(p)
            name = os.path.basename(p.rstrip("/\\"))
            if replace_mode:
                new = apply_replace_name(name, search, repl) if search else \
                    self._plain_name(p, taken)
                if new == name:               # 置換対象なし → 既定の名前へ
                    new = self._plain_name(p, taken)
            elif len(self._paths) == 1:
                new = self._name_edit.text().strip() or self._plain_name(p, taken)
            else:
                new = self._plain_name(p, taken)
            new = self._free_name(parent, new, taken)
            taken.add(new)
            out.append((p, os.path.join(parent, new)))
        return out

    def _plain_name(self, p, taken):
        """既定の名前。別フォルダ宛なら «元の名前» のまま（r146）。"""
        name = os.path.basename(p.rstrip("/\\"))
        if self._dest_dir and name not in taken \
                and not os.path.lexists(os.path.join(self._dest_dir, name)):
            return name
        return default_copy_name(p, taken)

    @staticmethod
    def _free_name(parent, name, taken):
        """その場所で空いている名前にする（_Copy, _Copy2, … と足す）。"""
        stem, ext = os.path.splitext(name)
        i, out = 1, name
        while out in taken or os.path.lexists(os.path.join(parent, out)):
            i += 1
            out = "%s_Copy%s%s" % (stem, "" if i == 2 else i, ext)
            if i > 999:
                break
        return out

    def rename_inside(self):
        if self._mode_btn.isChecked() and self._recursive_cb.isChecked() \
                and self._search_edit.text():
            return (self._search_edit.text(), self._replace_edit.text())
        return None

    @staticmethod
    def _esc(text: str) -> str:
        """HTML の最小エスケープ（標準の html モジュールは EXE から外してある）。"""
        return (str(text).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def preview_text(self) -> str:
        """プレビューの素のテキスト（検査用）。"""
        return self._preview.toPlainText()

    @staticmethod
    def _ok_color() -> str:
        try:
            from core.theme_engine import qss_vars
            return qss_vars()["success"]
        except Exception:
            return "palette(highlight)"

    def _update_preview(self):
        try:
            specs = self.specs()
        except Exception:
            specs = []
        esc = self._esc
        green = self._ok_color()
        rows = []
        bad = False
        for src, dst in specs:
            n = os.path.basename(dst)
            mark = ""
            if os.path.exists(dst):
                mark = tr("  ← 既に存在", "  ← already exists")
                bad = True
            if any(c in n for c in '\\/:*?"<>|'):
                mark = tr("  ← 使えない文字", "  ← invalid characters")
                bad = True
            rows.append("%s → <span style=\"color:%s\">%s</span>%s"
                        % (esc(os.path.basename(src)), green, esc(n), esc(mark)))
        # r146: 「サブフォルダ以下の名前にも適用」が ON なら、中身がどう
        # 変わるかも見せる（ユーザー報告 2026-10-15「サブフォルダ含めた時に
        # 名前変更プレビューがでない」）。全部出す（2026-10-16）。
        for kind, a, b in self._inside_preview_rows(specs):
            if kind == "head":
                rows.append(esc(a))
            elif kind == "note":
                rows.append(esc(a))
            else:
                rows.append("&nbsp;&nbsp;&nbsp;&nbsp;%s → <span style=\"color:%s\">%s</span>"
                            % (esc(a), green, esc(b)))
        self._preview.setHtml("<div style=\"white-space:pre\">" + "<br>".join(rows) + "</div>")
        self._ok.setEnabled(bool(specs) and not bad)

    INSIDE_SCAN_MAX = 20000       # 歩く件数の安全弁（それ以上は走査を打ち切る）

    def _inside_preview_rows(self, specs):
        """フォルダの中身に置換が掛かる分（r146）。(kind, a, b) の列。
        kind: "head"（見出し）/ "row"（旧 → 新）/ "note"（注記）。
        «名前が変わるもの» は **全部** 出す（ユーザー指示 2026-10-16）。"""
        inside = self.rename_inside()
        if not inside:
            return []
        search, repl = inside
        out, scanned, capped = [], 0, False
        for src, _dst in specs:
            if not os.path.isdir(src) or os.path.islink(src):
                continue
            top = os.path.basename(src.rstrip("/\\"))
            for dirpath, dirnames, filenames in os.walk(src):
                for n in dirnames + filenames:
                    scanned += 1
                    new = apply_replace_name(n, search, repl)
                    if new != n:
                        rel = os.path.relpath(os.path.join(dirpath, n), src)
                        out.append(("row", "%s/%s" % (top, rel.replace("\\", "/")), new))
                    if scanned >= self.INSIDE_SCAN_MAX:
                        capped = True
                        break
                if capped:
                    break
            if capped:
                break
        if not out:
            return [("note", tr("  （サブフォルダ以下に置換対象の名前はありません）",
                                "  (nothing inside matches the search)"), "")]
        head = tr("  サブフォルダ以下（%d 件が変わる%s）:",
                  "  Inside folders (%d change%s):") % (
            len(out), tr("、走査上限に達した", ", scan capped") if capped else "")
        return [("head", head, "")] + out
