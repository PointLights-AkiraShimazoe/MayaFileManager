"""
Theme engine — design-token driven QSS generator.

ref → sys の2層トークンを config/design_tokens.json から読み込み、
QSS と QPalette に展開する（Mercury 準拠、r82）。

設計方針:
- 色・角丸・密度の値は **絶対にこのファイルへ直書きしない**（トークンJSONが単一の真実）
- ライト/ダーク切替は sys レイヤーの参照先差し替えのみで実現
- state layer (hover 8% / pressed 12%) を全ウィジェットに一貫適用

Mercury の規則（design_tokens.json の _mercury も参照）:
- 有彩色は Cobalt 1色だけ。意味を持つ信号色（エラー/同期状態）は例外
- 影は使わない。面の段差は明度だけで作る
- ボタン・入力・ナビはピル形状、カードは 12px
- font-weight は 500 まで（600/700 は使わない）
- 本文に純白は使わない（ダークは Ivory #ededf3）

ウィジェット個別の setStyleSheet からは qss_vars() を使う（色のベタ書き禁止）:
    from core.theme_engine import qss_vars
    self.setStyleSheet("QLabel{color:%(on_surface_dim)s;}" % qss_vars())

Usage:
    from core.theme_engine import apply_theme
    apply_theme(app, mode="dark")   # or "light"
"""

import json
import re
from pathlib import Path

_TOKENS_FILE = Path(__file__).parent.parent / "config" / "design_tokens.json"
_REF_RE = re.compile(r"\{([a-z_.0-9]+)\}")

_cached_tokens = None
_current_mode = "dark"       # apply_theme が最後に適用したモード（qss_vars の既定）
# r120: qss_vars() の結果（モード別）。描画中に呼ばれるのでキャッシュする。
# 値は {mode: (tokens_obj, vars_dict)}。tokens_obj の同一性で作り直しを判定する。
_QSS_CACHE = {}


# ---------------------------------------------------------------------------
# Token loading / resolution
# ---------------------------------------------------------------------------

