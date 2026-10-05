# -*- coding: utf-8 -*-
"""Maya 連携（userSetup.py）のセットアップ（r119）。

ここが解いている問題
--------------------
連携ポートは **Maya の起動時** に開く。その仕掛けを書く userSetup.py の
置き場は Maya 自身が «ドキュメント» をどう解決するかで決まり、Windows の
既知フォルダ API とは一致しないことがある（OneDrive のフォルダ移動を
Maya が無視する環境がある）。推測で書くと «インストールしたのに効かない»。

そして **セットアップ時に Maya が起動していないことの方が多い**。
そこで:

* 場所は «実在するフォルダ»（20xx のバージョンフォルダがある方）で決める。
* どこへ入れるかを必ず見せ、参照で変えられるようにする。
* 全バージョン共通 / バージョン別 を **ユーザーに選ばせる**
  （Maya は両方の userSetup.py を実行するので、どちらでも効く）。
* 後で Maya が繋がった時に食い違いを検出し、そちらへの追加を促す
  （MainWindow 側の責務。ここは «選ばせて書く» までを持つ）。
"""

import os

from core.compat import (
    Qt, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QCheckBox, QScrollArea, QFileDialog,
)
from core.diag import swallow as _swallow
from core.i18n import tr
from core import maya_bridge as mb


def _tv():
    from core.theme_engine import qss_vars
    return qss_vars()


_SOURCE_TEXT = {
    "confirmed": ("接続中の Maya に確認済み", "confirmed by a running Maya"),
    "manual": ("手動で指定した場所", "chosen by you"),
    "env": ("環境変数 MAYA_APP_DIR の値（Maya 側と一致するかは未確認）",
            "from MAYA_APP_DIR (not verified against Maya)"),
    "found": ("バージョンフォルダが実在するので、ほぼ確実",
              "a folder with Maya version subfolders exists here"),
    "guess": ("推測（該当フォルダがまだ無い）",
              "a guess (no such folder yet)"),
}


