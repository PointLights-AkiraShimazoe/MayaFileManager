# -*- coding: utf-8 -*-
"""Archive Column（r97）

zip / tar 系の中身を «そのまま» たどるためのインラインカラム。
通常のカラム（QFileSystemModel）とは独立に QStandardItemModel で動く。

できること／できないことは Windows 標準のエクスプローラーと同じ方針
（詳細は core/archive_browse.py の冒頭を参照）。書庫の中身は **読み取り専用**。

見た目: «通常のフォルダではない» ことが一目で分かるよう、面と文字色を
plane_archive / on_plane_archive（琥珀寄り）に振っている。
"""
from core.diag import swallow as _swallow  # r112

import os

from core.compat import (
    Qt, Signal, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton,
    QColumnView, QAbstractItemView, QMenu, QMessageBox,
    QFileDialog, QApplication, QUrl, QMimeData, )

try:  # PySide6
    from PySide6.QtGui import QStandardItemModel, QStandardItem
    try:
        from PySide6.QtGui import QFileIconProvider
    except ImportError:
        from PySide6.QtWidgets import QFileIconProvider
except ImportError:  # PySide2
    from PySide2.QtGui import QStandardItemModel, QStandardItem
    from PySide2.QtWidgets import QFileIconProvider

from core import archive_browse as ab
from core.i18n import tr

INNER_ROLE = Qt.UserRole + 11       # 書庫内パス（"/" 区切り）
ISDIR_ROLE = Qt.UserRole + 12
SIZE_ROLE = Qt.UserRole + 13


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


class _ArchiveModel(QStandardItemModel):
    """書庫の中身ツリー。D&D 時は «テンポラリへ展開した実ファイル» を渡す
    （エクスプローラーと同じ。書庫の中身を直接掴むことはできないため）。"""

    def __init__(self, archive_path, parent=None):
        super().__init__(parent)
        self._archive = archive_path

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        names = []
        for i in indexes:
            if i.column() != 0:
                continue
            n = i.data(INNER_ROLE)
            if n and n not in names:
                names.append(n)
        mime = QMimeData()
        try:
            paths = ab.extract_to_temp(self._archive, names)
            mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        except Exception as _e:
            _swallow(_e, "ui/archive_column.py:69 mimeData")
        return mime


