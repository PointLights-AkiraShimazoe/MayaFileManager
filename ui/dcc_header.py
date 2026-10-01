# -*- coding: utf-8 -*-
"""
ヘッダの DCC ブロック（r67、ユーザーのスケッチ準拠）
=====================================================

メニューバー中央に 2 段で配置する:

    上段: [Maya バージョン] [M] [◯━━ スイッチ ━━◯] [b] [Blender バージョン]
    下段: [接続中 Maya    ] [⟳]   [   起動   ]     [🗖] [接続中 Blender   ]

右端（別ブロック、2段重ね）: [クリック動作アイコン][コンボ] / [D&D アイコン][コンボ]

アイコンは resources/icons/hdr_*.png（ChatGPT でデザイン）。無ければ文字で代替。
M / b は «ブランドロゴではなく» 本ツール独自の文字バッジ（ロゴの再現はしない）。
色は theme_engine のトークン（#mfmDccBlock 系）で付ける。
"""
from core.diag import swallow as _swallow  # r112

import os

from core.compat import (
    Qt, Signal, QWidget, QGridLayout, QLabel, QComboBox,
    QToolButton, QPushButton, QSize, QIcon, QPainter, QColor,
)
from core.i18n import tr

_ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "resources", "icons")


_ICON_CACHE = {}


class _PopupCombo(QComboBox):
    """ヘッダ用コンボ（r71）: 本体は固定幅のまま、展開リストは «項目の文字幅» と
    «項目数» に合わせて広げる（固定幅 196px のままだと接続一覧の
    「Maya 2027 — scene.ma (:20261)」が途中で切れ、1行の高さも潰れて
    «全然見えない» と指摘された）。"""

    def showPopup(self):
        view = self.view()
        try:
            fm = view.fontMetrics()
            w = max((fm.horizontalAdvance(self.itemText(i)) for i in range(self.count())),
                    default=0)
            view.setMinimumWidth(max(self.width(), w + 40))
            rows = max(1, min(self.count(), self.maxVisibleItems()))
            rh = max(view.sizeHintForRow(0), 22)
            view.setMinimumHeight(rows * rh + 6)
        except Exception as _e:
            _swallow(_e, "ui/dcc_header.py:49 showPopup")
        super().showPopup()


def header_icon(stem: str) -> QIcon:
    """resources/icons/hdr_<stem>.png があればそれ、無ければ core/hdr_icons.py の
    埋め込みデータ（ChatGPT デザイン、24px アルファ）から生成。どちらも無ければ空。"""
    if stem in _ICON_CACHE:
        return _ICON_CACHE[stem]
    p = os.path.join(_ICON_DIR, "hdr_%s.png" % stem)
    ic = QIcon(p) if os.path.isfile(p) else QIcon()
    if ic.isNull():
        try:
            from core.hdr_icons import make_pixmap
            pm = make_pixmap(stem, 24)
            if pm is not None and not pm.isNull():
                ic = QIcon(pm)
        except Exception:
            ic = QIcon()
    _ICON_CACHE[stem] = ic
    return ic


def _icon_button(stem: str, fallback_text: str, tip: str, size: int = 22) -> QToolButton:
    b = QToolButton()
    ic = header_icon(stem)
    if not ic.isNull():
        b.setIcon(ic)
        b.setIconSize(QSize(size - 6, size - 6))
    else:
        b.setText(fallback_text)
    b.setToolTip(tip)
    b.setFixedSize(size, size)
    b.setCursor(Qt.PointingHandCursor)
    return b


def app_icon_for(exe_path) -> QIcon:
    """インストール済み DCC の実行ファイルから «正式なアプリアイコン» を取り出す
    （OS のシェルが exe に埋め込まれたアイコンを返す。ロゴを描くのではなく、
    ユーザーの PC にあるアプリ自身のアイコンを表示する）。無ければ空。"""
    try:
        if not exe_path:
            return QIcon()
        from core.compat import QtWidgets, QtCore
        p = str(exe_path)
        # r76: Microsoft Store 版（WindowsApps / アプリ実行エイリアス）はシェルの
        # アイコン取得（SHGetFileInfo、描画時に遅延実行）がパッケージ解決で
        # 固まり、mayapy ではプロセスごと落ちた（2026-09-16 実機: 起動直後に
        # 1.5 秒停止 → access violation）。文字バッジのままにする。
        if "windowsapps" in p.lower():
            return QIcon()
        if not os.path.isfile(p):
            return QIcon()
        prov = QtWidgets.QFileIconProvider()
        ic = prov.icon(QtCore.QFileInfo(p))
        if ic is None or ic.isNull():
            return QIcon()
        # 遅延エンジンをここで確定させる（描画中に初めてシェルへ行かせない）。
        # 取れなければ文字バッジに戻す。
        pm = ic.pixmap(18, 18)
        if pm.isNull():
            return QIcon()
        out = QIcon()
        out.addPixmap(pm)
        return out
    except Exception:
        return QIcon()