class MayaBridgeDialog(QDialog):
    """連携の書き込み先（フォルダ＋対象）を選ばせる。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Maya連携のセットアップ", "Set Up Maya Bridge"))
        self.setMinimumWidth(620)
        self._targets = []          # [(version or None, QCheckBox)]
        self._apply_theme()
        self._build_ui()
        self._reload()

    # ------------------------------------------------------------------
    def _apply_theme(self):
        self.setStyleSheet(
            "QDialog{background:%(surface)s;}"
            "QLabel{color:%(on_surface)s;}"
            "QLabel#hint{color:%(on_surface_dim)s;font-size:%(label_px)spx;}"
            "QLabel#warn{color:%(warning)s;font-size:%(label_px)spx;}"
            "QLabel#path{color:%(on_surface_variant)s;}"
            "QLineEdit{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:%(r_s)spx;"
            "min-height:24px;padding:0 8px;}"
            "QCheckBox{color:%(on_surface)s;}"
            "QPushButton{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:%(r_s)spx;"
            "min-height:26px;padding:0 14px;}"
            "QPushButton:hover{background:%(fill_subtle_hover)s;}"
            "QPushButton#primary{background:%(primary)s;color:%(on_primary)s;"
            "border:none;}" % _tv())

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        lay.addWidget(QLabel(tr(
            "Maya の起動時に連携ポートを開くための設定を書き込みます。\n"
            "これにより、マネージャー以外から起動した Maya も「接続:」リストに出ます。",
            "Writes the setting that opens a bridge port when Maya starts, so\n"
            "Mayas launched outside this manager also appear in the Connect list.")))

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Maya の設定フォルダ:", "Maya user folder:")), 0)
        self._dir_edit = QLineEdit(self)
        self._dir_edit.setToolTip(tr(
            "中に 2026 などのバージョンフォルダがある階層です。",
            "The folder that contains version subfolders such as 2026."))
        self._dir_edit.textChanged.connect(lambda _t: self._reload_targets())
        row.addWidget(self._dir_edit, 1)
        browse = QPushButton(tr("参照...", "Browse..."), self)
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        row.addWidget(browse, 0)
        lay.addLayout(row)

        self._src_label = QLabel("")
        self._src_label.setObjectName("hint")
        self._src_label.setWordWrap(True)
        lay.addWidget(self._src_label)

        lay.addWidget(QLabel(tr("書き込み先", "Install to")))
        body = QWidget(self)
        self._body_lay = QVBoxLayout(body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(4)
        scroll = QScrollArea(self)
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setMinimumHeight(140)
        lay.addWidget(scroll, 1)

        note = QLabel(tr(
            "「全バージョン共通」は 1 つ書くだけで全ての Maya に効きます。\n"
            "特定のバージョンだけに効かせたい時はバージョン別を選んでください。\n"
            "（反映は次回の Maya 起動から。作業中の Maya は\n"
            "　ツール →「起動中の Maya を今すぐ接続...」で繋げます）",
            "“All versions” needs one file and applies to every Maya.\n"
            "Pick a version to limit it to that one.\n"
            "(Takes effect from the next Maya launch; for a Maya that is\n"
            "　already running, use “Connect a running Maya now...”)"))
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(tr("キャンセル", "Cancel"), self)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        self._ok = QPushButton(tr("書き込む", "Write"), self)
        self._ok.setObjectName("primary")
        self._ok.setAutoDefault(False)
        self._ok.clicked.connect(self.accept)
        btns.addWidget(self._ok)
        lay.addLayout(btns)

    # ------------------------------------------------------------------
    def _browse(self):
        d = QFileDialog.getExistingDirectory(
            self,
            tr("Maya の設定フォルダを選択（中に 2026 等があります）",
               "Select Maya's user folder (it contains 2026, ...)"),
            self._dir_edit.text() or mb.maya_app_dir())
        if d:
            mb.set_maya_app_dir(d)
            self._dir_edit.setText(mb.maya_app_dir())

    def _reload(self):
        self._dir_edit.setText(mb.maya_app_dir())
        self._reload_targets()

    def directory(self) -> str:
        return self._dir_edit.text().strip()

    def _reload_targets(self):
        """フォルダを見て «全バージョン共通＋実在するバージョン» を並べ直す。

        r120: 以前は「userSetup.py は最初に見つかった 1 つしか実行されない」
        という前提で «隠している版» を警告していたが、**これは誤り**だった。
        実機ログ（2026-10-02）で、1 回の Maya 起動に対して共通とバージョン別の
        userSetup.py が **両方実行される** ことを確認した（スニペットが残す
        痕跡が 2 行出た）。したがって共通に 1 つ書けば全てのバージョンに効く。
        """
        d = self.directory()
        if d and d != mb.maya_app_dir():
            mb.set_maya_app_dir(d)
        src = mb.app_dir_source()
        self._src_label.setText(tr("この場所は %s", "This location is %s")
                                % tr(*_SOURCE_TEXT.get(src, ("", ""))))
        while self._body_lay.count():
            it = self._body_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._targets = []
        versions = mb.installed_versions()
        rows = [(None, tr("全バージョン共通", "All versions"))]
        for v in versions:
            rows.append((v, "Maya %s" % v))

        for ver, label in rows:
            try:
                p = mb.usersetup_path(ver)
                done = mb.is_usersetup_installed(ver)
                stale = mb.usersetup_is_outdated(ver)
            except Exception as _e:
                _swallow(_e, "ui/maya_bridge_dialog.py _reload_targets")
                continue
            mark = ""
            if stale:
                mark = "  " + tr("（古い版 — 要更新）", "(old version - update)")
            elif done:
                mark = "  " + tr("（設定済み）", "(already set up)")
            cb = QCheckBox(label + mark, self)
            # 既定: 古い版はどこであれ直す / それ以外は «共通が未導入なら共通だけ»
            if stale:
                want = True
            elif ver is None:
                want = not done
            else:
                want = False
            cb.setChecked(bool(want))
            cb.setToolTip(p)
            self._body_lay.addWidget(cb)
            lb = QLabel(p, self)
            lb.setObjectName("path")
            lb.setWordWrap(True)
            lb.setIndent(22)
            self._body_lay.addWidget(lb)
            self._targets.append((ver, cb))

        if not versions:
            hint = QLabel(tr(
                "このフォルダにバージョンフォルダ（2026 など）が見当たりません。\n"
                "Maya を一度も起動していないか、場所が違う可能性があります。",
                "No version folders (2026, ...) found here. Maya may never have "
                "been started, or this is the wrong folder."), self)
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            self._body_lay.addWidget(hint)
        if any(cb.isChecked() for _v, cb in self._targets):
            self._ok.setText(tr("書き込む", "Write"))
        else:
            self._ok.setText(tr("最新の内容に更新", "Update to latest"))
        self._body_lay.addStretch(1)

    def selected_targets(self):
        """チェックされた対象（None=全バージョン共通 / "2026" 等）。"""
        return [ver for ver, cb in self._targets if cb.isChecked()]
