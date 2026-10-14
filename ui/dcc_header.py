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
    Qt, Signal, QWidget, QGridLayout, QHBoxLayout, QLabel, QComboBox,
    QToolButton, QPushButton, QSize, QIcon, QPainter, QColor, QSizePolicy,
    QRect, QPixmap, QPen,
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


# ---------------------------------------------------------------------------
# DCC のアプリアイコン（右クリックメニュー等から使う共有の入口）— r125
# ---------------------------------------------------------------------------
# 右クリックメニューは «Maya で開く» のようにアプリ名を書いていたが、5 項目とも
# 同じ語が並んで読みにくかった（ユーザー指示 2026-10-06:「Maya という表記を
# なくし、アイコンで示して、テキストは端的な単語に」）。アプリの区別は
# アイコンだけが担うので、«必ず何かが出る» ことが要件になる。順に:
#   1) インストール済み exe に埋め込まれた正式アイコン（ヘッダのバッジと同じ）
#   2) resources/icons/app_<dcc>.png（ユーザーが置いた画像）
#   3) 本ツールで描く文字バッジ（M / B）… ロゴの再現はしない
_DCC_EXE = {}            # "maya" / "blender" -> exe パス
_DCC_ICON_CACHE = {}

# 3) のバッジ色。ロゴではなく «そのアプリを指す印» として使う控えめな色。
_DCC_BADGE = {"maya": ("#1E8E8A", "M"), "blender": ("#E87D0D", "B")}


def set_dcc_exe_paths(maya_exe=None, blender_exe=None):
    """インストール済み DCC の exe を覚える（アイコン取得に使う）。"""
    changed = False
    for dcc, exe in (("maya", maya_exe), ("blender", blender_exe)):
        if _DCC_EXE.get(dcc) != exe:
            _DCC_EXE[dcc] = exe
            changed = True
    if changed:
        _DCC_ICON_CACHE.clear()


def _letter_badge(dcc: str) -> QIcon:
    color, letter = _DCC_BADGE.get(dcc, ("#808080", "?"))
    try:
        from core.compat import QPixmap
        pm = QPixmap(18, 18)
        pm.fill(QColor(0, 0, 0, 0))
        pt = QPainter(pm)
        try:
            pt.setRenderHint(QPainter.Antialiasing, True)
            pt.setPen(Qt.NoPen)
            pt.setBrush(QColor(color))
            pt.drawRoundedRect(0, 0, 18, 18, 4, 4)
            f = pt.font()
            f.setBold(True)
            f.setPixelSize(12)
            pt.setFont(f)
            pt.setPen(QColor("#ffffff"))
            pt.drawText(pm.rect(), Qt.AlignCenter, letter)
        finally:
            pt.end()
        return QIcon(pm)
    except Exception:
        return QIcon()


def dcc_app_icon(dcc: str) -> QIcon:
    """その DCC を表すアイコン。**必ず空でないものを返す**（上記 1→2→3）。"""
    dcc = "blender" if dcc == "blender" else "maya"
    ic = _DCC_ICON_CACHE.get(dcc)
    if ic is not None:
        return ic
    ic = app_icon_for(_DCC_EXE.get(dcc))
    if ic.isNull():
        p = os.path.join(_ICON_DIR, "app_%s.png" % dcc)
        if os.path.isfile(p):
            ic = QIcon(p)
    if ic.isNull():
        ic = _letter_badge(dcc)
    _DCC_ICON_CACHE[dcc] = ic
    return ic


# ---------------------------------------------------------------------------
# メニューのアイコン列を揃える（r142）
# ---------------------------------------------------------------------------
# ユーザー指摘 2026-10-08:
#   「この maya のアイコンの場所のルールだとすると、Perforce のロゴも
#     Maya のロゴと同じ列にあるのが正しいと思います。」
#
# 原因: DCC の項目だけが setIcon() で «本物のアイコン列» を使い、
# 残りは絵文字を «テキストの頭» に置いていた。QMenu はアイコン列を
# 常に確保するので、絵文字はその分だけ右へずれて並んでいた。
# 全部を setIcon() に揃えれば列が一本になる。

_GLYPH_ICON_CACHE = {}