class _HeaderRow(QWidget):
    """ヘッダ行の手動レイアウト（r69）: メニューは左端、動作ブロックは右端、
    DCC ブロックは «起動ボタンがウィンドウの中央に来る» 位置に置く。
    QHBoxLayout の伸縮では左右の幅差で中央がずれるため自前で配置する。"""

    def __init__(self, menubar, dcc_block, action_block, parent=None):
        super().__init__(parent)
        self._mb, self._dcc, self._act = menubar, dcc_block, action_block
        for w in (menubar, dcc_block, action_block):
            w.setParent(self)
            w.show()

    def sizeHint(self):
        h = max(self._mb.sizeHint().height(), self._dcc.sizeHint().height(),
                self._act.sizeHint().height())
        return QSize(self._mb.sizeHint().width() + self._dcc.sizeHint().width()
                     + self._act.sizeHint().width() + 24, h)

    def minimumSizeHint(self):
        return self.sizeHint()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def showEvent(self, e):
        super().showEvent(e)
        self._relayout()

    def _relayout(self):
        W, H = self.width(), self.height()
        mbs, ds, as_ = self._mb.sizeHint(), self._dcc.sizeHint(), self._act.sizeHint()
        self._mb.setGeometry(0, 0, mbs.width(), mbs.height())
        self._act.setGeometry(W - as_.width(), max(0, (H - as_.height()) // 2),
                              as_.width(), as_.height())
        # 起動ボタンの中心を W/2 に合わせる（DCC ブロック内での起動ボタン位置を引く）
        try:
            self._dcc.resize(ds)
            if self._dcc.layout() is not None:
                self._dcc.layout().activate()
            lb = self._dcc.launch_btn
            launch_cx = lb.x() + lb.width() // 2
            if launch_cx <= 0:
                launch_cx = ds.width() // 2
        except Exception:
            launch_cx = ds.width() // 2
        x = W // 2 - launch_cx
        # メニュー／動作ブロックと重ならない範囲に収める
        x = max(mbs.width() + 6, min(x, W - as_.width() - ds.width() - 6))
        self._dcc.setGeometry(x, max(0, (H - ds.height()) // 2), ds.width(), ds.height())


class DccSwitch(QWidget):
    """Maya ⇔ Blender の切替（丸いノブが左右へ動くピル）。
    クリックで反転。value: "maya"（左）/ "blender"（右）。

    r88: «ON/OFF スイッチ» に見えないようにする（ユーザー指摘: Maya=OFF、
    Blender=ON に見える）。これは二者択一のセレクタなので、
      - トラックの色は左右どちらでも同じ（右側だけ色が付く、をやめた）
      - ノブはどちら側でも同じアクセント色
    にして «位置» だけで選択を示す。どちらが選ばれているかは左右の
    M / B バッジの強調でも分かる。"""

    changed = Signal(str)

    def __init__(self, value="maya", parent=None):
        super().__init__(parent)
        self._value = "blender" if value == "blender" else "maya"
        self.setFixedSize(46, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr("操作対象の DCC を切り替え（Maya ⇔ Blender）。\n"
                           "fbx/obj/abc/usd 等の共通形式は選択中の DCC へ送ります。",
                           "Switch target DCC (Maya ⇔ Blender). Interchange formats "
                           "go to the selected DCC."))
        # 色はテーマトークン（theme_engine.switch_colors）から。無ければ既定
        try:
            from core.theme_engine import switch_colors
            self._colors = switch_colors()
        except Exception:
            # Mercury のフォールバック（theme_engine が読めなかった場合のみ）
            self._colors = {"track": "#272735", "knob": "#5266eb",
                            "border": "#70707d", "tick": "#70707d"}

    def value(self) -> str:
        return self._value

    def set_value(self, v: str, emit: bool = True):
        v = "blender" if v == "blender" else "maya"
        if v == self._value:
            return
        self._value = v
        self.update()
        if emit:
            self.changed.emit(v)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.set_value("maya" if self._value == "blender" else "blender")
            e.accept()
        else:
            super().mousePressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            r = self.rect().adjusted(1, 1, -1, -1)
            right = self._value == "blender"
            # トラックは左右どちらでも同じ色（ON/OFF の含意を持たせない）
            p.setPen(QColor(self._colors["border"]))
            p.setBrush(QColor(self._colors["track"]))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            d = r.height() - 4
            # 反対側の «止まり位置» に小さな点を打ち、2 択であることを示す
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self._colors.get("tick", self._colors["border"])))
            dot = 4
            cy = r.center().y() - dot / 2 + 1
            other_cx = (r.left() + 2 + d / 2) if right else (r.right() - 1 - d / 2)
            p.drawEllipse(int(other_cx - dot / 2), int(cy), dot, dot)
            # ノブ（どちら側でも同じアクセント色）
            x = (r.right() - d - 1) if right else (r.left() + 2)
            p.setBrush(QColor(self._colors["knob"]))
            p.drawEllipse(x, r.top() + 2, d, d)
        finally:
            p.end()


class DccHeader(QWidget):
    """2段の DCC ブロック。値の保持は MainWindow 側（このクラスは部品を並べて
    シグナルを出すだけ）。"""

    dcc_changed = Signal(str)          # "maya" / "blender"
    maya_setup_requested = Signal()    # Maya の起動設定を開く（r119）
    launch_requested = Signal()        # 選択中 DCC の起動
    refresh_requested = Signal()       # 両方の接続一覧を更新
    front_requested = Signal()         # 選択中 DCC を最前面へ

    def __init__(self, dcc="maya", parent=None):
        super().__init__(parent)
        self.setObjectName("mfmDccBlock")
        self.setAttribute(Qt.WA_StyledBackground, True)
        g = QGridLayout(self)
        g.setContentsMargins(6, 2, 6, 2)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(3)

        # 上段
        self.maya_ver = _PopupCombo()
        self.maya_ver.setFixedWidth(136)
        self.maya_ver.setToolTip(tr("起動する Maya のバージョン", "Maya version to launch"))
        self.maya_badge = _icon_button("maya", "M", tr("Maya を操作対象にする", "Target Maya"))
        self.maya_badge.setObjectName("mfmDccBadge")
        self.switch = DccSwitch(dcc)
        self.blender_badge = _icon_button("blender", "b",
                                          tr("Blender を操作対象にする", "Target Blender"))
        self.blender_badge.setObjectName("mfmDccBadge")
        self.blender_ver = _PopupCombo()
        self.blender_ver.setFixedWidth(136)
        self.blender_ver.setToolTip(tr("起動する Blender のバージョン", "Blender version to launch"))
        # r119: Maya の起動設定（バージョン＋引数＋表示名）をここから開く。
        # 設定ダイアログの奥に埋めず、スイッチのすぐ右に置く（ユーザー指示）。
        self.maya_setup_btn = _icon_button(
            "note", "🗒",
            tr("Maya の起動設定（バージョン・引数・表示名）",
               "Maya launch settings (version, arguments, display name)"))
        self.maya_setup_btn.setObjectName("mfmDccBadge")
        self.maya_setup_btn.clicked.connect(self.maya_setup_requested.emit)
        g.addWidget(self.maya_ver, 0, 0)
        g.addWidget(self.maya_badge, 0, 1, Qt.AlignCenter)
        g.addWidget(self.switch, 0, 2, Qt.AlignCenter)
        g.addWidget(self.maya_setup_btn, 0, 3, Qt.AlignCenter)
        g.addWidget(self.blender_badge, 0, 4, Qt.AlignCenter)
        g.addWidget(self.blender_ver, 0, 5)

        # 下段
        self.maya_conn = self._conn_combo(tr("接続中の Maya", "Connected Maya"))
        self.refresh_btn = _icon_button("refresh", "⟳",
                                        tr("起動済みの Maya / Blender を再スキャン",
                                           "Rescan running Maya / Blender"))
        self.launch_btn = QPushButton()
        self.launch_btn.setObjectName("mfmLaunchCta")
        lic = header_icon("launch")
        if not lic.isNull():
            self.launch_btn.setIcon(lic)
            self.launch_btn.setIconSize(QSize(18, 18))
        else:
            self.launch_btn.setText("▶")
        self.launch_btn.setFixedSize(46, 22)
        self.launch_btn.setCursor(Qt.PointingHandCursor)
        self.front_btn = _icon_button("front", "🗖",
                                      tr("接続中の DCC を最前面に表示",
                                         "Bring the connected DCC to front"))
        self.blender_conn = self._conn_combo(tr("接続中の Blender", "Connected Blender"))
        g.addWidget(self.maya_conn, 1, 0)
        g.addWidget(self.refresh_btn, 1, 1, Qt.AlignCenter)
        g.addWidget(self.launch_btn, 1, 2, Qt.AlignCenter)
        g.addWidget(self.front_btn, 1, 3, Qt.AlignCenter)
        # r119: 上段が 1 列増えた（起動設定アイコン）ので、下段の Blender 側を
        # 2 列ぶち抜きにして «Blender バッジ＋バージョン» の下に揃える
        g.addWidget(self.blender_conn, 1, 4, 1, 2)

        # 配線
        self.switch.changed.connect(self._on_switch)
        self.maya_badge.clicked.connect(lambda _c=False: self.switch.set_value("maya"))
        self.blender_badge.clicked.connect(lambda _c=False: self.switch.set_value("blender"))
        self.launch_btn.clicked.connect(lambda _c=False: self.launch_requested.emit())
        self.refresh_btn.clicked.connect(lambda _c=False: self.refresh_requested.emit())
        self.front_btn.clicked.connect(lambda _c=False: self.front_requested.emit())
        self._apply_active()

    @staticmethod
    def _conn_combo(tip: str) -> QComboBox:
        c = _PopupCombo()
        # 本体は固定幅（ヘッダ幅の暴発防止。想定外の長文はツールチップで読む）。
        # 展開リストは _PopupCombo が文字幅・項目数に合わせて広げる（r71）
        c.setFixedWidth(136 + 60)
        c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        c.setMinimumContentsLength(20)
        c.setMaxVisibleItems(12)
        c.setToolTip(tip)
        return c

    def set_app_icons(self, maya_exe=None, blender_exe=None):
        """インストール済み DCC の実行ファイルから正式アイコンを取り、バッジへ。
        見つからなければ文字バッジ（M / b）のまま。"""
        for btn, exe, stem in ((self.maya_badge, maya_exe, "app_maya"),
                               (self.blender_badge, blender_exe, "app_blender")):
            ic = app_icon_for(exe)
            if ic.isNull():
                # r79: exe からアイコンが取れない場合（Store 版 Blender は ACL と
                # シェル拡張の問題で不可）は、ユーザーが用意した画像
                # resources/icons/app_<dcc>.png を使う（ユーザー提供のロゴ）。
                p = os.path.join(_ICON_DIR, stem + ".png")
                if os.path.isfile(p):
                    ic = QIcon(p)
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(18, 18))
                btn.setText("")

    def dcc(self) -> str:
        return self.switch.value()

    def set_dcc(self, v: str):
        self.switch.set_value(v, emit=False)
        self._apply_active()

    def _on_switch(self, v: str):
        self._apply_active()
        self.dcc_changed.emit(v)

    def _apply_active(self):
        """選択中の側を強調（バッジの checked 状態＝QSS で明るく）。"""
        on_maya = self.switch.value() == "maya"
        for b, active in ((self.maya_badge, on_maya), (self.blender_badge, not on_maya)):
            b.setProperty("active", "1" if active else "0")
            b.style().unpolish(b)
            b.style().polish(b)
        self.launch_btn.setToolTip(
            tr("%s を起動（連携ポート付き）", "Launch %s (with bridge port)")
            % ("Maya" if on_maya else "Blender"))


