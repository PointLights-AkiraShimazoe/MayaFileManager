# -*- coding: utf-8 -*-
"""
DCC からの保存／書き出しダイアログ（r70）
==========================================

- 右クリック「シーンを保存」「選択を書き出し」から開く。
- ファイル名欄（選択中ファイルがあればその名前を初期値、拡張子を除いて選択）。
- 拡張子プルダウン: 「Optional（任意）」＋ DCC が扱える形式（core/dcc_save）。
  形式を選ぶとファイル名の拡張子を自動で差し替える。「Optional」の時は
  ファイル名に打った拡張子で形式を判定する。
- 拡張子ごとのオプションを同じダイアログに表示し、値は設定 `save_options`
  （{dcc: {ext: {key: value}}}）に記憶。直近の形式は `save_last_ext`。
- 見た目は theme_engine の QSS に従う（色の直書きはしない）。
"""

import os

from core.compat import (
    Qt, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QComboBox, QCheckBox, QPushButton, QGroupBox, QMessageBox,
)
from core.diag import swallow as _swallow
from core.i18n import tr, current_lang
from core import dcc_save

_DEFAULT_EXT = {"maya": ".ma", "blender": ".blend"}


class SaveDialog(QDialog):
    """mode: "save"（シーンを保存）/ "export"（選択を書き出し）。"""

    def __init__(self, dcc: str, mode: str, folder: str, initial_name: str = "",
                 sm=None, parent=None):
        super().__init__(parent)
        self._dcc = "blender" if dcc == "blender" else "maya"
        self._mode = "export" if mode == "export" else "save"
        self._folder = folder or ""
        self._sm = sm
        self._opt_widgets = {}          # key -> widget
        self._opt_ext = None            # 現在オプションを表示している拡張子
        self._result_path = ""
        self._result_opts = {}
        app = "Blender" if self._dcc == "blender" else "Maya"
        self.setWindowTitle("%s: %s" % (
            app, tr("選択を書き出し", "Export Selection") if self._mode == "export"
            else tr("シーンを保存", "Save Scene")))
        self.setMinimumWidth(560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._build(initial_name)

    # ── UI ─────────────────────────────────────────────────────────────
    def _build(self, initial_name: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.addWidget(QLabel(tr("保存先:", "Folder:")), 0, 0)
        folder_lbl = QLabel(self._folder)
        folder_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        folder_lbl.setWordWrap(True)
        grid.addWidget(folder_lbl, 0, 1, 1, 2)

        grid.addWidget(QLabel(tr("ファイル名:", "File name:")), 1, 0)
        self._name_edit = QLineEdit()
        self._name_edit.textChanged.connect(self._on_name_changed)
        grid.addWidget(self._name_edit, 1, 1)

        self._ext_combo = QComboBox()
        self._ext_combo.addItem(tr("Optional（任意）", "Optional (auto)"), dcc_save.AUTO_EXT)
        for e in dcc_save.exts_for(self._dcc):
            self._ext_combo.addItem(e, e)
        self._ext_combo.setMinimumWidth(150)
        grid.addWidget(self._ext_combo, 1, 2)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        self._opt_box = QGroupBox(tr("オプション", "Options"))
        self._opt_layout = QGridLayout(self._opt_box)
        self._opt_layout.setHorizontalSpacing(8)
        self._opt_layout.setVerticalSpacing(4)
        root.addWidget(self._opt_box)

        self._preview = QLabel()
        self._preview.setObjectName("mfmDupPreview")
        self._preview.setWordWrap(True)
        root.addWidget(self._preview)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(tr("キャンセル", "Cancel"))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        self._ok = QPushButton(tr("書き出し", "Export") if self._mode == "export"
                               else tr("保存", "Save"))
        self._ok.setDefault(True)
        self._ok.clicked.connect(lambda _c=False: self._on_accept())
        btns.addWidget(self._ok)
        root.addLayout(btns)

        # 初期値: 自動命名 > 選択ファイル名 > 直近の形式 > DCC 既定
        # r119d: 自動命名は «設定するだけで何も起きない» 機能だった
        # （apply_auto_name に呼び出し元が無かった）。ここで繋ぐ。
        name = os.path.basename(initial_name or "")
        auto = self._auto_name_suggestion(os.path.splitext(name)[0])
        if auto:
            name = auto + os.path.splitext(name)[1]
        ext = os.path.splitext(name)[1].lower()
        if ext not in dcc_save.exts_for(self._dcc):
            last = self._remembered_ext()
            ext = last if last in dcc_save.exts_for(self._dcc) else _DEFAULT_EXT[self._dcc]
            name = (os.path.splitext(name)[0] if name else "") + ext
        # r86: コンボの既定は «Optional（任意）»（＝index 0、ユーザー指示）。
        # 拡張子はファイル名側（name）に入っているので、この既定のままでも
        # 保存される形式は変わらない（effective_ext がファイル名から判定する）。
        self._ext_combo.setCurrentIndex(0)
        self._ext_combo.currentIndexChanged.connect(self._on_ext_changed)
        self._name_edit.setText(name)
        self._rebuild_options(self.effective_ext())
        stem = os.path.splitext(name)[0]
        self._name_edit.setSelection(0, len(stem))
        self._name_edit.setFocus()

    # ── 拡張子 ───────────────────────────────────────────────────────────
    def selected_ext(self) -> str:
        """プルダウンの選択（AUTO_EXT または拡張子）。"""
        return self._ext_combo.currentData() or dcc_save.AUTO_EXT

    def effective_ext(self) -> str:
        """実際に使う拡張子。Optional の時はファイル名の拡張子（無ければ DCC 既定）。"""
        sel = self.selected_ext()
        if sel != dcc_save.AUTO_EXT:
            return sel
        name = self._name_edit.text().strip()
        typed = os.path.splitext(name)[1].lower()
        if not typed and name.startswith("."):
            # 名前が未入力で «.fbx» だけの状態（＝直近の形式が入っている）。
            # splitext は先頭ドットを «隠しファイル名» と見なし拡張子を返さない
            # ため、ここで拾う（r86: 既定を Optional にしたので必須）。
            if name.lower() in dcc_save.exts_for(self._dcc):
                typed = name.lower()
        if typed in dcc_save.exts_for(self._dcc):
            return typed
        if typed:
            return typed        # 未知の拡張子はそのまま尊重する
        # 拡張子が無い名前 → 直近に使った形式 → DCC 既定（r86）。
        # コンボの既定を Optional にしたので、ここで «記憶» を効かせないと
        # 「前回 FBX で書き出したのに .ma になる」事故になる。
        last = self._remembered_ext()
        if last in dcc_save.exts_for(self._dcc):
            return last
        return _DEFAULT_EXT[self._dcc]

    def _on_ext_changed(self, _idx: int):
        sel = self.selected_ext()
        if sel != dcc_save.AUTO_EXT:
            # ファイル名の拡張子を選択した形式へ自動で差し替える。
            # 既知の拡張子だけを外す（chr_A.v012 のような «.» 付きの名前は保持）。
            name = self._name_edit.text().strip()
            stem, cur = os.path.splitext(name)
            base = stem if cur.lower() in dcc_save.exts_for(self._dcc) else name
            new = base + sel
            if new != name:
                self._name_edit.blockSignals(True)
                self._name_edit.setText(new)
                self._name_edit.blockSignals(False)
        self._rebuild_options(self.effective_ext())

    def _on_name_changed(self, _text: str):
        if self.selected_ext() == dcc_save.AUTO_EXT:
            self._rebuild_options(self.effective_ext())
        else:
            self._update_preview()

    # ── オプション ─────────────────────────────────────────────────────
    def _rebuild_options(self, ext: str):
        if ext == self._opt_ext:
            self._update_preview()
            return
        self._store_current_options()
        self._opt_ext = ext
        while self._opt_layout.count():
            item = self._opt_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._opt_widgets = {}
        defs = dcc_save.options_for(self._dcc, ext)
        saved = self._saved_options(ext)
        if not defs:
            known = ext in dcc_save.exts_for(self._dcc)
            lbl = QLabel(tr("この形式にオプションはありません", "No options for this format")
                         if known else
                         tr("未対応の拡張子です: %s", "Unsupported extension: %s") % (ext or "-"))
            self._opt_layout.addWidget(lbl, 0, 0, 1, 2)
        for row, d in enumerate(defs):
            key, ja, en, typ, default = d[0], d[1], d[2], d[3], d[4]
            label = ja if current_lang() == "ja" else en
            val = saved.get(key, default)
            if typ == "bool":
                w = QCheckBox(label)
                w.setChecked(bool(val))
                self._opt_layout.addWidget(w, row, 0, 1, 2)
            elif typ == "choice":
                w = QComboBox()
                for c in d[5]:
                    w.addItem(str(c), c)
                i = w.findData(val)
                w.setCurrentIndex(i if i >= 0 else 0)
                self._opt_layout.addWidget(QLabel(label + ":"), row, 0)
                self._opt_layout.addWidget(w, row, 1)
            else:
                w = QLineEdit(str(val))
                self._opt_layout.addWidget(QLabel(label + ":"), row, 0)
                self._opt_layout.addWidget(w, row, 1)
            self._opt_widgets[key] = w
        self._opt_box.setTitle(tr("オプション（%s）", "Options (%s)") % (ext or "-"))
        self._update_preview()

    def current_options(self) -> dict:
        out = {}
        defs = {d[0]: d for d in dcc_save.options_for(self._dcc, self._opt_ext or "")}
        for key, w in self._opt_widgets.items():
            typ = defs.get(key, (None, None, None, "bool"))[3]
            if isinstance(w, QCheckBox):
                out[key] = bool(w.isChecked())
            elif isinstance(w, QComboBox):
                out[key] = w.currentData()
            elif typ == "int":
                try:
                    out[key] = int(w.text())
                except ValueError:
                    out[key] = defs[key][4]
            else:
                out[key] = w.text()
        return out

    def _saved_options(self, ext: str) -> dict:
        if self._sm is None:
            return {}
        allopts = self._sm.get("save_options", {}) or {}
        return dict((allopts.get(self._dcc) or {}).get(ext) or {})

    def _store_current_options(self):
        """表示中の形式のオプションを設定へ（保存はダイアログ確定時）。"""
        if self._sm is None or not self._opt_ext or not self._opt_widgets:
            return
        allopts = dict(self._sm.get("save_options", {}) or {})
        per = dict(allopts.get(self._dcc) or {})
        per[self._opt_ext] = self.current_options()
        allopts[self._dcc] = per
        self._sm.set("save_options", allopts, save=False)

    def _remembered_ext(self) -> str:
        if self._sm is None:
            return ""
        d = self._sm.get("save_last_ext", {}) or {}
        return (d.get(self._dcc) or {}).get(self._mode, "") or ""

    # ── 結果 ────────────────────────────────────────────────────────────
    def _update_preview(self):
        p = self.path()
        if not os.path.basename(p) or os.path.basename(p).startswith("."):
            self._preview.setText(tr("ファイル名を入力してください", "Enter a file name"))
            self._ok.setEnabled(False)
            return
        self._ok.setEnabled(True)
        exists = os.path.isfile(p)
        self._preview.setText(
            "→ %s%s" % (p, tr("  （既存: 上書きします）", "  (exists: will overwrite)") if exists else ""))

    def path(self) -> str:
        name = self._name_edit.text().strip()
        if not name:
            return ""
        ext = self.effective_ext()
        if self.selected_ext() != dcc_save.AUTO_EXT and not name.lower().endswith(ext):
            name = os.path.splitext(name)[0] + ext
        elif not os.path.splitext(name)[1]:
            name = name + ext
        return os.path.join(self._folder, name)

    def _on_accept(self):
        p = self.path()
        if not p:
            return
        ext = os.path.splitext(p)[1].lower()
        if ext not in dcc_save.exts_for(self._dcc):
            QMessageBox.warning(self, self.windowTitle(),
                                tr("未対応の拡張子です: %s", "Unsupported extension: %s") % ext)
            return
        if os.path.isfile(p):
            ret = QMessageBox.question(
                self, self.windowTitle(),
                tr("%s は既に存在します。上書きしますか？", "%s already exists. Overwrite?")
                % os.path.basename(p), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        self._result_path = p
        # r119d: 自動命名の連番は «保存が確定した時だけ» 進める
        # （提案しただけで進めると、取り消した分だけ番号が飛ぶ）。
        self._commit_auto_seq()
        self._result_opts = self.current_options()
        self._store_current_options()
        if self._sm is not None:
            d = dict(self._sm.get("save_last_ext", {}) or {})
            per = dict(d.get(self._dcc) or {})
            per[self._mode] = ext
            d[self._dcc] = per
            self._sm.set("save_last_ext", d, save=True)   # save_options も同時に永続化
        self.accept()

    # ── 自動命名（r119d）────────────────────────────────────────────
    def _auto_naming(self):
        """この保存先に効くルール (ルール, 連番)。無ければ (None, 0)。"""
        if self._sm is None or not self._folder:
            return None, 0
        try:
            from core.file_operations import find_auto_name_rule, peek_auto_seq
            enabled, rules = self._sm.get_active_auto_naming()
            if not enabled or not rules:
                return None, 0
            _d, rule = find_auto_name_rule(self._folder, rules)
            if not rule:
                return None, 0
            return rule, peek_auto_seq(self._folder, rule)
        except Exception as _e:
            _swallow(_e, "ui/save_dialog.py _auto_naming")
            return None, 0

    def _auto_name_suggestion(self, base_name: str) -> str:
        rule, seq = self._auto_naming()
        if not rule:
            return ""
        try:
            from core.file_operations import expand_auto_name
            return expand_auto_name(rule.get("template", "{seq:04d}"), seq,
                                    directory=self._folder, name=base_name)
        except Exception as _e:
            _swallow(_e, "ui/save_dialog.py _auto_name_suggestion")
            return ""

    def _commit_auto_seq(self):
        rule, seq = self._auto_naming()
        if not rule:
            return
        try:
            from core.file_operations import commit_auto_seq
            commit_auto_seq(self._folder, rule, seq)
        except Exception as _e:
            _swallow(_e, "ui/save_dialog.py _commit_auto_seq")

    def result_path(self) -> str:
        return self._result_path

    def result_options(self) -> dict:
        return dict(self._result_opts)

    def dcc_code(self) -> str:
        """DCC へ送る Python コード（確定後に呼ぶ）。"""
        fn = dcc_save.blender_code if self._dcc == "blender" else dcc_save.maya_code
        return fn(self._result_path, self._mode, self._result_opts)