def glyph_icon(ch: str, size: int = 18) -> QIcon:
    """文字（絵文字含む）をアイコンにする。色はテーマの本文色。"""
    key = (ch, size)
    hit = _GLYPH_ICON_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        from core.compat import QPixmap
        from core.theme_engine import qss_vars
        color = qss_vars().get("on_surface", "#dddddd")
        pm = QPixmap(size, size)
        pm.fill(QColor(0, 0, 0, 0))
        pt = QPainter(pm)
        try:
            pt.setRenderHint(QPainter.Antialiasing, True)
            f = pt.font()
            f.setPixelSize(max(10, int(size * 0.8)))
            pt.setFont(f)
            pt.setPen(QColor(color))
            pt.drawText(pm.rect(), Qt.AlignCenter, ch)
        finally:
            pt.end()
        ic = QIcon(pm)
    except Exception:
        ic = QIcon()
    _GLYPH_ICON_CACHE[key] = ic
    return ic


# 外部サービス連携の exe（アイコンの出所）。detect() が見つけたものを入れる。
_INTEG_EXE = {}
_INTEG_ICON_CACHE = {}
# exe が無いときの代替グリフ（ロゴの再現はしない）
_INTEG_GLYPH = {"p4": "\u2387", "git": "\u2387", "svn": "\u2387",
                "cloud": "\u2601"}


def set_integration_exe(key: str, exe) -> None:
    if exe and _INTEG_EXE.get(key) != exe:
        _INTEG_EXE[key] = exe
        _INTEG_ICON_CACHE.pop(key, None)


def integration_icon(key: str) -> QIcon:
    """連携先を表すアイコン。**必ず空でないものを返す**。

    Maya / Blender と同じ仕組みで、まずインストール済み exe（Perforce なら
    p4v.exe）に埋め込まれた正式アイコンを使う。無ければ文字へ落とす。"""
    ic = _INTEG_ICON_CACHE.get(key)
    if ic is not None:
        return ic
    ic = app_icon_for(_INTEG_EXE.get(key))
    if ic.isNull():
        p = os.path.join(_ICON_DIR, "app_%s.png" % key)
        if os.path.isfile(p):
            ic = QIcon(p)
    if ic.isNull():
        ic = glyph_icon(_INTEG_GLYPH.get(key, "\u2387"))
    _INTEG_ICON_CACHE[key] = ic
    return ic


class PrefsBlock(QWidget):
    """表示言語とテーマのプルダウン（r134）。

    メニューバーの **真下の空き** に置く（ユーザー指示 2026-10-08）。
    設定ダイアログにも同じ項目があるが、書き込む設定キーは同じなので
    どちらから変えても矛盾しない。"""

    language_changed = Signal(str)     # "auto" / "ja" / "en"
    theme_changed = Signal(str)        # "system" / "light" / "dark"

    LANGS = ("auto", "ja", "en")
    THEMES = ("system", "light", "dark")

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 2)
        lay.setSpacing(6)
        self.lang = QComboBox(self)
        self.lang.addItems([tr("システム", "System"), "日本語", "English"])
        self.lang.setToolTip(tr("表示言語", "UI language"))
        self.theme = QComboBox(self)
        self.theme.addItems([tr("システム", "System"),
                             tr("ライト", "Light"), tr("ダーク", "Dark")])
        self.theme.setToolTip(tr("テーマ", "Theme"))
        for cap, cb in ((tr("言語", "Lang"), self.lang),
                        (tr("テーマ", "Theme"), self.theme)):
            lb = QLabel(cap, self)
            lb.setObjectName("mfmPrefsCaption")
            lay.addWidget(lb, 0)
            cb.setMinimumWidth(92)
            lay.addWidget(cb, 0)
        lay.addStretch(1)
        try:
            from core.theme_engine import qss_vars
            v = qss_vars()
            self.setStyleSheet(
                "#mfmPrefsCaption{color:%(on_surface_dim)s;"
                "font-size:%(label_px)spx;background:transparent;}"
                "QComboBox{font-size:%(label_px)spx;min-height:20px;}" % v)
        except Exception:
            pass
        self.lang.currentIndexChanged.connect(
            lambda i: self.language_changed.emit(
                self.LANGS[i] if 0 <= i < len(self.LANGS) else "auto"))
        self.theme.currentIndexChanged.connect(
            lambda i: self.theme_changed.emit(
                self.THEMES[i] if 0 <= i < len(self.THEMES) else "system"))

    def set_values(self, language: str, theme: str):
        """シグナルを出さずに表示だけ合わせる（起動時・設定画面からの反映）。"""
        for cb, vals, cur in ((self.lang, self.LANGS, language),
                              (self.theme, self.THEMES, theme)):
            try:
                i = vals.index(cur)
            except ValueError:
                i = 0
            cb.blockSignals(True)
            cb.setCurrentIndex(i)
            cb.blockSignals(False)


