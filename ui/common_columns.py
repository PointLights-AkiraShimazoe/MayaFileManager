"""
Common Folder Column
====================
複数フォルダ選択時に、選択フォルダ群の «子階層に共通して存在する同名フォルダ» を
1カラムとして表示する（平坦カラムの左に挿入）。

- 項目 = 共通名フォルダ（その名前を持つ各ソースの実パス群を保持）
- 選択（Ctrl/Shift複数選択可）すると、選択した共通フォルダ群の配下だけが
  平坦ビューにリストされる（フィルタリング）
- さらにその選択ソース群に共通フォルダがあれば、次の共通カラムが右に増える
  （再帰。共通名が無くなるまで）

仕様の意図: 「複数選択したフォルダの中で、さらに任意のフォルダ以下のものだけを
平坦ビューに出す」ためのドリルダウン・フィルタ。
"""

import os

from core.compat import (
    Qt, Signal, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListView,
    QAbstractItemView, QToolButton, QSizePolicy,
)

try:  # PySide6
    from PySide6.QtGui import QStandardItemModel, QStandardItem
except ImportError:  # PySide2
    from PySide2.QtGui import QStandardItemModel, QStandardItem

_SOURCES_ROLE = Qt.UserRole + 11


class CommonFolderColumn(QWidget):
    """共通フォルダのドリルダウン用カラム。"""

    selection_changed = Signal()
    # 平坦化の深さ切替（True=全階層 / False=選択フォルダ直下のみ）
    depth_mode_changed = Signal(bool)

    def __init__(self, level: int, parent=None):
        super().__init__(parent)
        self.level = level
        self.setMinimumWidth(160)
        self.setObjectName("mfmCommonCol")
        # 色はトークンから（共通フォルダ列だけ緑寄りの面で種別を示す。r82）
        from core.theme_engine import qss_vars
        self.setStyleSheet(
            "#mfmCommonCol{background:%(plane_common)s;}"
            "QWidget#cchdrbar{background:%(plane_common_header)s;"
            "border-bottom:1px solid %(hairline)s;}"
            "QLabel#cchdr{background:transparent;color:%(on_plane_common)s;"
            "padding:3px 6px;font-weight:%(w_strong)s;}"
            "QToolButton#depthsw{background:%(fill_subtle)s;color:%(on_plane_common)s;"
            "border:1px solid %(hairline_strong)s;border-radius:9px;"
            "padding:0px 4px;min-height:16px;max-height:20px;font-weight:%(w_strong)s;}"
            "QToolButton#depthsw:checked{background:%(primary)s;color:%(on_primary)s;"
            "border-color:%(primary)s;}"
            "QListView{background:%(plane_common)s;color:%(on_surface)s;border:none;}"
            % qss_vars()
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        from core.i18n import tr
        # 緑バー: タイトル ＋ 深さ切替スイッチ
        bar = QWidget(self)
        bar.setObjectName("cchdrbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 4, 0)
        bl.setSpacing(4)
        self._hdr = QLabel(tr("⊞ 子フォルダ", "⊞ Subfolders"), bar)
        self._hdr.setObjectName("cchdr")
        # タイトル側を縮小可能にして、スイッチの文言が省略されないようにする
        self._hdr.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bl.addWidget(self._hdr, 1)
        self._depth_btn = QToolButton(bar)
        self._depth_btn.setObjectName("depthsw")
        self._depth_btn.setCheckable(True)
        self._depth_btn.setChecked(True)          # 既定: 全階層
        self._depth_btn.setToolTip(tr(
            "平坦表示の深さ: ON=選択フォルダ以下の全階層 / OFF=選択フォルダ直下のみ",
            "Flat depth: ON = all levels below selection / OFF = direct children only"))
        self._depth_btn.toggled.connect(self._on_depth_toggled)
        self._depth_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        bl.addWidget(self._depth_btn, 0)
        self._update_depth_look()
        lay.addWidget(bar, 0)
        self._model = QStandardItemModel(self)
        self._view = QListView(self)
        self._view.setModel(self._model)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._view.setUniformItemSizes(True)
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self._view, 1)
        sm = self._view.selectionModel()
        if sm is not None:
            sm.selectionChanged.connect(lambda *_a: self.selection_changed.emit())

    def set_entries(self, entries):
        """entries: [(表示名, [ソース実パス...]), ...]（共通名のみ渡す想定）。"""
        self._model.clear()
        for name, sources in entries:
            it = QStandardItem("📁 %s  (%d)" % (name, len(sources)))
            it.setEditable(False)
            it.setData(list(sources), _SOURCES_ROLE)
            it.setToolTip("\n".join(sources))
            self._model.appendRow(it)
        from core.i18n import tr
        self._hdr.setText(tr("⊞ 子フォルダ  L%d", "⊞ Subfolders  L%d")
                          % (self.level + 1))

    # --- 深さ切替 --------------------------------------------------------
    _ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "resources", "icons")

    def _update_depth_look(self):
        """ON=全階層 / OFF=直下のみ をアイコンで表現（resources/icons のSVG）。
        SVGが読めない環境ではテキストにフォールバック。"""
        from core.i18n import tr
        on = self._depth_btn.isChecked()
        # ChatGPT でデザインした PNG（透明背景・フロスト色）を優先、無ければ SVG
        stem = "flat_all_levels" if on else "flat_direct_only"
        icon = None
        try:
            from core.compat import QIcon
            for ext in (".png", ".svg"):
                cand = QIcon(os.path.join(self._ICON_DIR, stem + ext))
                if not cand.isNull() and not cand.pixmap(16, 16).isNull():
                    icon = cand
                    break
        except Exception:
            icon = None
        if icon is not None:
            from core.compat import QSize
            self._depth_btn.setIcon(icon)
            self._depth_btn.setIconSize(QSize(18, 18))
            self._depth_btn.setText("")
            self._depth_btn.setFixedSize(30, 22)
        else:
            self._depth_btn.setText(tr("⇊ 全階層", "⇊ All levels") if on
                                    else tr("⇣ 直下のみ", "⇣ Direct only"))
            self._depth_btn.adjustSize()
            self._depth_btn.setMinimumWidth(self._depth_btn.sizeHint().width())
        self._depth_btn.setToolTip(
            tr("平坦表示の深さ: 現在「%s」（クリックで切替）",
               "Flat depth: now \"%s\" (click to toggle)")
            % (tr("全階層", "All levels") if on else tr("直下のみ", "Direct only")))

    def _on_depth_toggled(self, on: bool):
        self._update_depth_look()
        self.depth_mode_changed.emit(bool(on))

    def set_recursive(self, on: bool):
        """他カラムとの同期用（シグナルは出さない）。"""
        self._depth_btn.blockSignals(True)
        self._depth_btn.setChecked(bool(on))
        self._depth_btn.blockSignals(False)
        self._update_depth_look()

    def is_recursive(self) -> bool:
        return self._depth_btn.isChecked()

    def selected_sources(self):
        """選択された共通フォルダの実パス群（union）。"""
        out = []
        sm = self._view.selectionModel()
        if sm is None:
            return out
        for idx in sm.selectedIndexes():
            srcs = idx.data(_SOURCES_ROLE) or []
            for s in srcs:
                if s not in out:
                    out.append(s)
        return out

    def has_selection(self):
        sm = self._view.selectionModel()
        return sm is not None and bool(sm.selectedIndexes())
