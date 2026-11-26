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


class _ElideLabel(QLabel):
    """幅が足りなければ «…» で省略するラベル（r153、2026-11-22）。

    書庫カラムの見出し（「xxx.zip（8 ファイル）」）は QLabel の最小幅が
    文字列の全幅になるため、カラムが狭いと右の «解凍» と閉じるボタンが
    カラムの外へ押し出されて見えなかった（撮影で発見）。見出しは縮めて省略し、
    ボタンは必ず見えるようにする。全文はツールチップで見られる。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        from core.compat import QSizePolicy
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(1)

    def setText(self, text):
        self._full = text or ""
        self._refit()

    def full_text(self):
        return self._full

    def _refit(self):
        from core.compat import QFontMetrics
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._full, Qt.ElideMiddle, max(0, self.width() - 2)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refit()


_OPEN_PICKERS = []


def _current_folder_of(widget) -> str:
    """Manager が今表示しているフォルダ（親をたどって current_path を持つものに聞く）。"""
    w = widget
    while w is not None:
        fn = getattr(w, "current_path", None)
        if callable(fn):
            try:
                p = fn() or ""
                if p and os.path.isdir(p):
                    return p
            except Exception as _e:
                _swallow(_e, "ui/archive_column.py _current_folder_of")
        w = w.parentWidget() if hasattr(w, "parentWidget") else None
    return ""


def pick_extract_dest(parent, start: str, on_pick):
    """解凍先を決める小さな窓（r154、実機 2026-11-24 の J-20）。

    r153 は Qt のフォルダ選択の窓（QFileDialog）を出していたが、その窓は Manager の
    フリーズ対策（ファイル情報を UI で待たない）を通らず、大きい・遅いフォルダ
    （Perforce・ネットワーク）を開いたり閉じたりした時に数十秒止まっていた
    （mfm_freeze.log: QFileSystemModel::permissions → GetFileAttributesW で 33 秒）。
    フォルダ一覧は出さず、パスを直接決める:
      * 開いた時は書庫のあるフォルダが入って全選択（そのまま «解凍» で同じ場所）
      * Manager で行き先を表示して «表示中» を押すと、そのフォルダが入る
      * Manager からフォルダをドラッグして落としても入る。貼り付けも可
    非モーダル（開いたまま Manager を操作できる）。決まったら on_pick(フォルダ)。"""
    from core.compat import (QDialog, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton,
                             QToolButton, QStyle)

    class _DropEdit(QLineEdit):
        def __init__(self, *a):
            super().__init__(*a)
            self.setAcceptDrops(True)

        def dragEnterEvent(self, e):
            if e.mimeData().hasUrls():
                e.setDropAction(Qt.CopyAction)   # パスを入れるだけ。元は動かさない
                e.accept()
            else:
                super().dragEnterEvent(e)

        def dragMoveEvent(self, e):
            if e.mimeData().hasUrls():
                e.setDropAction(Qt.CopyAction)
                e.accept()
            else:
                super().dragMoveEvent(e)

        def dropEvent(self, e):
            urls = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
            if urls:
                p = urls[0]
                self.setText(p if os.path.isdir(p) else os.path.dirname(p))
                e.setDropAction(Qt.CopyAction)   # 元フォルダを動かさない
                e.accept()
            else:
                super().dropEvent(e)

    dlg = QDialog(parent.window() if parent is not None else None)
    dlg.setWindowTitle(tr("解凍先", "Extract to"))
    dlg.setWindowModality(Qt.NonModal)
    dlg.setAttribute(Qt.WA_DeleteOnClose, True)
    dlg.setMinimumWidth(520)
    edit = _DropEdit(dlg)
    edit.setObjectName("extractDest")
    edit.setText(os.path.normpath(start) if start else "")
    edit.selectAll()
    edit.setToolTip(tr("解凍先のフォルダ（パスの貼り付け・Manager からのドラッグも可）",
                       "Destination folder (paste a path or drag a folder from the Manager)"))
    here_btn = QToolButton(dlg)
    here_btn.setObjectName("extractUseCurrent")
    here_btn.setIcon(dlg.style().standardIcon(QStyle.SP_DirOpenIcon))
    here_btn.setText(tr("表示中", "Current"))
    here_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    here_btn.setToolTip(tr("Manager で表示中のフォルダを入れる", "Use the folder shown in the Manager"))
    ok_btn = QPushButton(draw_icon("extract", 16), tr("解凍", "Extract"), dlg)
    ok_btn.setObjectName("extractOk")
    ok_btn.setDefault(True)
    cancel_btn = QPushButton(tr("キャンセル", "Cancel"), dlg)
    row1 = QHBoxLayout()
    row1.addWidget(edit, 1)
    row1.addWidget(here_btn)
    row2 = QHBoxLayout()
    row2.addStretch(1)
    row2.addWidget(ok_btn)
    row2.addWidget(cancel_btn)
    lay = QVBoxLayout(dlg)
    lay.addLayout(row1)
    lay.addLayout(row2)

    def _use_current():
        p = _current_folder_of(parent)
        if p:
            edit.setText(os.path.normpath(p))
            edit.selectAll()

    def _ok():
        d = edit.text().strip().strip('"')
        if not d:
            return
        if os.path.exists(d) and not os.path.isdir(d):
            QMessageBox.warning(dlg, tr("解凍", "Extract"),
                                tr("フォルダではありません: %s", "Not a folder: %s") % d)
            return
        dlg.accept()
        on_pick(d)

    here_btn.clicked.connect(_use_current)
    ok_btn.clicked.connect(_ok)
    edit.returnPressed.connect(_ok)
    cancel_btn.clicked.connect(dlg.reject)
    _OPEN_PICKERS.append(dlg)
    dlg.finished.connect(lambda *_a: _OPEN_PICKERS.remove(dlg) if dlg in _OPEN_PICKERS else None)
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
        # 非モーダル: 決まった時に解凍する（Manager は使えたまま）
        pick_extract_dest(parent, os.path.dirname(archive) or "",
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
        self._title = _ElideLabel(hdr)
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
        pick_extract_dest(self, start,
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
    """書庫用のカラムビュー。各カラムへドラッグ設定を流し込む。

    r153（2026-11-22、ユーザー指摘「✕ が見えるまで広げるとスクロールバーが右端から
    離れ、バーが丁度よい所ではボタンがほぼ見えない」）: QColumnView のカラム幅は
    固定で、書庫カラムの幅と関係なかった。**カラムを書庫カラムの幅に合わせる**:
    最後のカラムが右端まで伸びる（スクロールバーは常に右端）。1 階層だけなら
    横スクロールは出ない。幅は自前で決めるので、カラムのつまみは出さない。"""

    _MIN_COL = 150

    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            self.setResizeGripsVisible(False)
        except Exception as _e:
            _swallow(_e, "ui/archive_column.py _ArchiveColumnView grips")
        from core.compat import QTimer
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self.fit_columns)

    def setModel(self, model):
        super().setModel(model)
        try:
            self.selectionModel().currentChanged.connect(lambda *_a: self._fit_timer.start())
        except Exception as _e:
            _swallow(_e, "ui/archive_column.py _ArchiveColumnView setModel")
        self._fit_timer.start()

    def _depth(self) -> int:
        """今見えているカラムの数（ルート＋選んだフォルダの深さ）。"""
        n = 1
        idx = self.currentIndex()
        m = self.model()
        while idx.isValid():
            if m is not None and m.hasChildren(idx):
                n += 1
            idx = idx.parent()
            # 選んだのがファイルなら、その親までがカラム
        return n

    def fit_columns(self):
        try:
            vpw = max(1, self.viewport().width())
            n = max(1, self._depth())
            if n == 1:
                widths = [vpw]
            else:
                each = max(self._MIN_COL, vpw // n)
                last = max(self._MIN_COL, vpw - each * (n - 1))
                widths = [each] * (n - 1) + [last]
            if list(self.columnWidths())[:len(widths)] != widths:
                self.setColumnWidths(widths)
            # 1 階層なら横スクロールは要らない（幅の更新が遅れて一瞬出るのも出さない）
            hb = self.horizontalScrollBar()
            if n == 1:
                self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                hb.setValue(0)
            else:
                self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        except Exception as _e:
            _swallow(_e, "ui/archive_column.py fit_columns")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_timer.start()

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