def _lead_icon(kind: str, color: str, size: int = 16) -> QIcon:
    """ヘッダ左端のアイコンを描く（r145）。外部ファイルに依存しない。

    kind="pin"     … 画鋲（最前面表示）
    kind="compact" … 大きい枠の右下に小さい枠（コンパクトへ畳む）
    kind="expand"  … 小さい枠から外へ広がる矢印（通常へ戻す）
    """
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    pt = QPainter(pm)
    try:
        c = QColor(color)
        pen = QPen(c)
        pen.setWidth(1)
        pt.setPen(pen)
        pt.setRenderHint(QPainter.Antialiasing, kind == "pin")
        u = size / 16.0

        def R(x, y, w, h):
            return QRect(int(x * u), int(y * u), int(w * u), int(h * u))

        if kind == "pin":
            pt.setBrush(c)
            pt.drawEllipse(R(4, 2, 8, 8))          # 頭
            pt.drawRect(R(7, 9, 2, 5))             # 針
        elif kind == "compact":
            pt.setBrush(QColor(0, 0, 0, 0))
            pt.drawRect(R(1, 1, 14, 14))           # 今の窓
            pt.setBrush(c)
            pt.drawRect(R(8, 8, 6, 6))             # 畳んだ先
        elif kind.startswith("dir:"):
            # r145: コンパクトを «どちら向きに開くか»。
            # 小さな点（パック）と、そこから伸びる枠（ウィンドウ）で示す。
            d = kind.split(":", 1)[1]
            right = d in ("dr", "ur")
            down = d in ("dr", "dl")
            wx = 5 if right else 1
            wy = 5 if down else 1
            pt.setBrush(QColor(0, 0, 0, 0))
            pt.drawRect(R(wx, wy, 10, 10))         # 広がる先
            pt.setBrush(c)
            px = 4 if right else 11
            py = 4 if down else 11
            pt.drawRect(R(px - 2, py - 2, 4, 4))   # パック
        else:                                      # expand
            pt.setBrush(c)
            pt.drawRect(R(1, 9, 6, 6))             # 今の小窓
            pt.setBrush(QColor(0, 0, 0, 0))
            pt.drawRect(R(5, 1, 10, 10))           # 戻る先
    finally:
        pt.end()
    return QIcon(pm)