class ArchiveColumn(QWidget):
    """書庫の中身をたどるインラインカラム。"""

    closed = Signal()
    file_activated = Signal(str)        # 展開済みの実パス
    status_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._icons = QFileIconProvider()
        self._archive = ""
        self._entries = []
        self.setMinimumWidth(220)
        self.setObjectName("mfmArchiveCol")
        self._apply_theme()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        hdr = QWidget(self)
        hdr.setObjectName("arcHdr")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(6, 3, 3, 3)
        hl.setSpacing(4)
        self._title = QLabel(hdr)
        self._title.setObjectName("arcTitle")
        hl.addWidget(self._title, 1)
        self._extract_btn = QToolButton(hdr)
        self._extract_btn.setText(tr("展開…", "Extract…"))
        self._extract_btn.setToolTip(tr("書庫の中身をフォルダへ展開する",
                                        "Extract the archive to a folder"))
        self._extract_btn.clicked.connect(lambda _c=False: self._extract_dialog(None))
        hl.addWidget(self._extract_btn, 0)
        close_btn = QToolButton(hdr)
        close_btn.setText("✕")
        close_btn.setFixedSize(24, 20)
        close_btn.setToolTip(tr("書庫カラムを閉じる", "Close archive column"))
        close_btn.clicked.connect(self.closed.emit)
        hl.addWidget(close_btn, 0)
        lay.addWidget(hdr, 0)

        self._model = None
        self._view = _ArchiveColumnView(self)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._context_menu)
        self._view.activated.connect(self._on_activated)
        lay.addWidget(self._view, 1)

    # ------------------------------------------------------------------
    def _apply_theme(self):
        from core.theme_engine import qss_vars
        self.setStyleSheet(
            "#mfmArchiveCol{background:%(plane_archive)s;}"
            "QWidget#arcHdr{background:%(plane_archive_header)s;"
            "border-bottom:1px solid %(hairline)s;}"
            "QLabel#arcTitle{color:%(on_plane_archive)s;}"
            "QToolButton{background:%(fill_subtle)s;color:%(on_plane_archive)s;"
            "border:1px solid %(hairline)s;border-radius:9px;"
            "min-height:16px;max-height:18px;padding:0px 6px;}"
            "QToolButton:hover{background:%(fill_subtle_hover)s;}"
            "QColumnView{background:%(plane_archive)s;border:none;}"
            "QListView{background:%(plane_archive)s;color:%(on_plane_archive)s;"
            "border:none;border-right:1px solid %(hairline)s;}"
            % qss_vars()
        )

    def archive_path(self) -> str:
        return self._archive

    def set_archive(self, path) -> bool:
        """書庫を読み込んで一覧を作る。読めなければ False（呼び出し側で非表示に）。"""
        self._archive = path or ""
        try:
            self._entries = ab.list_entries(self._archive)
        except ValueError as e:
            self._entries = []
            self.status_message.emit(str(e))
            return False
        name = os.path.basename(self._archive)
        n_files = len([e for e in self._entries if not e.is_dir])
        note = ""
        if ab.has_encrypted(self._entries):
            note = tr("／🔒パスワード付き（展開不可）", " / password protected")
        self._title.setText(tr("📦 %s（%d ファイル）%s", "📦 %s (%d files)%s")
                            % (name, n_files, note))
        self._title.setToolTip(self._archive)
        self._build_model()
        return True

    def _build_model(self):
        model = _ArchiveModel(self._archive, self)
        root = model.invisibleRootItem()

        def fill(parent_item, inner):
            for e in ab.children(self._entries, inner):
                it = QStandardItem(e.base)
                it.setEditable(False)
                it.setData(e.name.rstrip("/"), INNER_ROLE)
                it.setData(bool(e.is_dir), ISDIR_ROLE)
                it.setData(int(e.size), SIZE_ROLE)
                it.setIcon(self._icon_for(e))
                if e.is_dir:
                    it.setToolTip(e.name.rstrip("/"))
                    fill(it, e.name.rstrip("/"))
                else:
                    it.setToolTip("%s  (%s)" % (e.name, _human(e.size)))
                parent_item.appendRow(it)

        fill(root, "")
        self._model = model
        self._view.setModel(model)

    def _icon_for(self, entry):
        from core.compat import QFileInfo
        if entry.is_dir:
            return self._icons.icon(QFileIconProvider.Folder)
        # 実体が無くても «拡張子だけ» でアイコンは引ける
        return self._icons.icon(QFileInfo(entry.base))

    # ------------------------------------------------------------------
    def selected_inner(self):
        out = []
        sm = self._view.selectionModel()
        if sm is None:
            return out
        for i in sm.selectedIndexes():
            if i.column() != 0:
                continue
            n = i.data(INNER_ROLE)
            if n and n not in out:
                out.append(n)
        return out

    def _on_activated(self, index):
        if not index.isValid() or index.data(ISDIR_ROLE):
            return
        self._open_inner([index.data(INNER_ROLE)])

    def _open_inner(self, names):
        names = [n for n in (names or []) if n]
        if not names:
            return
        try:
            paths = ab.extract_to_temp(self._archive, names)
        except Exception as e:
            QMessageBox.warning(self, tr("書庫", "Archive"),
                                tr("展開できませんでした: %s", "Cannot extract: %s") % (e,))
            return
        if not paths:
            QMessageBox.warning(
                self, tr("書庫", "Archive"),
                tr("展開できませんでした（パスワード付きの可能性があります）",
                   "Cannot extract (the archive may be password protected)"))
            return
        self.status_message.emit(
            tr("書庫から展開して開きます: %s", "Extracted and opening: %s")
            % os.path.basename(paths[0]))
        for p in paths:
            self.file_activated.emit(p)

    def _extract_dialog(self, names):
        start = os.path.dirname(self._archive) or ""
        dest = QFileDialog.getExistingDirectory(
            self, tr("展開先を選択", "Extract to"), start,
            QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog)
        if dest:
            self._extract_to(names, dest)

    def _extract_here(self, names):
        base = os.path.splitext(os.path.basename(self._archive))[0]
        dest = os.path.join(os.path.dirname(self._archive), base)
        self._extract_to(names, dest)

    def _extract_to(self, names, dest):
        try:
            made = ab.extract_members(self._archive, names, dest)
        except Exception as e:
            QMessageBox.warning(self, tr("書庫", "Archive"),
                                tr("展開できませんでした: %s", "Cannot extract: %s") % (e,))
            return
        self.status_message.emit(
            tr("展開しました: %d ファイル → %s", "Extracted %d files to %s")
            % (len(made), dest))

    def _context_menu(self, pos):
        names = self.selected_inner()
        menu = QMenu(self)
        if names:
            dirs_only = all(self._is_dir_name(n) for n in names)
            if not dirs_only:
                menu.addAction(tr("開く", "Open"),
                               lambda: self._open_inner(names))
            menu.addAction(tr("選択を展開…", "Extract selected…"),
                           lambda: self._extract_dialog(names))
            menu.addSeparator()
            menu.addAction(tr("書庫内パスをコピー", "Copy path inside archive"),
                           lambda: QApplication.clipboard().setText("\n".join(names)))
            menu.addSeparator()
        menu.addAction(tr("すべてここに展開", "Extract all here"),
                       lambda: self._extract_here(None))
        menu.addAction(tr("すべて展開…", "Extract all…"),
                       lambda: self._extract_dialog(None))
        menu.addSeparator()
        note = menu.addAction(tr("※書庫の中身は読み取り専用です",
                                 "Archive contents are read-only"))
        note.setEnabled(False)
        gp = self._view.viewport().mapToGlobal(pos) if self._view.viewport() else pos
        menu.exec(gp) if hasattr(menu, "exec") else menu.exec_(gp)
        menu.deleteLater()      # r124: 右クリックの回数だけ溜めない

    def _is_dir_name(self, inner):
        for e in self._entries:
            if e.name.rstrip("/") == inner:
                return e.is_dir
        return False


class _ArchiveColumnView(QColumnView):
    """書庫用のカラムビュー。各カラムへドラッグ設定を流し込む。"""

    def createColumn(self, index):
        view = super().createColumn(index)
        try:
            view.setDragEnabled(True)           # 外へ D&D（展開して渡す）
            view.setAcceptDrops(False)          # 書庫への書き込みは不可
            view.setDragDropMode(QAbstractItemView.DragOnly)
            view.setEditTriggers(QAbstractItemView.NoEditTriggers)
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        except Exception as _e:
            _swallow(_e, "ui/archive_column.py:302 createColumn")
        return view