class ActionBlock(QWidget):
    """右端: クリック動作 / D&D動作（アイコン＋コンボを2段重ね）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mfmActionBlock")
        self.setAttribute(Qt.WA_StyledBackground, True)
        g = QGridLayout(self)
        g.setContentsMargins(6, 2, 8, 2)
        g.setHorizontalSpacing(4)
        g.setVerticalSpacing(3)
        items = [tr("開く", "Open"), tr("インポート", "Import"),
                 tr("リファレンス", "Reference"), tr("何もしない (None)", "None")]
        self.click_icon = _icon_label("click", "🖱", tr("クリック動作（ファイルをクリックした時に DCC で行う操作）",
                                                     "Click action (what the DCC does when a file is clicked)"))
        self.click_combo = _PopupCombo()
        self.click_combo.addItems(items)
        self.click_combo.setToolTip(self.click_icon.toolTip())
        self.dnd_icon = _icon_label("dnd", "⇢", tr("D&D動作（ファイルを DCC のウィンドウへドロップした時の操作）",
                                                 "Drag & drop action (when files are dropped onto a DCC window)"))
        self.dnd_combo = _PopupCombo()
        self.dnd_combo.addItems(items)
        self.dnd_combo.setToolTip(self.dnd_icon.toolTip())
        for c in (self.click_combo, self.dnd_combo):
            c.setFixedWidth(150)      # 「何もしない (None)」が見切れない幅（r69）
        g.addWidget(self.click_icon, 0, 0, Qt.AlignCenter)
        g.addWidget(self.click_combo, 0, 1)
        g.addWidget(self.dnd_icon, 1, 0, Qt.AlignCenter)
        g.addWidget(self.dnd_combo, 1, 1)


def _icon_label(stem: str, fallback_text: str, tip: str, size: int = 20) -> QLabel:
    lab = QLabel()
    ic = header_icon(stem)
    if not ic.isNull():
        lab.setPixmap(ic.pixmap(size - 4, size - 4))
    else:
        lab.setText(fallback_text)
    lab.setFixedSize(size, size)
    lab.setAlignment(Qt.AlignCenter)
    lab.setToolTip(tip)
    return lab