class LeadBlock(QWidget):
    """メニューバーの «左隣» に置く 2 つのトグル（r145、ユーザー指示 2026-10-09）。

    - 最前面表示: すべてのウィンドウの上に出す／やめる
    - コンパクトモード: Maya の上に小さく置いて、マウスオーバーで開く

    ここは «状態を持たない» 見た目だけの部品。実際の切り替えは MainWindow 側。
    """

    always_on_top_toggled = Signal(bool)
    compact_toggled = Signal(bool)
    open_dir_changed = Signal(str)

    # 押すたび 右下 → 左下 → 左上 → 右上 → 右下 …（指示 2026-10-09）
    DIRS = ("dr", "dl", "ul", "ur")
    DIR_LABELS = {"dr": ("右下", "down-right"), "dl": ("左下", "down-left"),
                  "ul": ("左上", "up-left"), "ur": ("右上", "up-right")}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mfmLeadBlock")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 2, 0)
        lay.setSpacing(2)
        try:
            from core.theme_engine import qss_vars
            col = qss_vars().get("on_surface_dim", "#9aa0b4")
        except Exception:
            col = "#9aa0b4"
        self.top_btn = QToolButton(self)
        self.top_btn.setObjectName("mfmLeadToggle")
        self.top_btn.setCheckable(True)
        self.top_btn.setIcon(_lead_icon("pin", col))
        self.top_btn.setIconSize(QSize(16, 16))
        self.top_btn.setToolTip(tr("最前面に表示（すべてのウィンドウの上）",
                                   "Always on top (above every window)"))
        self.top_btn.toggled.connect(self.always_on_top_toggled.emit)

        self.compact_btn = QToolButton(self)
        self.compact_btn.setObjectName("mfmLeadToggle")
        # r145: 押しっぱなしのトグル。今がコンパクトかどうかが «光っているか»
        # で分かる（ユーザー指摘 2026-10-09: 今の状態が分からない）。
        self.compact_btn.setCheckable(True)
        self.compact_btn.setIcon(_lead_icon("compact", col))
        self.compact_btn.setIconSize(QSize(16, 16))
        self.compact_btn.setToolTip(tr(
            "コンパクトモード（小さく畳んで、マウスを乗せると開く）",
            "Compact mode (shrink to a puck; hover to open)"))
        self.compact_btn.toggled.connect(self.compact_toggled.emit)

        self._dir = "dr"
        self._dir_col = col
        self.dir_btn = QToolButton(self)
        self.dir_btn.setObjectName("mfmLeadToggle")
        self.dir_btn.setIconSize(QSize(16, 16))
        self.dir_btn.clicked.connect(lambda _c=False: self._cycle_dir())
        self._apply_dir_look()

        for b in (self.top_btn, self.compact_btn, self.dir_btn):
            b.setFixedSize(24, 22)
            b.setCursor(Qt.PointingHandCursor)
            lay.addWidget(b, 0)
        try:
            from core.theme_engine import qss_vars
            self.setStyleSheet(
                "#mfmLeadBlock{background:transparent;}"
                "#mfmLeadToggle{background:transparent;border:1px solid "
                "transparent;border-radius:5px;}"
                "#mfmLeadToggle:hover{background:%(state_hover)s;}"
                "#mfmLeadToggle:checked{background:%(cta_tint)s;"
                "border-color:%(primary)s;}" % qss_vars())
        except Exception:
            pass

    def set_always_on_top(self, on: bool):
        """シグナルを出さずに見た目だけ合わせる。"""
        self.top_btn.blockSignals(True)
        self.top_btn.setChecked(bool(on))
        self.top_btn.blockSignals(False)

    def set_compact(self, on: bool):
        """シグナルを出さずに見た目だけ合わせる（r145）。"""
        self.compact_btn.blockSignals(True)
        self.compact_btn.setChecked(bool(on))
        self.compact_btn.blockSignals(False)

    # ── 開く向き（r145）────────────────────────────────────────────
    def open_dir(self) -> str:
        return self._dir

    def set_open_dir(self, d: str, notify: bool = False):
        self._dir = d if d in self.DIRS else "dr"
        self._apply_dir_look()
        if notify:
            self.open_dir_changed.emit(self._dir)

    def _cycle_dir(self):
        i = self.DIRS.index(self._dir) if self._dir in self.DIRS else 0
        self.set_open_dir(self.DIRS[(i + 1) % len(self.DIRS)], notify=True)

    def _apply_dir_look(self):
        self.dir_btn.setIcon(_lead_icon("dir:" + self._dir, self._dir_col))
        ja, en = self.DIR_LABELS.get(self._dir, ("右下", "down-right"))
        self.dir_btn.setToolTip(tr(
            "コンパクトから開く向き: %s（押すと右下→左下→左上→右上）" % ja,
            "Compact opens toward: %s (click to cycle)" % en))


