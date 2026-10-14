# -*- coding: utf-8 -*-
"""Maya の起動プロファイル（r119）。

ユーザー指示:
  «同じ Maya バージョンでも別の引数を持たせたいことがある»。
  検出されたバージョンを並べ、行を追加でき、バージョンはプルダウンで選ぶ。
  マネージャーのプルダウンに出す «表示名» も決められるようにする。

1 行 = 1 プロファイル = （バージョン, 表示名, 追加引数, 環境変数, ラッパー）。
r146（ユーザー指示 2026-10-13）: bat で組んでいた起動手順を取り込めるように
«環境変数» と «ラッパー» を追加した。UI 言語のように Maya が起動時に読む
設定は引数では渡せず、それが理由で bat が必要になっていたため。
プロファイルが 1 つも無ければ、従来どおり «検出されたバージョンそのまま»
＋ 共通の追加引数（設定の maya_extra_args）で動く。
"""

import os
import uuid

from core.compat import (
    Qt, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QToolButton, QLineEdit, QComboBox, QScrollArea, QFrame,
)
from core.diag import swallow as _swallow
from core.i18n import tr


def _tv():
    from core.theme_engine import qss_vars
    return qss_vars()


class _ProfileRow(QFrame):
    """1 行: バージョン（プルダウン）／表示名／追加引数。"""

    def __init__(self, versions, data=None, parent=None, on_remove=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self._on_remove = on_remove
        data = data or {}
        self._id = data.get("id") or uuid.uuid4().hex[:8]

        g = QGridLayout(self)
        g.setContentsMargins(6, 6, 6, 6)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(4)

        g.addWidget(QLabel(tr("バージョン:", "Version:")), 0, 0)
        self.ver = QComboBox()
        for v in versions:
            self.ver.addItem("Maya %s" % v, v)
        want = str(data.get("version", "") or "")
        if want:
            i = self.ver.findData(want)
            if i < 0:                      # 未検出のバージョンでも消さない
                self.ver.addItem(tr("Maya %s（未検出）", "Maya %s (not found)")
                                 % want, want)
                i = self.ver.findData(want)
            self.ver.setCurrentIndex(i)
        g.addWidget(self.ver, 0, 1)

        g.addWidget(QLabel(tr("表示名:", "Display name:")), 0, 2)
        self.label = QLineEdit(data.get("label", ""))
        self.label.setPlaceholderText(
            tr("（空欄＝「Maya 2026」）", "(empty = “Maya 2026”)"))
        self.label.setToolTip(tr("マネージャーのプルダウンに出る名前",
                                 "The name shown in the manager's dropdown"))
        g.addWidget(self.label, 0, 3)

        rm = QToolButton()
        rm.setText("✕")
        rm.setToolTip(tr("この行を削除", "Remove this row"))
        rm.clicked.connect(lambda _c=False: self._remove())
        g.addWidget(rm, 0, 4)

        g.addWidget(QLabel(tr("追加引数:", "Extra arguments:")), 1, 0)
        self.args = QLineEdit(data.get("args", ""))
        self.args.setPlaceholderText(
            tr("-batch など（スペース区切り）", "e.g. -batch (space separated)"))
        self.args.setToolTip(tr(
            "maya.exe にそのまま渡す引数。\n"
            "開くファイルは選択中のものが自動で渡るので書きません。",
            "Passed straight to maya.exe.\n"
            "The file to open is supplied automatically; do not add it here."))
        g.addWidget(self.args, 1, 1, 1, 4)

        # r146（ユーザー指示 2026-10-13）: bat で組んでいた起動を取り込めるように。
        g.addWidget(QLabel(tr("環境変数:", "Environment:")), 2, 0)
        self.env = QLineEdit(data.get("env", ""))
        self.env.setPlaceholderText(
            "MAYA_UI_LANGUAGE=en_US; MAYA_MODULE_PATH=D:/path/to/modules")
        self.env.setToolTip(tr(
            "起動時に足す環境変数。KEY=VALUE を ; か改行で区切ります。\n"
            "bat の «SET KEY=VALUE» をそのまま貼っても通ります。\n"
            "UI 言語のように «Maya が起動時に読む» ものは引数では渡せないので、\n"
            "ここに書きます（例: MAYA_UI_LANGUAGE=en_US）。",
            "Environment variables added at launch. KEY=VALUE separated by ; "
            "or newlines.\n"
            "A bat-style “SET KEY=VALUE” line is accepted as well.\n"
            "Things Maya reads at startup (such as the UI language) cannot be "
            "passed as arguments; put them here."))
        g.addWidget(self.env, 2, 1, 1, 4)

        g.addWidget(QLabel(tr("ラッパー:", "Wrapper:")), 3, 0)
        self.wrapper = QLineEdit(data.get("wrapper", ""))
        self.wrapper.setPlaceholderText(
            tr("（空欄＝ maya.exe を直接起動）  D:/path/to/run.bat {exe} en_EN {file}",
               "(empty = launch maya.exe directly)  D:/path/to/run.bat {exe} en_EN {file}"))
        self.wrapper.setToolTip(tr(
            "maya.exe の代わりに走らせる .bat / .exe。\n"
            "{exe} {file} {port} はそれぞれ Maya の実行ファイル・開くファイル・\n"
            "連携ポートに置き換わります。\n"
            "プレースホルダを書かない場合は «ラッパー maya.exe» の順で繋ぎます。\n"
            "注意: 連携（開く／インポート／リファレンス）は、ラッパーが\n"
            "引数をそのまま maya.exe へ渡す作りになっている必要があります。",
            "A .bat / .exe to run instead of maya.exe.\n"
            "{exe} {file} {port} are replaced with the Maya executable, the "
            "file to open and the bridge port.\n"
            "Without placeholders the wrapper is called as “wrapper maya.exe”.\n"
            "Note: the bridge only works if the wrapper forwards its arguments "
            "to maya.exe."))
        g.addWidget(self.wrapper, 3, 1, 1, 4)

        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 2)

    def _remove(self):
        if callable(self._on_remove):
            self._on_remove(self)

    def get_data(self):
        return {
            "id": self._id,
            "version": self.ver.currentData() or "",
            "label": self.label.text().strip(),
            "args": self.args.text().strip(),
            "env": self.env.text().strip(),
            "wrapper": self.wrapper.text().strip(),
        }


