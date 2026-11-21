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
from ui.dialog_util import pick_directory  # r145

INNER_ROLE = Qt.UserRole + 11       # 書庫内パス（"/" 区切り）
ISDIR_ROLE = Qt.UserRole + 12
SIZE_ROLE = Qt.UserRole + 13


def draw_icon(kind: str, size: int = 14, color: str = ""):
    """書庫カラムのヘッダの絵（r153、2026-11-21）。

    ユーザー指摘「zip のカラムを閉じるボタンに何のアイコンも無く、閉じ方が分からない」
    「展開と書かれていたりアイコンが無いので、そこから解凍できるとは誰も思わない」。
    «✕» の文字はボタンの内側の余白に押し出されて消えていた（24px 幅で左右 6px の余白）。
    フォントに頼らず線で描く。kind: "close"（×）／"extract"（下向き矢印＋受け皿）。"""
    from core.compat import QPixmap, QPainter, QPen, QColor, QIcon
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    pt = QPainter(pm)
    try:
        pt.setRenderHint(QPainter.Antialiasing, True)
        if not color:
            from core.theme_engine import qss_vars as _qv0
            color = _qv0()["on_surface"]
        pen = QPen(QColor(color))
        pen.setWidthF(max(1.5, size / 8.0))
        pen.setCapStyle(Qt.RoundCap)
        pt.setPen(pen)
        m = size * 0.22
        if kind == "close":
            pt.drawLine(int(m), int(m), int(size - m), int(size - m))
            pt.drawLine(int(size - m), int(m), int(m), int(size - m))
        else:
            cx = size / 2.0
            pt.drawLine(int(cx), int(size * 0.12), int(cx), int(size * 0.62))
            pt.drawLine(int(cx - size * 0.22), int(size * 0.40), int(cx), int(size * 0.62))
            pt.drawLine(int(cx + size * 0.22), int(size * 0.40), int(cx), int(size * 0.62))
            y = int(size * 0.86)
            pt.drawLine(int(size * 0.14), int(size * 0.66), int(size * 0.14), y)
            pt.drawLine(int(size * 0.14), y, int(size * 0.86), y)
            pt.drawLine(int(size * 0.86), y, int(size * 0.86), int(size * 0.66))
    finally:
        pt.end()
    return QIcon(pm)


_OPEN_PICKERS = []


def pick_directory_nonmodal(parent, title: str, start: str, on_pick):
    """解凍先を選ぶ窓を «Manager を止めずに» 出す（r153、2026-11-21）。

    ユーザー指摘「解凍の窓でパスが指定しづらい。代わりに Manager からパスを拾いたいのに、
    ロックされていて Manager としても終わっている」。モーダルの
    getExistingDirectory は Manager 全体を止める。非モーダルで出し、Manager で
    パスをコピーして貼り付けられるようにする。選ばれたら on_pick(フォルダ)。"""
    dlg = QFileDialog(parent, title, start or "")
    dlg.setFileMode(QFileDialog.Directory)
    dlg.setOption(QFileDialog.ShowDirsOnly, True)
    dlg.setOption(QFileDialog.DontUseNativeDialog, True)
    # ユーザー指摘「開いた瞬間、圧縮ファイルがある場所と同じフォルダが選ばれている
    # 見た目でないと、同じ所なのか分からず迷う」: 一つ上の階層を開き、
    # 書庫のあるフォルダを選択した状態で出す（そのまま «選択» で同じ場所に解凍）。
    try:
        st = os.path.normpath(start or "")
        up = os.path.dirname(st)
        if st and up and up != st and os.path.isdir(st):
            dlg.setDirectory(up)
            dlg.selectFile(st)
    except Exception as _e:
        _swallow(_e, "ui/archive_column.py pick_directory_nonmodal select start")
    dlg.setWindowModality(Qt.NonModal)
    dlg.setAttribute(Qt.WA_DeleteOnClose, True)
    _OPEN_PICKERS.append(dlg)

    def _done(*_a):
        try:
            _OPEN_PICKERS.remove(dlg)
        except ValueError:
            pass

    dlg.fileSelected.connect(lambda d: on_pick(d) if d else None)
    dlg.finished.connect(_done)
    dlg.show()
    dlg.raise_()
    return dlg