class _HeaderRow(QWidget):
    """ヘッダ行の手動レイアウト（r69）: メニューは左端、動作ブロックは右端、
    DCC ブロックは «起動ボタンがウィンドウの中央に来る» 位置に置く。
    QHBoxLayout の伸縮では左右の幅差で中央がずれるため自前で配置する。

    r134: メニューバーの真下は元々空いているので、そこに言語・テーマの
    プルダウンを置く。DCC ブロックは 2 段なので全体の高さは変わらない。"""

    def __init__(self, menubar, dcc_block, action_block, parent=None,
                 prefs_block=None, lead_block=None):
        super().__init__(parent)
        self._mb, self._dcc, self._act = menubar, dcc_block, action_block
        self._prefs = prefs_block
        self._lead = lead_block          # r145: メニューバーの左隣
        for w in (menubar, dcc_block, action_block, prefs_block, lead_block):
            if w is None:
                continue
            w.setParent(self)
            w.show()

    def _lead_w(self):
        return self._lead.sizeHint().width() if self._lead is not None else 0

    def sizeHint(self):
        left = self._mb.sizeHint().height()
        if self._prefs is not None:
            left += self._prefs.sizeHint().height()
        h = max(left, self._dcc.sizeHint().height(),
                self._act.sizeHint().height())
        w = self._lead_w() + self._mb.sizeHint().width()
        if self._prefs is not None:
            w = max(w, self._prefs.sizeHint().width())
        return QSize(w + self._dcc.sizeHint().width()
                     + self._act.sizeHint().width() + 24, h)

    def _left_width(self, minimum=False):
        """左ブロック（メニューバー／言語・テーマ）の幅。"""
        f = (lambda w: w.minimumSizeHint().width()) if minimum \
            else (lambda w: w.sizeHint().width())
        w = self._lead_w() + f(self._mb)
        if self._prefs is not None:
            w = max(w, f(self._prefs))
        return w

    def minimumSizeHint(self):
        """r144: 幅まで «自然な大きさ» を要求すると、ウィンドウがこれ以上
        細くできなくなる（ユーザー指摘 2026-10-08「もう半分くらいは細く
        なってもUI的には大丈夫では」）。高さは必要なだけ要求し、幅は
        各ブロックが «詰まった状態» で並ぶ分だけにする。
        狭い時は DCC ブロックを縮めて収める（_relayout）。"""
        s = self.sizeHint()
        w = (self._left_width(minimum=True)
             + self._dcc.minimumSizeHint().width()
             + self._act.minimumSizeHint().width() + 24)
        return QSize(max(320, min(w, s.width())), s.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def showEvent(self, e):
        super().showEvent(e)
        self._relayout()

    def _relayout(self):
        W, H = self.width(), self.height()
        mbs, ds, as_ = self._mb.sizeHint(), self._dcc.sizeHint(), self._act.sizeHint()
        lw = self._lead_w()
        if self._lead is not None:
            ls = self._lead.sizeHint()
            # メニューバーと «同じ高さの中央» に置く（左端）
            self._lead.setGeometry(0, max(0, (mbs.height() - ls.height()) // 2),
                                   ls.width(), ls.height())
        self._mb.setGeometry(lw, 0, mbs.width(), mbs.height())
        if self._prefs is not None:
            ps = self._prefs.sizeHint()
            self._prefs.setGeometry(0, mbs.height(), ps.width(), ps.height())
        self._act.setGeometry(W - as_.width(), max(0, (H - as_.height()) // 2),
                              as_.width(), as_.height())
        # r144: 幅が足りない時は DCC ブロックを縮めて収める（重ねない）。
        left_min = self._left_width(minimum=True)
        avail = W - left_min - as_.width() - 12
        dw = max(self._dcc.minimumSizeHint().width(), min(ds.width(), avail))
        ds = QSize(dw, ds.height())
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
        left_w = self._left_width()
        # r146: 隙間を 6 → 2。中央寄せの方が «見た目の基準» として大事で、
        # メニューバーとの間が 2px 空いていれば重なりは起きない。
        lo = min(left_w + 2, max(0, W - as_.width() - ds.width() - 6))
        x = max(lo, min(x, W - as_.width() - ds.width() - 6))
        x = max(0, x)
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
        # r146: 左端に起動設定アイコンが 1 つ増えたぶん、左の詰めを切り詰める。
        # ここが緩いと «起動ボタンがウィンドウ中央» の配置（_HeaderRow）が
        # 左の下限に当たって中央からずれる（回帰スイート 2026-10-13: 8px）。
        g.setContentsMargins(2, 2, 6, 2)
        g.setHorizontalSpacing(5)
        g.setVerticalSpacing(3)

        # 上段
        self.maya_ver = _PopupCombo()
        _elastic_width(self.maya_ver, 92, 136)
        self.maya_ver.setToolTip(tr("起動する Maya のバージョン", "Maya version to launch"))
        self.maya_badge = _icon_button("maya", "M", tr("Maya を操作対象にする", "Target Maya"))
        self.maya_badge.setObjectName("mfmDccBadge")
        self.switch = DccSwitch(dcc)
        self.blender_badge = _icon_button("blender", "b",
                                          tr("Blender を操作対象にする", "Target Blender"))
        self.blender_badge.setObjectName("mfmDccBadge")
        self.blender_ver = _PopupCombo()
        _elastic_width(self.blender_ver, 92, 136)
        self.blender_ver.setToolTip(tr("起動する Blender のバージョン", "Blender version to launch"))
        # Maya の起動設定（バージョン＋引数＋表示名）をここから開く。
        # r119 はスイッチのすぐ右に置いていたが、r146 で «Maya バージョンの
        # 左» へ移す（ユーザー指示 2026-10-13）。中央の [M][切替][b] は
        # «どちらを操作するか» のかたまりで、起動設定は Maya 側の持ち物。
        # かたまりの中に混ぜると、どちらに属する設定なのか読み取れない。
        self.maya_setup_btn = _icon_button(
            "note", "🗒",
            tr("Maya の起動設定（バージョン・引数・表示名）",
               "Maya launch settings (version, arguments, display name)"))
        self.maya_setup_btn.setObjectName("mfmDccBadge")
        self.maya_setup_btn.clicked.connect(self.maya_setup_requested.emit)
        g.addWidget(self.maya_setup_btn, 0, 0, Qt.AlignCenter)
        g.addWidget(self.maya_ver, 0, 1)
        g.addWidget(self.maya_badge, 0, 2, Qt.AlignCenter)
        g.addWidget(self.switch, 0, 3, Qt.AlignCenter)
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
        # r146: 上段の左端に起動設定アイコンが来たので、下段の Maya 側を
        # 2 列ぶち抜きにして «起動設定＋Maya バージョン» の下に揃える。
        # 中央の 3 つ（更新・起動・最前面）はその右に 1 列ずつ並ぶ。
        g.addWidget(self.maya_conn, 1, 0, 1, 2)
        g.addWidget(self.refresh_btn, 1, 2, Qt.AlignCenter)
        g.addWidget(self.launch_btn, 1, 3, Qt.AlignCenter)
        g.addWidget(self.front_btn, 1, 4, Qt.AlignCenter)
        g.addWidget(self.blender_conn, 1, 5)

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
        # 本体は上限つきの可変幅（ヘッダ幅の暴発防止。想定外の長文は
        # ツールチップで読む）。r144: 狭い時は縮んでウィンドウを細くできる。
        # 展開リストは _PopupCombo が文字幅・項目数に合わせて広げる（r71）
        _elastic_width(c, 110, 136 + 60)
        c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        c.setMinimumContentsLength(8)
        c.setMaxVisibleItems(12)
        c.setToolTip(tip)
        return c

    def set_app_icons(self, maya_exe=None, blender_exe=None):
        """インストール済み DCC の実行ファイルから正式アイコンを取り、バッジへ。
        見つからなければ文字バッジ（M / b）のまま。"""
        # r125: 右クリックメニューも同じアイコンを使うので、ここで共有しておく。
        set_dcc_exe_paths(maya_exe, blender_exe)
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
            # 広い時は「何もしない (None)」が見切れない 150px（r69）、
            # 狭い時は 96px まで詰まる（r144）
            _elastic_width(c, 96, 150)
        g.addWidget(self.click_icon, 0, 0, Qt.AlignCenter)
        g.addWidget(self.click_combo, 0, 1)
        g.addWidget(self.dnd_icon, 1, 0, Qt.AlignCenter)
        g.addWidget(self.dnd_combo, 1, 1)


def _elastic_width(w, minimum: int, maximum: int):
    """幅を «詰められる» ようにする（r144）。

    固定幅だとヘッダ全体がそれ以上細くならず、ウィンドウ幅の下限になる
    （ユーザー指摘 2026-10-08「もう半分くらいは細くなっても大丈夫では」）。
    広い時は従来の幅、狭い時は minimum まで縮む。中身はツールチップで読める。"""
    w.setMinimumWidth(minimum)
    w.setMaximumWidth(maximum)
    try:
        w.setSizePolicy(QSizePolicy.Preferred, w.sizePolicy().verticalPolicy())
    except Exception:
        pass
    return w


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