class MayaLaunchDialog(QDialog):
    """起動プロファイルの編集。«同じバージョンで別引数» を並べられる。"""

    def __init__(self, settings_manager, versions, parent=None):
        super().__init__(parent)
        from ui.dialog_util import apply_window_buttons as _awb
        _awb(self)   # r146: ✕ を必ず押せる状態にする
        self._sm = settings_manager
        self._versions = [str(v) for v in versions]
        self._rows = []
        self.setWindowTitle(tr("Maya の起動設定", "Maya Launch Settings"))
        self.setMinimumSize(760, 520)
        self._apply_theme()
        self._build_ui()
        self._load()

    def _apply_theme(self):
        self.setStyleSheet(
            "QDialog{background:%(surface)s;}"
            "QLabel{color:%(on_surface)s;}"
            "QLabel#hint{color:%(on_surface_dim)s;font-size:%(label_px)spx;}"
            "QLineEdit,QComboBox{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:%(r_s)spx;"
            "min-height:22px;padding:0 8px;}"
            "QPushButton{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:%(r_s)spx;"
            "min-height:26px;padding:0 14px;}"
            "QPushButton:hover{background:%(fill_subtle_hover)s;}"
            "QPushButton#primary{background:%(primary)s;color:%(on_primary)s;"
            "border:none;}" % _tv())

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        hint = QLabel(tr(
            "起動するバージョンと引数・環境変数の組み合わせを並べます。\n"
            "同じバージョンを何行でも置けるので、使い分けられます。\n"
            "表示名はマネージャーのプルダウンにそのまま出ます。\n"
            "UI 言語のように «Maya が起動時に読む» 設定は引数では渡せません。\n"
            "«環境変数» に書いてください（例: MAYA_UI_LANGUAGE=en_US）。",
            "List the version / argument / environment combinations to launch.\n"
            "The same version can appear on several rows, so you can keep\n"
            "variants. The display name is what the manager's dropdown shows.\n"
            "Settings Maya reads at startup (such as the UI language) cannot "
            "be passed as arguments — put them in “Environment”."))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("共通の追加引数:", "Shared extra arguments:")), 0)
        self._common_args = QLineEdit()
        self._common_args.setPlaceholderText(
            tr("全ての起動に付ける引数（空欄可）",
               "Applied to every launch (may be empty)"))
        self._common_args.setToolTip(tr(
            "各行の追加引数より «前» に付きます。",
            "Placed before each row's own extra arguments."))
        row.addWidget(self._common_args, 1)
        lay.addLayout(row)

        # r146: 共通の環境変数（全ての起動に載せる）
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(tr("共通の環境変数:", "Shared environment:")), 0)
        self._common_env = QLineEdit()
        self._common_env.setPlaceholderText(
            "MAYA_UI_LANGUAGE=en_US; MAYA_MODULE_PATH=D:/path/to/modules")
        self._common_env.setToolTip(tr(
            "全ての起動に載せる環境変数。各行の «環境変数» が同じ名前を\n"
            "持っていたら、そちらが勝ちます。",
            "Applied to every launch. A row's own value wins when the same "
            "name appears in both."))
        row2.addWidget(self._common_env, 1)
        lay.addLayout(row2)

        self._body = QWidget()
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(4)
        self._body_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(self._body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        lay.addWidget(scroll, 1)

        add = QPushButton(tr("＋ 行を追加", "+ Add row"))
        add.setAutoDefault(False)
        add.clicked.connect(lambda _c=False: self.add_row())
        lay.addWidget(add)

        self._empty_note = QLabel(tr(
            "行が 1 つも無い場合は、検出されたバージョンがそのまま並びます。",
            "With no rows, the detected versions are listed as they are."))
        self._empty_note.setObjectName("hint")
        self._empty_note.setWordWrap(True)
        lay.addWidget(self._empty_note)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(tr("キャンセル", "Cancel"))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        ok = QPushButton(tr("保存", "Save"))
        ok.setObjectName("primary")
        ok.setAutoDefault(False)
        ok.clicked.connect(self._save)
        btns.addWidget(ok)
        lay.addLayout(btns)

    # ------------------------------------------------------------------
    def _load(self):
        self._common_args.setText(str(self._sm.get("maya_extra_args", "") or ""))
        self._common_env.setText(str(self._sm.get("maya_extra_env", "") or ""))
        for data in self._sm.get_maya_launch_profiles():
            self.add_row(data)
        self._update_note()

    def add_row(self, data=None):
        if data is None and not self._rows and self._versions:
            # 最初の 1 行は «いま検出されている最新» を既定にする
            data = {"version": self._versions[0]}
        row = _ProfileRow(self._versions, data, self, on_remove=self._remove_row)
        self._rows.append(row)
        self._body_lay.insertWidget(self._body_lay.count() - 1, row)
        self._update_note()
        return row

    def _remove_row(self, row):
        if row in self._rows:
            self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._update_note()

    def _update_note(self):
        self._empty_note.setVisible(not self._rows)

    def profiles(self):
        out = []
        for r in self._rows:
            d = r.get_data()
            if d["version"]:
                out.append(d)
        return out

    def _save(self):
        self._sm.set("maya_extra_args", self._common_args.text().strip(),
                     save=False)
        self._sm.set("maya_extra_env", self._common_env.text().strip(),
                     save=False)
        self._sm.save_maya_launch_profiles(self.profiles())
        self.accept()