def load_tokens(force_reload=False):
    """design_tokens.json を読み込み、参照 ({ref.palette.xxx}) を解決して返す。"""
    global _cached_tokens
    if _cached_tokens is not None and not force_reload:
        return _cached_tokens

    with open(_TOKENS_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    def lookup(path):
        node = raw
        for key in path.split("."):
            node = node[key]
        return node

    def resolve(value, depth=0):
        if depth > 8:
            raise ValueError(f"Token reference too deep: {value}")
        if isinstance(value, str):
            m = _REF_RE.fullmatch(value)
            if m:
                return resolve(lookup(m.group(1)), depth + 1)
            return value
        if isinstance(value, dict):
            return {k: resolve(v, depth) for k, v in value.items()}
        return value

    _cached_tokens = resolve(raw)
    return _cached_tokens


def get_colors(mode="dark"):
    """解決済みの sys カラーロール辞書を返す。"""
    tokens = load_tokens()
    return tokens["sys"][mode]


def current_mode() -> str:
    """apply_theme が最後に適用したモード（"dark" / "light"）。"""
    return _current_mode


def set_mode(mode: str) -> str:
    """QApplication には触れずにモードだけ切り替える。
    Maya 内起動では Maya 全体の見た目を壊さないよう apply_theme を呼ばないが、
    ウィジェット個別のスタイル（qss_vars）は設定に合わせる必要があるため使う。"""
    global _current_mode
    _current_mode = mode if mode in ("dark", "light") else "dark"
    return _current_mode


def qss_vars(mode=None) -> dict:
    """ウィジェット個別の setStyleSheet 用に、色ロール＋形状・書体・密度を
    ひとまとめの dict で返す（"%(on_surface)s" 形式で差し込む）。
    アプリ全体の QSS と同じトークンを使うことで、ライト/ダークの取りこぼしを防ぐ。
    アンダースコア始まりの注釈キーは除外する。

    r120: **結果をモード別に覚える。** ここは «描画中に» 呼ばれる
    （ThumbnailDelegate.paint / StatusBadgeDelegate.paint はセル 1 つごとに
    呼ぶ）。中で矢印・チェックの PNG を作っているので 1 回 0.25ms ほど掛かり、
    セル数 × フレーム数だけ積み上がってカラムのスライドがカクついていた
    （ユーザー報告 2026-10-02）。トークンを読み直すかモードが変われば
    作り直す（load_tokens() は同じ dict を返すので «同一性» で判定できる）。
    """
    m = mode or _current_mode
    t = load_tokens()
    hit = _QSS_CACHE.get(m)
    if hit is not None and hit[0] is t:
        return dict(hit[1])        # 呼び出し側が書き換えても元を壊さない
    v = {k: val for k, val in t["sys"][m].items()
         if not k.startswith("_") and isinstance(val, str)}
    v.update({
        "r_s": t["shape"]["corner_small"],
        "r_m": t["shape"]["corner_medium"],
        "r_l": t["shape"]["corner_large"],
        "r_pill": t["shape"]["corner_pill"],
        "font": t["type"]["family"],
        "title_px": t["type"]["title_px"],
        "body_px": t["type"]["body_px"],
        "label_px": t["type"]["label_px"],
        "caption_px": t["type"]["caption_px"],
        "w_normal": t["type"]["weight_normal"],
        "w_strong": t["type"]["weight_strong"],
        "row_h": t["density"]["list_row_h"],
        "input_h": t["density"]["input_h"],
        "button_h": t["density"]["button_h"],
    })
    # r119: 自前で描く印（矢印・チェック）のパスもここから引けるようにする。
    # ウィジェット個別の setStyleSheet からも同じ画像を使えるようにするため。
    v.update({
        "arrow_png": _arrow_png(v["on_surface_variant"]),
        "arrow_png_dim": _arrow_png(v["on_surface_dim"]),
        "arrow_png_up": _arrow_png(v["on_surface_variant"], "up"),
        "arrow_png_up_dim": _arrow_png(v["on_surface_dim"], "up"),
        "check_png": _check_png(v["on_primary"]),
        "check_png_dim": _check_png(v["on_surface_dim"]),
        "dash_png": _check_png(v["on_primary"], "dash"),
    })
    _QSS_CACHE[m] = (t, v)
    return dict(v)


def area_accents(mode=None):
    """エリア（分割ペイン）ごとの識別色 [(面, 文字, 罫線), ...] をトークンから返す。"""
    t = load_tokens()
    m = mode or _current_mode
    return [tuple(x) for x in t["area_accents"][m]]


# ---------------------------------------------------------------------------
# コンボボックスの ▼（r86）
# ---------------------------------------------------------------------------

def _arrow_png(color: str, direction: str = "down") -> str:
    """三角（▼ / ▲）の PNG を作ってパスを返す（色・向きごとにキャッシュ）。

    QSS は subcontrol に文字を置けず、border で三角を作る CSS の小技も
    Qt では «小さな四角» にしか描かれない（実機確認）。確実に出すには
    画像を渡すしかないので、テーマ色に合わせた PNG をその場で生成する。

    r119: QSpinBox の上下ボタンにも使う。QSS で QSpinBox に背景と枠を
    当てると、スタイル既定の矢印が «地と同化して見えなくなる» ため
    （ユーザー報告 2026-10-01）。上向きもここで作る。"""
    try:
        from core.compat import QPixmap, QPainter, QColor, QPoint, Qt, QtGui
        QPolygon = QtGui.QPolygon
    except Exception:
        return ""
    key = color.lstrip("#").lower()
    try:
        from core.settings_manager import SettingsManager
        base = SettingsManager._resolve_root()
    except Exception:
        base = Path.home() / ".maya_file_manager"
    out = Path(base) / "ui" / ("arrow_%s_%s.png" % (direction, key))
    if out.exists():
        return out.as_posix()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        w, h = 9, 6
        pm = QPixmap(w, h)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        if direction == "up":
            pts = [QPoint(0, h), QPoint(w, h), QPoint(w // 2, 0)]
        else:
            pts = [QPoint(0, 0), QPoint(w, 0), QPoint(w // 2, h)]
        p.drawPolygon(QPolygon(pts))
        p.end()
        pm.save(str(out), "PNG")
        return out.as_posix()
    except Exception:
        return ""


def _check_png(color: str, glyph: str = "check") -> str:
    """チェックマーク／横棒の PNG（色ごとにキャッシュ）。

    r119: QCheckBox に QSS で枠を当てるとスタイル既定のチェックが
    消えるため、印も自前で用意する。"""
    try:
        from core.compat import QPixmap, QPainter, QColor, Qt, QtGui
        QPen = QtGui.QPen
    except Exception:
        return ""
    key = color.lstrip("#").lower()
    try:
        from core.settings_manager import SettingsManager
        base = SettingsManager._resolve_root()
    except Exception:
        base = Path.home() / ".maya_file_manager"
    out = Path(base) / "ui" / ("mark_%s_%s.png" % (glyph, key))
    if out.exists():
        return out.as_posix()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        w = h = 14
        pm = QPixmap(w, h)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(color))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        if glyph == "dash":
            p.drawLine(3, h // 2, w - 3, h // 2)
        else:
            p.drawLine(3, 7, 6, 10)
            p.drawLine(6, 10, 11, 4)
        p.end()
        pm.save(str(out), "PNG")
        return out.as_posix()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# QSS generation
# ---------------------------------------------------------------------------

def build_qss(mode="dark"):
    """トークンからアプリ全体の QSS 文字列を生成する。"""
    t = load_tokens()
    c = t["sys"][mode]
    shape = t["shape"]
    typ = t["type"]
    den = t["density"]

    v = {
        # colors
        "primary": c["primary"],
        "on_primary": c["on_primary"],
        "primary_container": c["primary_container"],
        "on_primary_container": c["on_primary_container"],
        "surface": c["surface"],
        "surface_low": c["surface_container_low"],
        "surface_mid": c["surface_container"],
        "surface_high": c["surface_container_high"],
        "on_surface": c["on_surface"],
        "on_surface_variant": c["on_surface_variant"],
        "on_surface_dim": c["on_surface_dim"],
        "outline": c["outline"],
        "outline_variant": c["outline_variant"],
        "hairline": c["hairline"],
        "hairline_strong": c["hairline_strong"],
        "fill_subtle": c["fill_subtle"],
        "fill_subtle_hover": c["fill_subtle_hover"],
        "fill_subtle_pressed": c["fill_subtle_pressed"],
        "error": c["error"],
        "selection": c["selection"],
        "on_selection": c["on_selection"],
        "hover": c["state_hover"],
        "pressed": c["state_pressed"],
        "focus": c["state_focus"],
        # hierarchy planes (r56)
        "plane_focus": c.get("plane_focus", c["surface_container_low"]),
        "plane_focus_border": c.get("plane_focus_border", c["hairline_strong"]),
        "plane_side": c.get("plane_side", c["surface"]),
        "plane_header": c.get("plane_header", c["surface"]),
        "cta_tint": c.get("cta_tint", c["primary"]),
        "cta_tint_hover": c.get("cta_tint_hover", c["primary"]),
        # shape
        "r_s": shape["corner_small"],
        "r_m": shape["corner_medium"],
        "r_l": shape["corner_large"],
        "r_pill": shape.get("corner_pill", 14),
        # type
        "font": typ["family"],
        "title_px": typ["title_px"],
        "body_px": typ["body_px"],
        "label_px": typ["label_px"],
        "w_strong": typ["weight_strong"],
        "arrow_png": _arrow_png(c["on_surface_variant"]),
        "arrow_png_dim": _arrow_png(c["on_surface_dim"]),
        "arrow_png_up": _arrow_png(c["on_surface_variant"], "up"),
        "arrow_png_up_dim": _arrow_png(c["on_surface_dim"], "up"),
        "check_png": _check_png(c["on_primary"]),
        "check_png_dim": _check_png(c["on_surface_dim"]),
        "dash_png": _check_png(c["on_primary"], "dash"),
        # density
        "row_h": den["list_row_h"],
        "tree_row_h": den["tree_row_h"],
        "toolbar_h": den["toolbar_h"],
        "input_h": den["input_h"],
        "button_h": den["button_h"],
    }

    qss = """
/* ===== generated by core/theme_engine.py — DO NOT EDIT COLORS HERE ===== */

QWidget {{
    background-color: {surface};
    color: {on_surface};
    font-family: {font};
    font-size: {body_px}px;
}}

QMainWindow, QDialog {{ background-color: {surface}; }}

/* ---------- Top app bar (toolbar) ---------- */
QToolBar {{
    background-color: {surface_low};
    border: none;
    min-height: {toolbar_h}px;
    spacing: 4px;
    padding: 2px 6px;
}}
QToolButton {{
    background: transparent;
    border: none;
    border-radius: {r_s}px;
    /* 24px 等の固定サイズ小ボタン（▲▼✕…）の内容領域を潰さないよう控えめに */
    padding: 1px 3px;
    min-width: 0px;
    color: {on_surface_variant};
}}
QToolButton:hover {{ background-color: {fill_subtle_hover}; color: {on_surface}; }}
QToolButton:pressed {{ background-color: {fill_subtle_pressed}; }}
QToolButton:checked {{
    background-color: {primary_container};
    color: {on_primary_container};
}}

/* ---------- Buttons（AuthKit: フロスト・ピル＋ヘアライン。CTAのみ violet） ---------- */
QPushButton {{
    background-color: {fill_subtle};
    color: {on_surface};
    border: 1px solid {hairline};
    border-radius: {r_pill}px;
    min-height: {button_h}px;
    padding: 2px 16px;
}}
QPushButton:hover {{ background-color: {fill_subtle_hover}; border-color: {hairline_strong}; }}
QPushButton:pressed {{ background-color: {fill_subtle_pressed}; }}
QPushButton:default {{
    background-color: {primary};
    color: {on_primary};
    border: 1px solid {primary};
    border-radius: {r_pill}px;
    font-weight: {w_strong};
}}
QPushButton:default:hover {{ background-color: {cta_tint_hover}; }}
QPushButton:disabled {{ color: {on_surface_dim}; border-color: {hairline}; }}

/* ---------- Inputs（Mercury: 入力もピル形状） ---------- */
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background-color: {fill_subtle};
    color: {on_surface};
    border: 1px solid {hairline};
    border-radius: {r_pill}px;
    min-height: {input_h}px;
    padding: 1px 8px;
    selection-background-color: {selection};
    selection-color: {on_selection};
}}
QTextEdit, QPlainTextEdit {{ border-radius: {r_m}px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus {{
    border: 1px solid {primary};
}}
/* プルダウンであることが一目で分かるように ▼ を必ず描く（r86、ユーザー指示）。
   Qt の QSS は subcontrol に文字を置けないため、border で三角形を作る。
   image:none を付けないとスタイル既定の矢印と二重になる環境がある。 */
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    width: 18px;
}}
QComboBox::down-arrow {{
    image: url("{arrow_png}");
    width: 9px;
    height: 6px;
    margin-right: 6px;
}}
QComboBox::down-arrow:disabled {{ image: url("{arrow_png_dim}"); }}
QComboBox::down-arrow:on {{ margin-top: 1px; }}
QComboBox QAbstractItemView {{
    background-color: {surface_high};
    color: {on_surface};
    border: 1px solid {hairline};
    border-radius: {r_s}px;
    padding: 3px;
    outline: none;
    selection-background-color: {selection};
    selection-color: {on_selection};
}}
/* 展開リストの1行: ヘッダの透明コンボでも潰れない高さと余白（r71） */
QComboBox QAbstractItemView::item {{
    min-height: 22px;
    padding: 2px 8px;
    color: {on_surface};
}}
QComboBox QAbstractItemView::item:hover {{ background-color: {hover}; }}
QComboBox QAbstractItemView::item:selected {{
    background-color: {selection};
    color: {on_selection};
}}

/* ---------- Item views (browser / tree / list) ---------- */
QTreeView, QListView, QColumnView, QTableView, QListWidget, QTreeWidget, QTableWidget {{
    background-color: {surface};
    alternate-background-color: {surface_low};
    border: 1px solid {hairline};
    border-radius: {r_s}px;
    outline: 0;
}}
QTreeView::item {{ min-height: {tree_row_h}px; border-radius: {r_s}px; }}
QListView::item, QTableView::item {{ min-height: {row_h}px; border-radius: {r_s}px; }}
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover,
QListWidget::item:hover, QTreeWidget::item:hover {{
    background-color: {hover};
}}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected,
QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {selection};
    color: {on_selection};
}}
QHeaderView::section {{
    background-color: {surface_low};
    color: {on_surface_variant};
    border: none;
    border-bottom: 1px solid {hairline};
    padding: 4px 8px;
    font-size: {label_px}px;
    font-weight: {w_strong};
}}

/* ---------- Docks / panels ---------- */
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{
    background-color: {surface_low};
    color: {on_surface_variant};
    padding: 5px 10px;
    font-size: {label_px}px;
    font-weight: {w_strong};
}}
QSplitter::handle {{ background-color: {hairline}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* ---------- Tabs ---------- */
QTabWidget::pane {{
    border: 1px solid {outline_variant};
    border-radius: {r_s}px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {on_surface_variant};
    padding: 6px 16px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ background-color: {hover}; }}
QTabBar::tab:selected {{
    color: {on_surface};
    border-bottom: 2px solid {on_surface};
    font-weight: {w_strong};
}}

/* ---------- Menus ---------- */
QMenuBar {{ background-color: {surface_low}; }}
QMenuBar::item {{ padding: 4px 10px; border-radius: {r_s}px; }}
QMenuBar::item:selected {{ background-color: {hover}; }}
QMenu {{
    background-color: {surface_high};
    border: 1px solid {hairline};
    border-radius: {r_m}px;
    padding: 6px;
}}
QMenu::item {{ padding: 5px 24px 5px 12px; border-radius: {r_s}px; }}
QMenu::item:selected {{ background-color: {selection}; color: {on_selection}; }}
QMenu::separator {{ height: 1px; background: {hairline}; margin: 4px 8px; }}

/* ---------- Scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{
    background: {outline};
    border-radius: 5px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::handle:hover {{ background: {on_surface_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- Status bar / tooltips ---------- */
QStatusBar {{
    background-color: {surface_low};
    color: {on_surface_variant};
    font-size: {label_px}px;
}}
QToolTip {{
    background-color: {surface_high};
    color: {on_surface};
    border: 1px solid {hairline};
    border-radius: {r_s}px;
    padding: 4px 8px;
}}

/* ---------- Misc ---------- */
QGroupBox {{
    border: 1px solid {hairline};
    border-radius: {r_m}px;
    margin-top: 10px;
    padding-top: 6px;
    font-weight: {w_strong};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {on_surface_variant};
}}
QProgressBar {{
    background-color: {surface_low};
    border: none;
    border-radius: {r_s}px;
    text-align: center;
    min-height: 6px;
}}
QProgressBar::chunk {{ background-color: {primary}; border-radius: {r_s}px; }}
/* ---------- QSpinBox の上下ボタン（r119）----------
   QSS で QSpinBox に背景・枠を当てると、スタイル既定の小さな矢印が
   地と同化して «見えない» 状態になる（ユーザー報告 2026-10-01）。
   コンボの ▼ と同じ方式で、テーマ色の PNG を明示的に置く。 */
QSpinBox, QDoubleSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    background-color: {fill_subtle};
    border: none;
    border-left: 1px solid {hairline};
    width: 18px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
    margin: 1px 1px 0 0;
    border-top-right-radius: {r_pill}px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
    margin: 0 1px 1px 0;
    border-bottom-right-radius: {r_pill}px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {fill_subtle_hover};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{arrow_png_up}"); width: 9px; height: 6px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{arrow_png}"); width: 9px; height: 6px;
}}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
QSpinBox::up-arrow:off, QDoubleSpinBox::up-arrow:off {{
    image: url("{arrow_png_up_dim}");
}}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled,
QSpinBox::down-arrow:off, QDoubleSpinBox::down-arrow:off {{
    image: url("{arrow_png_dim}");
}}

/* ---------- チェックボックス / ラジオ（r119）----------
   既定のままだと暗い面に暗い枠で «ただの文字» に見える
   （ユーザー報告 2026-10-01）。枠をはっきり描き、ON は塗りつぶす。 */
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    background-color: {fill_subtle};
    border: 1px solid {on_surface_variant};
}}
QCheckBox::indicator {{ border-radius: {r_s}px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {primary};
    background-color: {fill_subtle_hover};
}}
QCheckBox::indicator:checked {{
    background-color: {primary};
    border-color: {primary};
    image: url("{check_png}");
}}
QCheckBox::indicator:indeterminate {{
    background-color: {primary};
    border-color: {primary};
    image: url("{dash_png}");
}}
QRadioButton::indicator:checked {{
    background-color: {primary};
    border: 4px solid {fill_subtle};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {hairline};
    background-color: {surface_low};
}}
QCheckBox::indicator:checked:disabled {{ image: url("{check_png_dim}"); }}

/* =====================================================================
   視認性の階層（r56）。objectName で面を分ける:
     1) mfmBrowserPanel  … ブラウジング（主役）: 一段明るい面＋強いヘアライン枠
     2) mfmSidePanel     … ブックマーク/履歴（脇役）: 地の色・枠なし・控えめな文字
        mfmPresetBar     … プリセット行（脇役）: 同上
     3) mfmHeaderCorner / QMenuBar … ヘッダ（背景）: 最も沈めた面・薄い文字
   ===================================================================== */

/* 1) ブラウジング面 */
QWidget#mfmBrowserPanel {{
    background-color: {plane_focus};
    border: 1px solid {plane_focus_border};
    border-radius: {r_m}px;
}}
#mfmBrowserPanel QListView, #mfmBrowserPanel QColumnView,
#mfmBrowserPanel QTreeView, #mfmBrowserPanel QListWidget {{
    background-color: {plane_focus};
    color: {on_surface};
}}
QFrame#mfmBrowserToolbar {{ background-color: {plane_focus}; border: none; }}
#mfmBrowserPanel QLabel {{ background-color: transparent; }}

/* インライン名前変更のエディタ（項目の上に重ねるため «不透明» が必須。
   フロストウォッシュ（半透明）だと下の項目名が透けて二重に見える） */
QLineEdit#mfmInlineRename {{
    background-color: {surface_high};
    color: {on_surface};
    border: 1px solid {primary};
    border-radius: 4px;
    padding: 0px 4px;
    min-height: 0px;
    selection-background-color: {selection};
    selection-color: {on_selection};
}}

/* 2) サイドバー（ブックマーク/履歴）・プリセット行 */
QWidget#mfmSidePanel, QWidget#mfmPresetBar {{ background-color: {plane_side}; }}
#mfmSidePanel QTreeWidget, #mfmSidePanel QListWidget, #mfmSidePanel QTreeView {{
    background-color: transparent;
    border: none;
    color: {on_surface_variant};
    font-size: {label_px}px;
}}
#mfmSidePanel QLabel {{ color: {on_surface_dim}; font-size: {label_px}px; }}
#mfmSidePanel QToolButton {{ color: {on_surface_dim}; font-size: {label_px}px; }}
#mfmSidePanel QLineEdit {{ font-size: {label_px}px; min-height: 22px; }}

/* 3) ヘッダ（メニューバー＋右肩ウィジェット） */
QMenuBar {{
    background-color: {plane_header};
    color: {on_surface_dim};
    border-bottom: 1px solid {hairline};
}}
QMenuBar::item {{ color: {on_surface_dim}; }}
QMenuBar::item:selected {{ background-color: {hover}; color: {on_surface}; }}
QWidget#mfmHeaderCorner {{ background-color: {plane_header}; }}
/* ヘッダ中央の DCC ブロック／右端の動作ブロック（r67）: ヘッダと同じ面 */
QWidget#mfmDccBlock, QWidget#mfmActionBlock {{ background-color: {plane_header}; }}
#mfmDccBlock QComboBox, #mfmActionBlock QComboBox {{
    background-color: transparent;
    color: {on_surface_variant};
    border: 1px solid {hairline};
    font-size: {label_px}px;
    min-height: 20px;
    padding: 0px 6px;
}}
#mfmDccBlock QComboBox:hover, #mfmActionBlock QComboBox:hover {{ background-color: {fill_subtle}; }}
#mfmDccBlock QToolButton, #mfmActionBlock QLabel {{
    color: {on_surface_dim};
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 11px;
}}
#mfmDccBlock QToolButton:hover {{ background-color: {fill_subtle_hover}; }}
/* M / b バッジ: 選択中は明るく（violet ティント＋枠）、非選択は沈める */
QToolButton#mfmDccBadge {{ font-weight: {w_strong}; color: {on_surface_dim}; }}
QToolButton#mfmDccBadge[active="1"] {{
    color: {on_primary_container};
    background-color: {cta_tint};
    border: 1px solid {primary};
}}
#mfmHeaderCorner QLabel {{ color: {on_surface_dim}; font-size: {label_px}px; }}
#mfmHeaderCorner QComboBox {{
    background-color: transparent;
    color: {on_surface_variant};
    border: 1px solid {hairline};
    font-size: {label_px}px;
    min-height: 22px;
}}
#mfmHeaderCorner QComboBox:hover {{ background-color: {fill_subtle}; }}
#mfmHeaderCorner QToolButton {{ color: {on_surface_dim}; }}
/* 起動CTA: 唯一の有彩色だが、ヘッダは沈める階層なので «ティント＋枠» に抑える */
QPushButton#mfmLaunchCta {{
    background-color: {cta_tint};
    color: {on_primary_container};
    border: 1px solid {primary};
    border-radius: {r_pill}px;
    font-weight: {w_strong};
    padding: 1px 14px;
    min-height: 22px;
}}
QPushButton#mfmLaunchCta:hover {{ background-color: {cta_tint_hover}; }}
QPushButton#mfmLaunchCta:pressed {{ background-color: {primary}; color: {on_primary}; }}
""".format(**v)
    return qss


# ---------------------------------------------------------------------------
# Application entry
# ---------------------------------------------------------------------------

def apply_theme(app, mode="dark"):
    """
    QApplication にテーマを適用する。
    Fusion スタイル + トークン由来 QPalette + 生成 QSS の3点セット。
    実行時の再呼び出し（テーマ切替）にも対応。
    """
    from core.compat import QPalette, QColor

    global _current_mode
    _current_mode = mode
    c = get_colors(mode)

    def qc(value):
        # rgba() 文字列は QPalette には使わないため hex のみ対応
        return QColor(value)

    palette = QPalette()
    palette.setColor(QPalette.Window,          qc(c["surface"]))
    palette.setColor(QPalette.WindowText,      qc(c["on_surface"]))
    palette.setColor(QPalette.Base,            qc(c["surface_container_low"]))
    palette.setColor(QPalette.AlternateBase,   qc(c["surface_container"]))
    palette.setColor(QPalette.ToolTipBase,     qc(c["surface_container_high"]))
    palette.setColor(QPalette.ToolTipText,     qc(c["on_surface"]))
    palette.setColor(QPalette.Text,            qc(c["on_surface"]))
    palette.setColor(QPalette.Button,          qc(c["surface_container_high"]))
    palette.setColor(QPalette.ButtonText,      qc(c["on_surface"]))
    palette.setColor(QPalette.BrightText,      qc(c["error"]))
    palette.setColor(QPalette.Link,            qc(c["primary"]))
    palette.setColor(QPalette.Highlight,       qc(c["selection"]))
    palette.setColor(QPalette.HighlightedText, qc(c["on_selection"]))

    app.setStyle("Fusion")
    app.setPalette(palette)
    app.setStyleSheet(build_qss(mode))


def switch_colors(mode=None):
    """DccSwitch（ヘッダの Maya⇔Blender 切替）の色をトークンから返す。
    r88: ON/OFF に見えないよう «左右で同じ» 配色にする。
    トラック=surface_container_high（左右共通）/ ノブ=primary（左右共通）/
    枠=hairline_strong / 反対側の止まり位置の点=on_surface_dim。"""
    c = get_colors(mode or _current_mode)
    return {"track": c["surface_container_high"], "knob": c["primary"],
            "border": c["outline"], "tick": c["on_surface_dim"]}


def export_qss(mode="dark", out_path=None):
    """生成QSSをファイルへ書き出す（デバッグ・外部利用向け）。"""
    qss = build_qss(mode)
    if out_path:
        Path(out_path).write_text(qss, encoding="utf-8")
    return qss