def extract_archive(parent, archive: str, here: bool, status=None) -> bool:
    """書庫を丸ごと解凍する（r153: ファイルの右クリックからも解凍できるように）。
    here=True: 書庫と同じ場所の «書庫名» フォルダへ。False: 解凍先を選ぶ。"""
    if here:
        base = os.path.splitext(os.path.basename(archive))[0]
        if base.lower().endswith(".tar"):
            base = base[:-4]
        dest = os.path.join(os.path.dirname(archive), base)
    else:
        # 非モーダル: 選ばれた時に解凍する（Manager は使えたまま）
        pick_directory_nonmodal(
            parent, tr("解凍先を選択", "Extract to"), os.path.dirname(archive) or "",
            lambda d: _extract_all(parent, archive, d, status))
        return True
    return _extract_all(parent, archive, dest, status)


def _extract_all(parent, archive, dest, status) -> bool:
    try:
        made = ab.extract_members(archive, None, dest)
    except Exception as e:
        QMessageBox.warning(parent, tr("書庫", "Archive"),
                            tr("解凍できませんでした: %s", "Cannot extract: %s") % (e,))
        return False
    if status is not None:
        status(tr("解凍しました: %d ファイル → %s", "Extracted %d files to %s") % (len(made), dest))
    return True


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
        from core.theme_engine import qss_vars as _qv
        _ic = _qv()["on_plane_archive"]
        self._extract_btn = QToolButton(hdr)
        self._extract_btn.setObjectName("arcExtract")
        self._extract_btn.setIcon(draw_icon("extract", 14, _ic))
        self._extract_btn.setText(tr("解凍", "Extract"))
        self._extract_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._extract_btn.setToolTip(tr("書庫の中身をフォルダへ解凍する（解凍先を選ぶ）",
                                        "Extract the archive to a folder"))
        self._extract_btn.clicked.connect(lambda _c=False: self._extract_dialog(None))
        hl.addWidget(self._extract_btn, 0)
        close_btn = QToolButton(hdr)
        close_btn.setObjectName("arcClose")
        close_btn.setIcon(draw_icon("close", 12, _ic))
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
            "QToolButton#arcClose{padding:0px;}"
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
            note = tr("／🔒パスワード付き（解凍不可）", " / password protected")
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
                                tr("解凍できませんでした: %s", "Cannot extract: %s") % (e,))
            return
        if not paths:
            QMessageBox.warning(
                self, tr("書庫", "Archive"),
                tr("解凍できませんでした（パスワード付きの可能性があります）",
                   "Cannot extract (the archive may be password protected)"))
            return
        self.status_message.emit(
            tr("圧縮ファイルから解凍して開きます: %s", "Extracted and opening: %s")
            % os.path.basename(paths[0]))
        for p in paths:
            self.file_activated.emit(p)

    def _extract_dialog(self, names):
        # r153: 非モーダル（Manager からパスを拾えるように）
        start = os.path.dirname(self._archive) or ""
        pick_directory_nonmodal(self, tr("解凍先を選択", "Extract to"), start,
                                lambda d, n=names: self._extract_to(n, d))

    def _extract_here(self, names):
        base = os.path.splitext(os.path.basename(self._archive))[0]
        dest = os.path.join(os.path.dirname(self._archive), base)
        self._extract_to(names, dest)

    def _extract_to(self, names, dest):
        try:
            made = ab.extract_members(self._archive, names, dest)
        except Exception as e:
            QMessageBox.warning(self, tr("書庫", "Archive"),
                                tr("解凍できませんでした: %s", "Cannot extract: %s") % (e,))
            return
        self.status_message.emit(
            tr("解凍しました: %d ファイル → %s", "Extracted %d files to %s")
            % (len(made), dest))

    def _context_menu(self, pos):
        names = self.selected_inner()
        menu = QMenu(self)
        if names:
            dirs_only = all(self._is_dir_name(n) for n in names)
            if not dirs_only:
                menu.addAction(tr("開く", "Open"),
                               lambda: self._open_inner(names))
            menu.addAction(tr("選択を解凍…", "Extract selected…"),
                           lambda: self._extract_dialog(names))
            menu.addSeparator()
            menu.addAction(tr("書庫内パスをコピー", "Copy path inside archive"),
                           lambda: QApplication.clipboard().setText("\n".join(names)))
            menu.addSeparator()
        menu.addAction(tr("すべてここに解凍", "Extract all here"),
                       lambda: self._extract_here(None))
        menu.addAction(tr("すべて解凍…", "Extract all…"),
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
