# -*- coding: utf-8 -*-
"""Miller カラムビュー本体と周辺ウィジェット（r110 で分離）。

CappedColumnView: 深さ上限つき QColumnView。選択・D&D・カラム幅・
表示モード切替など、カラム上の操作はすべてここに集約する。"""
from core.diag import swallow as _swallow  # r112
from core.i18n import tr  # r118

import os

from core.compat import (
    Qt, QObject,
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QLineEdit, QToolButton,
    QColumnView, QListView,
    QFrame, QAbstractItemView, QSlider, QSizePolicy,
    QMenu, QInputDialog, QStyle, QProxyStyle, QModelIndex, QSize, QRect, QPixmap, QPainter, QColor, QFileInfo, QUrl, QMimeData, QPoint,
    QFontMetrics, QTimer, QDrag, QCursor,
)
from core.compat import QtCore as _QtCore
try:  # PySide6: QtGui / PySide2: QtWidgets
    from core.compat import QIcon
except ImportError:  # pragma: no cover
    QIcon = None
try:
    from PySide6.QtGui import QFileIconProvider
except ImportError:
    try:
        from PySide6.QtWidgets import QFileIconProvider
    except ImportError:
        from PySide2.QtWidgets import QFileIconProvider
from core.file_operations import (
    open_with_default_app
)

from ui.browser_util import (  # noqa: F401  （再エクスポート）
    _cursor_over_maya_window, _cursor_over_dcc_window, _time_mod, _MFM_T0,
    _MFM_STARTUP_LOG, _MFM_FREEZE_LOG, _re_mod, _DRIVE_IN_LABEL_RE,
    _safe_file_path, _SafeIconProvider, _mfm_slow_note, _MFM_UI_LOG,
    _MFM_UILOG_ON, _mfm_warn, _mfm_uilog, _MFM_BLOCKING,
    mfm_blocking_begin, mfm_blocking_end, mfm_blocking_reason,
    _mfm_timeline, _MFM_LOG_PATH, _MFM_DEBUG, _mfm_log,
)
from ui.browser_delegates import (  # noqa: F401  （再エクスポート）
    _paint_expanded_mark, ThumbnailDelegate, _BADGE_STYLE, _P4_BADGE_PNG,
    _BADGE_ICON_DIR, _BADGE_PIXMAP_CACHE, _badge_pixmap, _badge_kind,
    _FileOpNotifier, _INTEG_RESULT_CONNECTED,
    _on_integration_action_finished, _badge_tooltip, StatusBadgeDelegate,
)
from ui.browser_models import (  # noqa: F401  （再エクスポート）
    FileFilterProxyModel,
)





class _DccAwareMime(QMimeData):
    """ドラッグするファイル一覧を «落とし先が DCC かどうか» で出し分ける mime（r90）。

    背景（2026-09-23 実害）: install.py を Maya のビューポートへ D&D すると、
    Maya は «OLE のドロップ処理の中で» スクリプトを同期実行する。インストーラが
    ダイアログを出すと Maya 側の Drop() が戻らず、ドラッグ元（このツール）は
    Windows の DoDragDrop の中で止まったまま（mfm_freeze.log で 250 秒以上）。
    さらに設定の «D&D 動作» もドロップ後に Manager から送るため、Maya 自身の
    ドロップ処理と二重実行になる（例: .ma が Maya に取り込まれ、さらに開く）。

    対策: ファイル一覧は «落とし先が取りに来た瞬間» に作る（Qt の遅延レンダリング:
    retrieveData）。カーソル下が Maya/Blender で、Manager 側で処理できる
    （ブリッジ接続中＋対応形式）なら、DCC には «空のファイル一覧» を渡して
    ネイティブ処理をさせない。ドロップ後に Manager がブリッジ経由（非同期）で
    実行するので、OLE の同期処理でツールが止まることは無い。
    自アプリ内・Explorer 等へは従来どおり実ファイルを渡す。"""

    def __init__(self, paths, decide):
        super().__init__()
        self._paths = list(paths)
        # () -> (app or None, [Manager が引き受けるパス])  ※r91 で bool から一覧へ
        self._decide = decide
        self.takeover_app = None         # Manager が引き受けた DCC
        self.handled = []                # Manager が引き受けたパス
        self.served_real_to_dcc = False  # DCC に実ファイルを渡した（ネイティブ処理）

    def formats(self):
        return ["text/uri-list"]

    def hasFormat(self, mimetype):
        return mimetype == "text/uri-list"

    def retrieveData(self, mimetype, preferred_type=None):
        if mimetype != "text/uri-list":
            return None
        try:
            app, handled = self._decide()
        except Exception:
            app, handled = None, []
        if not app:
            return [QUrl.fromLocalFile(p) for p in self._paths]
        handled = list(handled or [])
        if handled:
            self.takeover_app = app
            self.handled = handled
        # Manager が引き受けたものは DCC に渡さない。残り（Manager の各コマンドの
        # 対象外）は DCC 自身のドロップ処理に任せる
        keep = set(handled)
        served = [p for p in self._paths if p not in keep]
        if served:
            self.served_real_to_dcc = True
        return [QUrl.fromLocalFile(p) for p in served]


class _ColumnSizePopup(QFrame):
    """表示サイズ調整のスライダー（r85）。

    カラムヘッダの «表示切替（▦）» ボタンにマウスオーバーすると出る。
    リスト表示／サムネイル表示のどちらでも同じスライダーで調整し、
    値はモードごとに別々に覚える（リストは小さく、グリッドは大きくが自然なため）。
    ボタンとポップアップのどちらからもマウスが離れたら少し待って閉じる。"""

    LIST_RANGE = (16, 128)
    THUMB_RANGE = (48, 256)

    def __init__(self, owner):
        # r100: **Qt.Popup にしてはいけない**。Qt::Popup はマウスを grab するため、
        # ▦ にマウスオーバーした瞬間に入力を奪い、続く «▦ のクリック» は
        # ポップアップを閉じるだけで消費される（＝表示切替が効かない）。
        # r103: さらに «別ウィンドウ» をやめて **カラムビューの子ウィジェット**に
        # する。トップレベルだとウィンドウマネージャ側の都合（表示順・アクティブ
        # ウィンドウ判定・ツールチップとの重なり）で出ないことがあり、実機で
        # 「説明のツールチップしか出ない」状態になっていた（2026-09-25）。
        # 子ウィジェットなら必ず描かれ、grab もしない。
        super().__init__(owner)
        self.setAutoFillBackground(True)
        self._owner = owner          # CappedColumnView
        self._view = None            # 対象カラム（QListView）
        self._btn = None
        self._mode = "list"          # r104: 今どのモード用に出しているか
        self.setObjectName("mfmSizePopup")
        self.setFrameShape(QFrame.NoFrame)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)
        self._icon = QLabel(tr("▦ 全体", "▦ All"), self)
        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setFixedWidth(130)
        self._slider.setToolTip(tr("表示サイズ", "Item size"))
        self._label = QLabel("", self)
        self._label.setFixedWidth(34)
        lay.addWidget(self._icon)
        lay.addWidget(self._slider)
        lay.addWidget(self._label)
        self._slider.valueChanged.connect(self._on_value)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(450)
        self._hide_timer.timeout.connect(self._maybe_hide)
        self.setMouseTracking(True)
        self._apply_style()

    def _apply_style(self):
        from core.theme_engine import qss_vars
        self.setStyleSheet(
            "#mfmSizePopup{background:%(surface_container_high)s;"
            "border:1px solid %(hairline_strong)s;border-radius:%(r_m)spx;}"
            "QLabel{color:%(on_surface_variant)s;font-size:%(label_px)spx;"
            "background:transparent;}"
            "QSlider::groove:horizontal{height:3px;background:%(hairline)s;"
            "border-radius:1px;}"
            "QSlider::handle:horizontal{width:12px;margin:-5px 0;"
            "background:%(primary)s;border-radius:6px;}"
            "QSlider::sub-page:horizontal{background:%(primary)s;border-radius:1px;}"
            % qss_vars())

    # ------------------------------------------------------------------
    def show_for(self, view, btn):
        """そのカラム用の値を読み込んで、ボタンの下に出す。"""
        self._view = view
        self._btn = btn
        mode = getattr(view, "_mfm_view_mode", "list")
        self._mode = mode          # r104: このスライダーが «今どのモード用» か
        lo, hi = self.THUMB_RANGE if mode == "thumb" else self.LIST_RANGE
        cur = self._owner.column_item_size(view)
        self._slider.blockSignals(True)
        self._slider.setRange(lo, hi)
        self._slider.setValue(max(lo, min(hi, int(cur))))
        self._slider.blockSignals(False)
        self._label.setText("%dpx" % self._slider.value())
        self._apply_style()
        self.adjustSize()
        # r103: 親（カラムビュー）の座標系で、ボタンの真下に出す。
        # はみ出す時は内側へ寄せる（子ウィジェットなのでクリップされるため）。
        pos = self._owner.mapFromGlobal(btn.mapToGlobal(QPoint(0, btn.height() + 2)))
        x = max(2, min(pos.x(), self._owner.width() - self.width() - 2))
        y = max(2, min(pos.y(), self._owner.height() - self.height() - 2))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self._hide_timer.stop()

    def _on_value(self, v):
        self._label.setText("%dpx" % v)
        if self._view is None:
            return
        mode = getattr(self._view, "_mfm_view_mode", "list")
        # r104: ▦ を押して表示モードが変わった直後は、スライダーの目盛りが
        # «前のモードのレンジ» のまま残っている。そのまま適用すると
        # リストの値（例: 40px）がグリッドのセル寸法として保存され、
        # «切り替えるたびにグリッドが小さい» 状態になる（ユーザー報告
        # 2026-09-25）。モードがズレていたら読み直すだけにする。
        if mode != getattr(self, "_mode", mode):
            self.show_for(self._view, self._btn)
            return
        # «全体的な表示サイズ» を変える。同じモードの全カラムへ適用。
        self._owner.set_item_size_all(v, mode)

    # --- 閉じる判定（ボタン上／ポップアップ上のどちらでもなければ閉じる） ---
    def request_hide(self):
        self._hide_timer.start()

    def keep_open(self):
        self._hide_timer.stop()

    def _under_cursor(self, w):
        try:
            return w is not None and w.rect().contains(
                w.mapFromGlobal(QCursor.pos()))
        except Exception:
            return False

    def _maybe_hide(self):
        if self._under_cursor(self) or self._under_cursor(self._btn):
            self._hide_timer.start()
            return
        self.hide()

    def enterEvent(self, e):
        self.keep_open()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.request_hide()
        super().leaveEvent(e)


class _SizeButtonHover(QObject):
    """▦ ボタンのホバーでサイズ調整スライダーを出すフィルタ（r85）。

    r127: **すぐには出さない。** 以前は Enter で即座に出していたため、
    ボタンの上をマウスが «少し通っただけ» でサイズ調整バーが開いて邪魔に
    なっていた（ユーザー報告 2026-10-07）。«変えたい» という意思が
    はっきりしている時だけ出したいので、一定時間カーソルが乗り続けた
    場合にだけ開く。離れたら予約は取り消す。
    """

    # 乗せ続ける必要がある時間（ミリ秒）。Windows のツールチップ（約 500ms）
    # より «意思» が要る長さにしてある。通りすがりでは出ない。
    HOVER_DELAY_MS = 700

    def __init__(self, owner, view, btn):
        super().__init__(btn)
        self._owner = owner
        self._view = view
        self._btn = btn
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.HOVER_DELAY_MS)
        self._timer.timeout.connect(self._open_now)

    def _open_now(self):
        # 予約が切れた時点で «まだボタンの上に居る» ことを確かめてから出す。
        # 離れた直後にタイマーだけ生き残って開く、を防ぐ。
        try:
            btn = self._btn
            if btn is None or not btn.isVisible():
                return
            if not btn.rect().contains(btn.mapFromGlobal(QCursor.pos())):
                return
            self._owner.size_popup().show_for(self._view, btn)
        except Exception as e:
            _mfm_log("size popup error: %r" % (e,))

    def eventFilter(self, obj, event):
        et = event.type()
        if et == _QtCore.QEvent.Enter:
            self._timer.start()
        elif et == _QtCore.QEvent.Leave:
            self._timer.stop()
            try:
                self._owner.size_popup().request_hide()
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:274 eventFilter")
        elif et in (_QtCore.QEvent.MouseButtonPress,
                    _QtCore.QEvent.MouseButtonDblClick):
            # ▦ を «押した» 時は表示モードの切替が目的。
            # バーが遅れて出てくると邪魔なので予約を捨てる。
            self._timer.stop()
        return False


# r120: アニメーション時間のスタイルヒント。PySide6 では enum が入れ子
# （QStyle.StyleHint.SH_...）になっていることがあるので両方の綴りを見る。
# 見つからなければ None にして «切れません» と分かるようにする（黙って
# 効かないのが一番たちが悪い）。
def _anim_duration_hint():
    for owner in (getattr(QStyle, "StyleHint", None), QStyle):
        h = getattr(owner, "SH_Widget_Animation_Duration", None) \
            if owner is not None else None
        if h is not None:
            return h
    return None


SH_ANIMATION_DURATION = _anim_duration_hint()


class _NoAnimationStyle(QProxyStyle):
    """カラムのスライドアニメーションを切るためだけのスタイル（r120）。

    Qt 6 の QColumnView::scrollTo は SH_Widget_Animation_Duration が 0 なら
    水平スクロールを «アニメーションせずに» 終点へ入れる。ここで 0 を返す。
    他のヒントは元のスタイルへそのまま委ねるので、見た目は変わらない。"""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if SH_ANIMATION_DURATION is not None and hint == SH_ANIMATION_DURATION:
            return 0
        return super().styleHint(hint, option, widget, returnData)


class _ColumnResizeHandle(QWidget):
    """カラム境界の縦ハンドル。ドラッグで幅変更、ダブルクリックで内容に合わせる。

    QColumnView のビューポートを親にし、カラムの右辺を «またぐ» 形で配置する
    （左半分はカラム右端、右半分は隣カラムの左端）。カラム内に置くと縦
    スクロールバーと重なり細くせざるを得ず「掴みにくい」ため。"""

    WIDTH = 12
    MIN_W = 120
    MAX_W = 1400

    def __init__(self, column_view, view):
        super().__init__(column_view.viewport())
        self._cv = column_view
        self._view = view
        self._drag_x = None
        self._start_w = 0
        self._hover = False
        self.setFixedWidth(self.WIDTH)
        self.setCursor(Qt.SplitHCursor)
        self.setMouseTracking(True)
        self.setToolTip(tr("ドラッグで幅を変更 / ダブルクリックで内容に合わせる",
                           "Drag to resize / double-click to fit contents"))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            # AuthKit ヘアライン（ホバー/ドラッグ中は帯全体を明るく）
            if self._drag_x is not None or self._hover:
                p.fillRect(self.rect(), QColor(186, 215, 247, 70))
            # r105: スクロールバーの左に置くようになったので、罫線は帯の
            # 右端に寄せる（掴める帯＝その左側、と分かるように）
            p.fillRect(self.width() - 2, 0, 2, self.height(),
                       QColor(186, 215, 247, 90))
        finally:
            p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_x = e.globalPosition().toPoint().x() \
                if hasattr(e, "globalPosition") else e.globalPos().x()
            self._start_w = self._view.width()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_x is None:
            return
        gx = e.globalPosition().toPoint().x() \
            if hasattr(e, "globalPosition") else e.globalPos().x()
        w = max(self.MIN_W, min(self.MAX_W, self._start_w + (gx - self._drag_x)))
        self._cv.set_column_width_for_view(self._view, w)
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._drag_x is not None:
            self._drag_x = None
            self._cv.persist_column_widths()
            self.update()
            e.accept()
        else:
            super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        w = self._cv.fit_width_for_view(self._view)
        if w:
            self._cv.set_column_width_for_view(self._view, w)
            self._cv.persist_column_widths()
        e.accept()


# ---------------------------------------------------------------------------
# Column-capped View
# ---------------------------------------------------------------------------

class CappedColumnView(QColumnView):
    """
    QColumnView with configurable maximum column depth.

    仕様: 選択がmax_depthより深くなったら、ルートを1段下げて
    可視カラム数をmax_depth以内に保つ（macOS Finder相当の挙動）。

    実装注意: currentChanged の中で setRootIndex を呼ぶと
    QColumnView内部のカラム再構築と競合してクラッシュするため、
    QTimer.singleShot(0) でイベントループ一巡後に実行する。
    """

    def __init__(self, max_depth: int = 4, parent=None):
        super().__init__(parent)
        self._max_depth = max_depth
        self._go_up_cb = None
        self._thumb_mgr = None
        self._merge_cb = None
        self._flat_cb = None        # インライン平坦カラム要求コールバック cb(dirs)
        self._thumb_prefetch_cb = None  # r84: サムネ表示化した列の先読み cb(folder)
        self._item_size_cb = None       # r102: サイズ変更を他ビューへ配る cb(px, mode)
        self._size_popup = None         # r85: 表示サイズ調整スライダー
        self._flatten_view = None   # 現在「平坦」ONのカラムビュー（排他制御用）
        self._pending_multi_drag = None
        self._maya_drop_cb = None      # Mayaへのドロップ検出時コールバック(paths)
        self._file_drop_cb = None      # r87: カラムへのファイルドロップ cb(paths, dest, move)
        self._dcc_takeover_cb = None   # r90: DCC への落下を Manager が引き受けるか cb(paths, app)
        self._selected_dir_paths = set()
        self._mfm_columns = []      # r81: 生成済みカラムビュー（展開中フォルダ判定用）
        # r83: ナビゲーション後にユーザーが項目をクリックしたか。
        # 遅延実行される «カラム自己修復» が選択を壊さないためのガード。
        self._mfm_user_selected = False
        # 未読み込みカラムの「読み込み中」スピナー（カラム毎に個別表示）。
        # 判定: canFetchMore（fetch未開始）に加え、fetch開始後も列挙が終わる
        # まで（directoryLoaded 受信まで）rowCount==0 なら読み込み中とみなす。
        # 完了待ちを directoryLoaded «だけ» に依存してはならない（pip版Qtの
        # 再発火しない罠）ため、rowCount>0 でも即座に完了扱いにする。
        self._loaded_dirs = set()      # directoryLoaded 済みパス（normcase）
        self._loading_first_seen = {}  # path → 初回観測時刻（安全弁用）
        self._spin_reported = {}       # path → 最終ログ時刻（長期表示の調査用）
        self._spin_phase = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(150)
        self._loading_timer.timeout.connect(self._update_loading_overlays)
        self._loading_timer.start()

    def set_max_depth(self, depth: int):
        self._max_depth = depth

    # ── スクロール整合性（r54） ───────────────────────────────────────────
    # QColumnView は「カラム位置 x」「内部 offset」「hbar.value」の3つが常に
    # x == offset == -value の関係で動く前提。全カラム再構築（setRootIndex →
    # closeColumns）で offset は 0 に戻るが、スクロールアニメーション中は
    # updateScrollbars() が早期 return して hbar の値が残るため、
    # 「offset=0 / value=70」のような食い違いが生まれ、以後 value とカラム位置の
    # 対応が恒久的に 70px ずれる。症状＝末尾カラムが途中で切れる／右に空白／
    # スクロール最右端でも見えない階層がある（2026-09 実機、オフスクリーンで再現）。
    # 対策: 再構築の直前に必ず value を 0 に戻す（value 変化はカラム移動と
    # offset 更新を伴うため整合が保たれる）。
    def _reset_hscroll_for_rebuild(self):
        try:
            hbar = self.horizontalScrollBar()
            if hbar is not None and hbar.value() != 0:
                hbar.setValue(0)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:426 _reset_hscroll_for_rebuild")

    def setRootIndex(self, index):
        self._reset_hscroll_for_rebuild()
        super().setRootIndex(index)
        # r71: 深いパスへの再構築では 260ms 時点でまだ current の列が生成されて
        # いない（列挙が非同期）ことがあり、1回では右外に残った（mayapy の
        # 回帰スイート「navigate mid-anim」で再現）。数回に分けて再確認する。
        # 各回とも «既に見えていれば何もしない» ので副作用は無い。
        # 初回（260ms、Qt 自身のスクロールアニメーション後）は無条件。2回目以降は
        # 前回の確認以降に hbar が動いていたら（＝ユーザーが手でスクロールした）
        # 追いかけずに打ち切る。
        token = object()
        self._ensure_token = token
        self._ensure_last_val = None

        def _retry():
            if getattr(self, "_ensure_token", None) is not token:
                return
            hbar = self.horizontalScrollBar()
            last = getattr(self, "_ensure_last_val", None)
            if last is not None and hbar.value() != last:
                self._ensure_token = None
                return
            self._ensure_current_column_visible()
            self._ensure_last_val = hbar.value()

        for _ms in (260, 700, 1300, 2200):
            QTimer.singleShot(_ms, _retry)

    # ── ファイルではカラムを横スクロールさせない（r57） ────────────────
    # QColumnView::scrollTo は「index の列＋その子列」を可視化するために hbar を
    # アニメーションで動かす（QAbstractItemView::currentChanged 等から仮想呼び出し）。
    # ファイルは «操作対象» であり、クリックで列が滑ると操作の的が動いて
    # ストレスになる（指摘あり）。ファイルに対する scrollTo は横移動を行わず、
    # 所属列の縦スクロール（項目の可視化）だけ行う。フォルダは従来通り。
    def scrollTo(self, index, hint=QAbstractItemView.EnsureVisible):
        try:
            if index is not None and index.isValid() and not self._is_dir_index(index):
                _mfm_uilog("scrollTo(file) 抑止: %r" % (index.data(),))
                parent = index.parent()
                for v in self.findChildren(QListView):
                    if getattr(v, "_mfm_header", None) is not None \
                            and v.isVisible() and v.rootIndex() == parent:
                        try:
                            v.scrollTo(index)      # 縦方向のみ（列内）
                        except Exception as _e:
                            _swallow(_e, "ui/browser_column_view.py:473 scrollTo")
                        break
                return
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:477 scrollTo")
        super().scrollTo(index, hint)
        # r120: アニメーションを切っている時は、Qt がアニメーションの
        # «終了» を合図に行う後片付け（_q_changeCurrentColumn）が走らない。
        # そのままだと新しいカラムが作られても «表示されない» ままになる
        # （実機報告 2026-10-03）。同じことを自前の再配置で済ませる。
        if not self.slide_animation():
            self._show_current_column()
            self._relayout_columns()

    def _show_current_column(self):
        """«現在のフォルダの中身を出すカラム» を表示する（r120）。

        Qt は新しいカラムを作った後、**アニメーションの終了を合図に**
        後片付け（そのカラムの show）を行う。アニメーションを切ると
        その合図が来ないので、作られたのに表示されないままになる
        （実機報告 2026-10-03「Off にすると次のカラムが表示されない」）。
        ここで同じことをする。対象は «現在の index をルートに持つカラム»
        だけに限る（Qt が使い回し用に隠しているカラムを出さないため）。"""
        try:
            cur = self.currentIndex()
            if not cur.isValid():
                return
            for c in self.findChildren(QListView):
                if getattr(c, "_mfm_header", None) is None:
                    continue
                try:
                    if c.isVisible():
                        continue
                    if c.rootIndex() == cur:
                        c.show()
                except RuntimeError:
                    continue
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py _show_current_column")

    # ── r120: カラムのスライドアニメーション（設定で切れる）──────────
    #
    # 【重要・実機で踏んだ】**scrollTo を横取りして早期 return してはいけない。**
    # QColumnView::scrollTo は «スクロール» だけでなく、その index を表示する
    # ために必要なカラムを «作る»（内部の closeColumns(index, build=true)）。
    # 自前のスクロールで済ませて super() を呼ばずに返したところ、
    # 次のカラムが一切出なくなった（ユーザー報告 2026-10-03）。
    #
    # 正しい切り方は Qt が公開しているスタイルヒント。Qt 6 の
    # QColumnView::scrollTo は
    #     if (int d = style()->styleHint(SH_Widget_Animation_Duration, ...))
    #         アニメーションで動かす
    #     else
    #         horizontalScrollBar()->setValue(終点)      ← 一気に動く
    # となっているので、このビューにだけ «0 を返すスタイル» をかぶせれば、
    # カラム生成はそのままに移動だけが即座になる。
    def set_slide_animation(self, on: bool):
        on = bool(on)
        if on == getattr(self, "_slide_anim", True) and \
                hasattr(self, "_slide_anim"):
            return
        self._slide_anim = on
        try:
            if on:
                self.setStyle(None)            # アプリ既定のスタイルへ戻す
                self._noanim_style = None
            else:
                # 【重要】**引数を渡さないこと。** QProxyStyle(style) は
                # 渡したスタイルの «所有権を奪う»。QApplication.style() を
                # 渡したところ、アプリ既定のスタイルが proxy の持ち物になり、
                # ビューが丸ごと壊れた（カラムに項目が出なくなった）。
                # 引数なしなら «その時のアプリのスタイル» を所有せずに包む。
                st = _NoAnimationStyle()
                st.setParent(self)             # 参照を保って GC を防ぐ
                self._noanim_style = st
                self.setStyle(st)
        except Exception as e:
            _mfm_warn("set_slide_animation(%s) failed: %r" % (on, e))

    def slide_animation(self) -> bool:
        return bool(getattr(self, "_slide_anim", True))

    # ── ゆっくり2回クリックで名前変更（r67、Explorer 準拠） ──────────────
    def _schedule_reclick_rename(self, path: str):
        if not path:
            return
        self._cancel_reclick_rename()
        t = QTimer(self)
        t.setSingleShot(True)
        try:
            interval = QApplication.doubleClickInterval() + 80
        except Exception:
            interval = 480
        t.setInterval(interval)
        t.timeout.connect(lambda: self._fire_reclick_rename(path))
        t.start()
        self._reclick_timer = t

    def _cancel_reclick_rename(self):
        t = getattr(self, "_reclick_timer", None)
        if t is not None:
            try:
                t.stop()
                t.deleteLater()
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:503 _cancel_reclick_rename")
            self._reclick_timer = None

    def _fire_reclick_rename(self, path: str):
        self._reclick_timer = None
        cb = getattr(self, "_rename_cb", None)
        if callable(cb):
            try:
                cb(path)
            except Exception as e:
                _mfm_log("reclick rename error: %r" % (e,))

    def note_file_click(self, name):
        """ファイルクリック直後 1.2 秒間、水平スクロール値の変化を呼び出し元付きで
        記録する（原因切り分け用・常時有効・軽量）。"""
        try:
            self._file_click_t = _time_mod.monotonic()
            hbar = self.horizontalScrollBar()
            _mfm_uilog("file-click %r hval=%d/%d" % (name, hbar.value(), hbar.maximum()))
            if not getattr(self, "_hwatch_connected", False):
                hbar.valueChanged.connect(self._on_hbar_changed_watch)
                self._hwatch_connected = True
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:526 note_file_click")

    def _on_hbar_changed_watch(self, v):
        try:
            t0 = getattr(self, "_file_click_t", 0.0)
            if t0 and _time_mod.monotonic() - t0 < 1.2:
                _mfm_uilog("hscroll moved after file-click: value=%d" % v, with_stack=True)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:534 _on_hbar_changed_watch")

    def setModel(self, model):
        self._reset_hscroll_for_rebuild()
        super().setModel(model)

    def _ensure_current_column_visible(self):
        """再構築直後は Qt の scrollTo がアニメーション中で早期 return し、
        目的のカラムが右外に残ることがある。アニメーション無しで hbar の値を
        直接動かして «current のカラム＋その子カラム» を可視域に入れる
        （scrollTo は changeCurrentColumn を伴い選択モデルを触るため使わない）。"""
        try:
            cur = self.currentIndex()
            if not cur.isValid():
                return
            cols = self._column_views_sorted()
            if not cols:
                return
            target = None
            for i, v in enumerate(cols):
                if v.rootIndex() == cur.parent():
                    target = i
                    break
            if target is None:
                return
            hbar = self.horizontalScrollBar()
            vw = self.viewport().width()
            left = cols[target].x()
            right_i = min(target + 1, len(cols) - 1)
            right = cols[right_i].x() + cols[right_i].width()
            if left >= 0 and right <= vw:
                return
            # 右端を合わせる（左端が隠れるなら左端優先）
            delta = right - vw if right > vw else left
            new_val = max(hbar.minimum(), min(hbar.maximum(), hbar.value() + delta))
            if new_val != hbar.value():
                _mfm_log("ensure_visible: hval %d -> %d (left=%d right=%d vw=%d)"
                         % (hbar.value(), new_val, left, right, vw))
                hbar.setValue(new_val)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:574 _ensure_current_column_visible")

    def set_go_up_callback(self, cb):
        """◀ボタン押下時に呼ぶコールバック（1階層上げる）を登録。"""
        self._go_up_cb = cb

    def note_dir_loaded(self, path: str):
        """QFileSystemModel.directoryLoaded の記録（スピナー消灯の根拠）。"""
        try:
            key = os.path.normcase(os.path.normpath(path))
            self._loaded_dirs.add(key)
            self._loading_first_seen.pop(key, None)
            self._spin_reported.pop(key, None)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:588 note_dir_loaded")

    # ── r81: 展開中フォルダの目印 ─────────────────────────────────────
    def _track_column(self, view):
        """生成されたカラムを覚え、破棄時に外す。右隣のカラムが出来た/消えた
        タイミングで各カラムを再描画し、目印の出入りを即反映する。"""
        self._mfm_columns.append(view)
        self._invalidate_expanded_cache()
        try:
            view.destroyed.connect(lambda *_: self._on_column_destroyed())
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:598 _track_column")
        QTimer.singleShot(0, self._repaint_columns)

    def _on_column_destroyed(self):
        self._live_columns()
        self._invalidate_expanded_cache()
        self._repaint_columns()

    def _live_columns(self):
        alive = []
        for c in self._mfm_columns:
            try:
                c.rootIndex()
                alive.append(c)
            except RuntimeError:
                continue
        self._mfm_columns = alive
        return alive

    def _repaint_columns(self):
        self._invalidate_expanded_cache()
        for c in self._live_columns():
            try:
                c.viewport().update()
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:621 _repaint_columns")

    # r120: 展開中フォルダの判定は «セルごと・フレームごと» に呼ばれる。
    # 従来は呼ばれる度に生存カラムを数え直して rootIndex を全部引いていた
    # （カラム 5 本 × 可視 125 セル × 60fps ＝ 毎秒 37,500 回）。
    # 結果を覚えておき、カラムの増減時と、保険として短い時間で作り直す。
    _EXPANDED_TTL = 0.1          # 秒。これだけ遅れても «下地の色» なので支障なし
    _expanded_ids = None
    _expanded_ids_t = 0.0

    def _invalidate_expanded_cache(self):
        self._expanded_ids = None

    def _expanded_id_set(self):
        ids = getattr(self, "_expanded_ids", None)
        if ids is not None and \
                (_time_mod.monotonic() - self._expanded_ids_t) < self._EXPANDED_TTL:
            return ids
        ids = set()
        for c in self._live_columns():
            try:
                r = c.rootIndex()
                if r.isValid():
                    ids.add((r.internalId(), r.row(), r.column()))
            except Exception:
                continue
        self._expanded_ids = ids
        self._expanded_ids_t = _time_mod.monotonic()
        return ids

    def _is_expanded_index(self, index) -> bool:
        """index のフォルダの中身を表示しているカラムが存在するか
        （＝そのフォルダが「展開中」か）。"""
        if not index.isValid():
            return False
        return (index.internalId(), index.row(), index.column()) \
            in self._expanded_id_set()

    def createColumn(self, index):
        """各カラム生成時のセットアップ（D&D設定・ヘッダ・スピナー）。
        旧「◀ 上の階層へ」オーバーレイボタンは廃止（使われず紛らわしい、
        との指摘のため。2026-09-03）。"""
        view = super().createColumn(index)
        self._track_column(view)
        # 各カラム(子ビュー)へ Explorer 互換のD&D設定を適用。
        # これを怠るとカラム上でのドロップが効かない。
        try:
            view.setDragEnabled(True)
            view.setAcceptDrops(True)
            view.setDropIndicatorShown(True)
            view.setDragDropMode(QAbstractItemView.DragDrop)
            view.setDefaultDropAction(Qt.MoveAction)
            view.setDragDropOverwriteMode(False)
            view.setEditTriggers(QAbstractItemView.NoEditTriggers)
            # 各カラムで Shift/Ctrl の複数選択を効かせる（QColumnView単体だと
            # 列ビューに伝播せず効かないため明示設定）
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:656 createColumn")
        folder_path = self._path_for_index(index)
        # r85: 保存済みの «リスト表示のアイコンサイズ» を適用（既定 16px）
        try:
            view.setIconSize(QSize(self.column_item_size(view),
                                   self.column_item_size(view)))
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:663 createColumn")
        # 連携状態バッジ付きデリゲート（通常描画＋右下に小さな丸）
        try:
            view.setItemDelegate(StatusBadgeDelegate(self._path_of_index, view,
                                                is_dir_of_index=self._is_dir_index,
                                                is_expanded_index=self._is_expanded_index))
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:670 createColumn")
        # このカラムのフォルダの連携状態をワーカーへ要求（表示時に1回）
        try:
            if folder_path:
                self._integrations().request_status(folder_path)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:676 createColumn")
        # r119: このフォルダの表示モードを復元する（カラム毎に記憶）。
        # ヘッダより先にやると表示ボタンの見た目が合わないので、ヘッダの後。
        # カラム上部に「このカラムだけに効く」フィルタ／ソートのヘッダを設置
        self._build_column_header(view, folder_path)
        try:
            saved = self.saved_view_mode(folder_path)
            if saved == "thumb":
                self._set_column_view_mode(view, "thumb")
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py createColumn(view mode)")
        # 右端のリサイズハンドル（幅変更・ダブルクリックで自動調整）
        view._mfm_resize_handle = _ColumnResizeHandle(self, view)
        view._mfm_resize_handle.show()
        # ハンドルはビューポートの子（カラムの子ではない）なので、カラム破棄時に
        # 一緒に消す
        _h = view._mfm_resize_handle
        view.destroyed.connect(lambda *_a, h=_h: h.deleteLater())
        self._reposition_column_header(view)
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)
        # 「読み込み中」スピナー（このカラムが未読み込みの間だけ表示）
        spin = QLabel(view.viewport())
        spin.setAttribute(Qt.WA_TransparentForMouseEvents)
        spin.setAlignment(Qt.AlignCenter)
        from core.theme_engine import qss_vars
        spin.setStyleSheet(
            "QLabel{color:%(on_surface_variant)s;background:%(scrim)s;"
            "border:1px solid %(hairline)s;border-radius:%(r_m)spx;"
            "padding:6px 14px;font-size:%(body_px)spx;}" % qss_vars())
        spin.hide()
        view._mfm_loading = spin
        return view

    def _update_loading_overlays(self):
        """各カラムの読み込み状態を確認し、未読み込みのカラムにだけ
        「読み込み中」スピナーを表示する（150ms毎のポーリング）。"""
        self._spin_phase = (self._spin_phase + 1) % 4
        glyph = "◐◓◑◒"[self._spin_phase]
        # レイアウトの自己修復（カラム群の右ずれ検知）も同じ周期で行う
        self._check_column_layout_health()
        try:
            views = [v for v in self.findChildren(QListView)
                     if getattr(v, "_mfm_loading", None) is not None]
        except RuntimeError:
            return
        for view in views:
            try:
                spin = view._mfm_loading
                root = view.rootIndex()
                if not root.isValid() or not view.isVisible():
                    spin.hide()
                    continue
                # プロキシ → ソース(QFileSystemModel)へ解決
                m = view.model()
                idx = root
                while m is not None and hasattr(m, "mapToSource"):
                    idx = m.mapToSource(idx)
                    m = m.sourceModel()
                if m is None or not hasattr(m, "canFetchMore"):
                    spin.hide()
                    continue
                # 【重要】ソース側indexが無効なら何もしない。無効indexは
                # QFileSystemModel 内部で «ルート（マイコンピュータ）» を指すため、
                # canFetchMore が True になり続け、fetchMore で全ドライブ列挙
                # （切断ドライブで21秒×N）を誘発する。
                if not idx.isValid():
                    spin.hide()
                    continue
                # このカラムのフォルダの列挙が完了しているか。
                # 注意: rowCount は列挙完了前でも >0 になり得る
                # （起動時のパス復元では祖先チェーンが先にノード化され、
                #  各カラムに1件だけ表示される。r23はこれを見逃した）。
                # そのため判定は「directoryLoaded 受信済みか」を軸にする。
                p = ""
                try:
                    p = os.path.normcase(os.path.normpath(_safe_file_path(m, idx)))
                except Exception as _e:
                    _swallow(_e, "ui/browser_column_view.py:746 _update_loading_overlays")
                if not p:
                    spin.hide()
                    continue
                import time as _time
                now = _time.monotonic()
                loading = False
                reason = ""
                if p in self._loaded_dirs:
                    loading = False          # 列挙完了済み（最優先で消灯）
                elif m.canFetchMore(idx):
                    # fetch未開始 → 読み込みを促しつつスピナー表示
                    reason = "canFetchMore"
                    try:
                        m.fetchMore(idx)
                    except Exception as _e:
                        _swallow(_e, "ui/browser_column_view.py:762 _update_loading_overlays")
                    first = self._loading_first_seen.setdefault(p, now)
                    loading = (now - first) < 60.0   # 安全弁
                else:
                    # fetchは開始済みだが列挙完了通知が未受信（列挙スレッド動作中）
                    reason = "awaiting directoryLoaded"
                    first = self._loading_first_seen.setdefault(p, now)
                    # 安全弁: directoryLoaded を取り逃しても60秒で消灯
                    loading = (now - first) < 60.0
                if loading:
                    # 5秒以上出続ける列は原因調査用に状態を記録（30秒毎）
                    first = self._loading_first_seen.get(p, now)
                    age = now - first
                    last = self._spin_reported.get(p, -999.0)
                    if age > 5.0 and now - last > 30.0:
                        self._spin_reported[p] = now
                        try:
                            rows = view.model().rowCount(root)
                        except Exception:
                            rows = -1
                        _mfm_slow_note(
                            "SPINNER %.0fs path=%r reason=%s rows=%d "
                            "loaded_recorded=%s" % (
                                age, p, reason, rows, p in self._loaded_dirs))
                if loading:
                    from core.i18n import tr as _tr
                    spin.setText("%s %s" % (
                        glyph, _tr("読み込み中...", "Loading...")))
                    spin.adjustSize()
                    vp = view.viewport()
                    spin.move(max(0, (vp.width() - spin.width()) // 2),
                              max(0, (vp.height() - spin.height()) // 2))
                    spin.show()
                    spin.raise_()
                else:
                    spin.hide()
            except RuntimeError:
                continue   # カラム再構築中に破棄されたビュー
            except Exception:
                continue

    def _emit_go_up(self, folder_path=None):
        if callable(self._go_up_cb):
            self._go_up_cb(folder_path)

    # ------------------------------------------------------------------
    # カラム別フィルタ／ソート ヘッダ
    # ------------------------------------------------------------------
    _COL_HEADER_H = 52   # 2行（フィルタ行＋ソート/平坦/表示 行）

    @property
    def _SORT_KEYS(self):
        # r120: «表示名» 順（ユーザー指示 2026-10-02）。表示名を付けていない
        # 項目は実体名で並ぶので、付けた項目だけが意図した位置へ動く。
        return [("name", tr("名前", "Name")),
                ("alias", tr("表示名", "Display name")),
                ("type", tr("種類", "Type")),
                ("date", tr("日付", "Date")), ("size", tr("サイズ", "Size"))]

    def set_thumb_mgr(self, mgr):
        self._thumb_mgr = mgr

    def set_merge_callback(self, cb):
        self._merge_cb = cb

    # ── r85: 表示サイズ（リスト／サムネイル共通のスライダー） ──────────
    DEFAULT_SIZE = {"list": 16, "thumb": 96}

    def size_popup(self):
        if self._size_popup is None:
            self._size_popup = _ColumnSizePopup(self)
        return self._size_popup

    # ── 表示モード（リスト／サムネイル）のフォルダ別記憶（r119）────────
    # ユーザー指示: «サムネイル表示の有無もカラム毎に記憶してほしい»。
    # 同じフォルダを開き直した時・別エリアで開いた時も同じ見え方になる。
    VIEW_MODE_KEY = "column_view_modes"
    VIEW_MODE_MAX = 400        # 際限なく溜めない（古いものから捨てる）

    def _mode_key(self, folder_path: str) -> str:
        return os.path.normcase(os.path.abspath(folder_path or ""))

    def _view_modes(self) -> dict:
        sm = getattr(self, "_sm_widths", None)
        if sm is None:
            return {}
        try:
            d = sm.get(self.VIEW_MODE_KEY, None)
            return dict(d) if isinstance(d, dict) else {}
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py _view_modes")
            return {}

    def saved_view_mode(self, folder_path: str):
        """そのフォルダに記憶された表示モード。無ければ None（＝既定のリスト）。"""
        if not folder_path:
            return None
        v = self._view_modes().get(self._mode_key(folder_path))
        return v if v in ("list", "thumb") else None

    def remember_view_mode(self, folder_path: str, mode: str):
        """表示モードを覚える。既定（list）は «覚えない» のではなく明示的に
        保存する — «一度サムネにしてから戻した» を次回も再現するため。"""
        sm = getattr(self, "_sm_widths", None)
        if sm is None or not folder_path:
            return
        try:
            d = self._view_modes()
            k = self._mode_key(folder_path)
            d.pop(k, None)                 # 入れ直して «最近使った順» にする
            d[k] = "thumb" if mode == "thumb" else "list"
            if len(d) > self.VIEW_MODE_MAX:
                for old_k in list(d.keys())[:len(d) - self.VIEW_MODE_MAX]:
                    d.pop(old_k, None)
            sm.set(self.VIEW_MODE_KEY, d)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py remember_view_mode")

    def _size_key(self, mode: str) -> str:
        return "column_icon_size_thumb" if mode == "thumb" else "column_icon_size_list"

    def column_item_size(self, view) -> int:
        """そのカラムの表示サイズ(px)。モードごとに別の値を持つ。
        未設定なら設定ファイルの値 → 既定値の順。"""
        mode = getattr(view, "_mfm_view_mode", "list")
        cur = getattr(view, "_mfm_item_size", {}).get(mode) if \
            isinstance(getattr(view, "_mfm_item_size", None), dict) else None
        if cur:
            return int(cur)
        sm = getattr(self, "_sm_widths", None)
        if sm is not None:
            try:
                return int(sm.get(self._size_key(mode), self.DEFAULT_SIZE[mode]))
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:848 column_item_size")
        return self.DEFAULT_SIZE[mode]

    def set_column_item_size(self, view, px: int, save: bool = True):
        """表示サイズを適用する。リスト＝アイコン寸法、サムネイル＝セル寸法。
        同じスライダーで両モードを調整する（ユーザー指示）。"""
        mode = getattr(view, "_mfm_view_mode", "list")
        px = max(8, int(px))
        store = getattr(view, "_mfm_item_size", None)
        if not isinstance(store, dict):
            store = {}
            view._mfm_item_size = store
        store[mode] = px
        try:
            view.setIconSize(QSize(px, px))
            if mode == "thumb":
                view.setGridSize(QSize(px + 18, px + ThumbnailDelegate._TEXT_H + 8))
                if self._thumb_mgr is not None:
                    view.setItemDelegate(ThumbnailDelegate(
                        self._thumb_mgr, px, view,
                        is_expanded_index=self._is_expanded_index))
            else:
                view.setGridSize(QSize())
            view.doItemsLayout()
            view.viewport().update()
            self._reposition_column_header(view)
        except Exception as e:
            _mfm_log("set_column_item_size error: %r" % (e,))
        if save:
            sm = getattr(self, "_sm_widths", None)
            if sm is not None:
                try:
                    sm.set(self._size_key(mode), px)
                except Exception as _e:
                    _swallow(_e, "ui/browser_column_view.py:882 set_column_item_size")

    def set_item_size_all(self, px: int, mode: str = None, save: bool = True):
        """表示サイズを **同じモードの全カラムへ一括適用** する（r102）。

        スライダーは «全体的な表示サイズ» を変えるもの、というユーザー指示。
        モードごとに別の値を持つ（リストは小さく、グリッドは大きくが自然）。
        保存もここで 1 回だけ行い、新しく開くカラムにも効く。"""
        px = max(8, int(px))
        applied = 0
        for v in self._live_columns():
            if mode is not None and getattr(v, "_mfm_view_mode", "list") != mode:
                continue
            self.set_column_item_size(v, px, save=False)
            applied += 1
        if callable(getattr(self, "_item_size_cb", None)):
            try:
                self._item_size_cb(px, mode)      # 平坦／サムネビュー側へ
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:901 set_item_size_all")
        if save:
            sm = getattr(self, "_sm_widths", None)
            if sm is not None:
                try:
                    sm.set(self._size_key(mode or "list"), px)
                except Exception as _e:
                    _swallow(_e, "ui/browser_column_view.py:908 set_item_size_all")
        return applied

    def set_item_size_callback(self, cb):
        """カラム以外のビュー（平坦カラム等）へもサイズ変更を配るコールバック。"""
        self._item_size_cb = cb

    def set_dcc_takeover_callback(self, cb):
        """cb(paths, app) -> bool: DCC への落下を Manager が処理できるか（r90）。"""
        self._dcc_takeover_cb = cb

    def make_drag_mime(self, paths):
        """ドラッグ用の mime を作る（DCC への落下は Manager が引き受ける。r90）。
        判定はドラッグ 1 回につき DCC ごとに 1 度だけ（接続確認のコストを抑える）。"""
        cache = {}
        cb_ref = self

        def decide():
            app = _cursor_over_dcc_window()
            if not app:
                return None, []
            if app not in cache:
                cb = cb_ref._dcc_takeover_cb
                try:
                    r = cb(list(paths), app) if callable(cb) else []
                    # 旧仕様（bool）にも対応
                    cache[app] = (list(paths) if r is True else
                                  [] if not r else list(r))
                except Exception as e:
                    _mfm_log("dcc-takeover check error: %r" % (e,))
                    cache[app] = []
                _mfm_log("dcc-takeover: app=%s take=%d/%d"
                         % (app, len(cache[app]), len(paths)))
            return app, cache[app]

        mime = _DccAwareMime(paths, decide)
        mime._mfm_decide = decide
        return mime

    def set_file_drop_callback(self, cb):
        """カラムへのファイルドロップ cb(paths, dest_dir, move: bool)（r87）。"""
        self._file_drop_cb = cb

    def set_thumb_prefetch_callback(self, cb):
        """サムネイル表示に切り替えたカラムの先読みを依頼する cb(folder)。"""
        self._thumb_prefetch_cb = cb

    def set_flat_callback(self, cb):
        """インライン平坦カラムの表示要求コールバック cb(dirs) を登録。"""
        self._flat_cb = cb

    def _request_flat(self, dirs):
        if callable(self._flat_cb):
            self._flat_cb(list(dirs or []))

    def _reset_flatten_toggle(self):
        """平坦トグルの見た目と内部状態を確実にOFFへ戻す。

        平坦カラムがナビゲーション等で閉じた時に呼ぶ。ONのまま放置すると
        次のボタン押下が«OFF操作»になり「押しても平坦ビューが出ない」
        ように見える（実機報告の症状）。"""
        v = self._flatten_view
        self._flatten_view = None
        if v is not None:
            try:
                v._mfm_flatten = False
                b = getattr(v, "_mfm_flat_btn", None)
                if b is not None:
                    b.setChecked(False)
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:978 _reset_flatten_toggle")

    def _proxy_model(self):
        m = self.model()
        return m if hasattr(m, "set_column_filter") else None

    def _build_column_header(self, view, folder_path):
        """カラム上部に2行のヘッダを重ねる。
        1行目=フィルタ欄／2行目=ソート項目プルダウン＋昇順降順＋平坦化＋表示切替。"""
        proxy = self._proxy_model()
        if proxy is None or not folder_path:
            return
        try:
            view.setViewportMargins(0, self._COL_HEADER_H, 0, 0)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:993 _build_column_header")
        hdr = QWidget(view)
        hdr.setObjectName("mfmColHeader")
        view._mfm_hdr_geo = None          # r120: 作り直したら前回値を捨てる
        # 注意: グローバルQSSの min-height(26px) がヘッダ内の固定20px指定を
        # 上書きし、2行合計がヘッダ予約高(_COL_HEADER_H)を超えて先頭項目に
        # 被った実例あり。ヘッダ内では min-height を必ず明示して打ち消す。
        from core.theme_engine import qss_vars
        hdr.setStyleSheet(
            "#mfmColHeader{background:%(column_header)s;"
            "border-bottom:1px solid %(hairline)s;}"
            "QLineEdit{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:10px;"
            "min-height:18px;max-height:20px;padding:0 8px;}"
            "QLineEdit:focus{border-color:%(primary)s;}"
            "QComboBox{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:10px;"
            "min-height:18px;max-height:20px;padding:0 8px;}"
            "QToolButton{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:9px;"
            "min-height:16px;max-height:18px;padding:0px 6px;}"
            "QToolButton:hover{background:%(fill_subtle_hover)s;}"
            "QToolButton:checked{background:%(cta_tint)s;color:%(on_primary_container)s;"
            "border-color:%(primary)s;}"
            % qss_vars()
        )
        vlay = QVBoxLayout(hdr)
        vlay.setContentsMargins(3, 3, 3, 3)
        vlay.setSpacing(3)
        # --- 1行目: フィルタ ＋ 排他フィルタ ---
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(3)
        edit = QLineEdit(hdr)
        edit.setPlaceholderText(tr("フィルタ", "Filter"))
        edit.setClearButtonEnabled(True)
        edit.setFixedHeight(20)
        edit.setText(proxy.get_column_filter(folder_path))
        edit.textChanged.connect(
            lambda t, p=folder_path: proxy.set_column_filter(p, t))
        excl = QLineEdit(hdr)
        excl.setPlaceholderText(tr("排他", "Exclude"))
        excl.setClearButtonEnabled(True)
        excl.setFixedHeight(20)
        excl.setToolTip(tr("入力に一致するファイルを一覧から除外",
                           "Hide files matching this text"))
        excl.setText(proxy.get_column_exclude(folder_path))
        excl.textChanged.connect(
            lambda t, p=folder_path: proxy.set_column_exclude(p, t))
        row1.addWidget(edit, 1)
        row1.addWidget(excl, 1)
        vlay.addLayout(row1)
        # --- 2行目: ソート項目 + 昇順降順 + 平坦化 + 表示 ---
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(3)
        cur_key, cur_asc = proxy.get_column_sort(folder_path)
        sort_combo = QComboBox(hdr)
        sort_combo.setFixedHeight(20)
        for key, label in self._SORT_KEYS:
            sort_combo.addItem(label, key)
        for i in range(sort_combo.count()):
            if sort_combo.itemData(i) == cur_key:
                sort_combo.setCurrentIndex(i)
                break
        order_btn = QToolButton(hdr)
        order_btn.setCheckable(True)
        order_btn.setFixedSize(26, 20)
        order_btn.setChecked(not cur_asc)            # checked=降順
        order_btn.setText("▲" if cur_asc else "▼")
        order_btn.setToolTip(tr("昇順／降順", "Ascending / Descending"))

        def _apply_sort(p=folder_path, c=sort_combo, b=order_btn):
            key = c.currentData()
            asc = not b.isChecked()
            b.setText("▲" if asc else "▼")
            if proxy:
                proxy.set_column_sort(p, key, asc)
        sort_combo.currentIndexChanged.connect(lambda _i: _apply_sort())
        order_btn.clicked.connect(lambda _c=False: _apply_sort())

        flat_btn = QToolButton(hdr)
        flat_btn.setText(tr("平坦", "Flat"))
        flat_btn.setCheckable(True)
        flat_btn.setChecked(getattr(view, "_mfm_flatten", False))
        flat_btn.setFixedHeight(20)
        flat_btn.setToolTip(tr("選択中フォルダ以下の全ファイルを平坦表示"
                               "（選択が無ければこのカラムのフォルダ全体）",
                               "Show all files under the selected folder as a "
                               "flat list (whole column folder if nothing is "
                               "selected)"))

        def _on_flat_btn(checked, v=view, p=folder_path):
            # 状態変化の到達を最初に必ず記録（不達調査用）
            _mfm_log("flat_btn toggled: checked=%s col=%r" % (checked, p))
            try:
                self._toggle_flatten(v, checked, p)
            except Exception as e:
                _mfm_log("flat_btn ERROR: %r" % (e,))

        # clicked ではなく toggled を使う。toggled はチェック状態の変化
        # そのもので発火するため、ボタンの見た目が切り替わる限り必ず届く
        # （実機で clicked が slot に届かない事象への対策）
        flat_btn.toggled.connect(_on_flat_btn)
        view._mfm_flat_btn = flat_btn
        view_btn = QToolButton(hdr)
        view_btn.setText("▦")
        view_btn.setFixedSize(26, 20)
        # r103: 長いツールチップがスライダーと同じ位置に出て隠していたため短くする
        view_btn.setToolTip(tr("リスト⇄サムネイル切替",
                               "Toggle list/thumbnail view (hover for item size)"))
        view_btn.clicked.connect(lambda _c=False, v=view: self._toggle_view_mode(v))
        # r85: ボタンにマウスオーバー → 表示サイズのスライダーを出す
        _hover = _SizeButtonHover(self, view, view_btn)
        view_btn.installEventFilter(_hover)
        view_btn._mfm_size_hover = _hover     # GC 防止

        row2.addWidget(sort_combo, 1)
        row2.addWidget(order_btn, 0)
        row2.addWidget(flat_btn, 0)
        row2.addWidget(view_btn, 0)
        vlay.addLayout(row2)
        view._mfm_header = hdr
        self._install_display_name_footer(view, folder_path)
        self._reposition_column_header(view)
        hdr.show()
        hdr.raise_()

    _COL_FOOTER_H = 24

    def _install_display_name_footer(self, view, folder_path):
        """表示名の設定ファイルがあるカラムにだけ、下部へ操作列を出す（r115）。

        «表示名が変わっている印» を兼ねる。ファイルが無ければ作らない
        （＝通常のフォルダでは一切コストが掛からない）。"""
        from core import display_names
        if not folder_path or not display_names.has_file(folder_path):
            view._mfm_dn_footer = None
            view._mfm_dn_switch = None
            view._mfm_ft_geo = None       # r120
            return
        ft = QWidget(view)
        ft.setObjectName("mfmDnFooter")
        # r120: 位置の «前回値» は作り直したら捨てる。ウィジェットが別物に
        # なっているので、同じ寸法でも配置と見出しの省略をやり直す必要がある
        # （見出しだけ変えた時に反映されなくなっていた）。
        view._mfm_ft_geo = None
        from core.theme_engine import qss_vars
        tv = qss_vars()
        ft.setStyleSheet(
            "#mfmDnFooter{background:%(plane_flat_header)s;"
            "border-top:1px solid %(hairline)s;}"
            "QLabel{color:%(on_surface_variant)s;font-size:%(label_px)spx;}"
            "QLabel#mfmColTitle{color:%(on_surface)s;font-weight:%(w_strong)s;}"
            "QToolButton{background:%(fill_subtle)s;color:%(on_surface)s;"
            "border:1px solid %(hairline)s;border-radius:9px;"
            "min-height:16px;max-height:18px;padding:0px 6px;}"
            "QToolButton:hover{background:%(fill_subtle_hover)s;}"
            "QToolButton:checked{background:%(cta_tint)s;"
            "border-color:%(primary)s;}" % tv)
        row = QHBoxLayout(ft)
        row.setContentsMargins(6, 2, 4, 2)
        row.setSpacing(4)
        # r119: 見出しは «左詰め»、操作ボタンは «右詰め»。
        # r119h: 見出しとは別に addStretch を入れていたため、空きの半分を
        # スペーサーに取られて見出しが «アニメーショ» のように切れていた
        # （ユーザー報告 2026-10-02）。見出しがある時は見出し自身に空きを
        # 全部渡す（＝「表示名」ボタンの直前まで使う）。
        ttl = display_names.title(folder_path)
        if ttl:
            lb = QLabel(ttl, ft)
            lb.setObjectName("mfmColTitle")
            lb.setToolTip(ttl)
            # 入り切らない時だけ末尾を省略する（ボタンは縮ませない）
            lb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lb.setMinimumWidth(0)
            lb.setTextFormat(Qt.PlainText)
            lb._mfm_full_title = ttl
            row.addWidget(lb, 1)
            view._mfm_dn_title = lb
        else:
            view._mfm_dn_title = None
            row.addStretch(1)      # 見出しが無い時だけボタンを右へ押す
        sw = QToolButton(ft)
        sw.setCheckable(True)
        sw.setChecked(display_names.is_enabled(folder_path))
        sw.setText(tr("表示名", "Alias"))
        sw.setToolTip(tr("表示名の有効／無効", "Enable / disable display names"))
        # r119: toggled だとプログラムからの setChecked でも発火し、
        # 再入で «押せていない» ように見えた。利用者の操作だけを拾う。
        sw.clicked.connect(
            lambda on, d=folder_path: self._on_display_names_toggled(d, on))
        row.addWidget(sw, 0)
        cfg = QToolButton(ft)
        cfg.setText("⚙")
        cfg.setToolTip(tr("表示名の設定を開く", "Open display-name settings"))
        cfg.clicked.connect(
            lambda _c=False, d=folder_path: self._open_display_name_dialog(d))
        row.addWidget(cfg, 0)
        view._mfm_dn_footer = ft
        view._mfm_dn_switch = sw
        ft.show()
        ft.raise_()
        self._elide_column_title(view)

    def _elide_column_title(self, view):
        """見出しを «使える幅いっぱい» で表示し、足りない分だけ末尾を省略する。

        QLabel は自前で省略してくれないので、幅が変わるたびにここで作る。
        幅は «ボタンの手前まで» 全部使う（r119h）。"""
        lb = getattr(view, "_mfm_dn_title", None)
        if lb is None:
            return
        try:
            full = getattr(lb, "_mfm_full_title", "") or lb.text()
            avail = max(0, lb.width())
            fm = lb.fontMetrics()
            lb.setText(full if fm.horizontalAdvance(full) <= avail
                       else fm.elidedText(full, Qt.ElideRight, avail))
            lb.setToolTip(full)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py _elide_column_title")

    def _on_display_names_toggled(self, folder_path, on):
        """有効／無効スイッチ。

        r119: 以前はここから _refresh_display_names() を呼び、**押した当の
        ボタンごとフッタを作り直して** いた。保存が失敗していると作り直しで
        元の状態に戻るため «押しても反応しない» ように見えた（実際には
        隠し属性のせいで保存が毎回失敗していた）。
        フッタは作り直さず、保存できなければ戻した理由を必ず伝える。"""
        from core import display_names
        if not display_names.set_enabled(folder_path, bool(on)):
            sw = None
            for v in self._live_columns():
                if self._path_for_index(v.rootIndex()) == folder_path:
                    sw = getattr(v, "_mfm_dn_switch", None)
                    break
            if sw is not None:
                sw.blockSignals(True)
                sw.setChecked(not bool(on))
                sw.blockSignals(False)
            from core.compat import QMessageBox
            QMessageBox.warning(
                self.window(), tr("表示名", "Display Names"),
                tr("表示名の設定を保存できませんでした:\n%s\n\n"
                   "書き込み権限や、ファイルが読み取り専用になっていないかを"
                   "確認してください。",
                   "Could not save the display-name settings:\n%s\n\n"
                   "Check write permissions and whether the file is "
                   "read-only.")
                % os.path.join(folder_path, display_names.FILE_NAME))
            return
        self._refresh_display_names(rebuild_footers=False)

    def _open_display_name_dialog(self, folder_path):
        """表示名の編集（r119: 非モーダル）。

        «カラムの見た目を確かめながら名前を決める» 窓なので、
        開いている間マネージャーを触れないのは本末転倒。"""
        from ui.display_name_dialog import DisplayNameDialog
        from ui.dialog_util import show_tool_window

        def _make(d=folder_path):
            dlg = DisplayNameDialog(d, parent=self.window())
            dlg.changed.connect(lambda _d: self._refresh_display_names())
            return dlg
        show_tool_window(self, "_mfm_dn_dialog", _make)

    def _refresh_display_names(self, rebuild_footers=True):
        """対応表を捨てて全カラムを描き直す。

        rebuild_footers=False は «フッタのボタン自身から呼ばれた» 場合用。
        押したボタンを作り直すと、そのクリックの処理中にウィジェットが
        消えることになる（r119）。
        """
        from core import display_names
        m = self.model()
        if hasattr(m, "invalidate_display_names"):
            m.invalidate_display_names()
        # r120: «表示名» 順で並べているカラムは、名前を変えたら並びも変わる
        if hasattr(m, "resort_columns"):
            m.resort_columns()
        for v in self._live_columns():
            try:
                if rebuild_footers:
                    ft = getattr(v, "_mfm_dn_footer", None)
                    if ft is not None:
                        ft.setParent(None)
                        ft.deleteLater()
                        v._mfm_dn_footer = None
                    self._install_display_name_footer(
                        v, self._path_for_index(v.rootIndex()))
                    self._reposition_column_header(v)
                else:
                    sw = getattr(v, "_mfm_dn_switch", None)
                    if sw is not None:
                        d = self._path_for_index(v.rootIndex())
                        sw.blockSignals(True)
                        sw.setChecked(display_names.is_enabled(d))
                        sw.blockSignals(False)
                v.viewport().update()
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py _refresh_display_names")

    def _toggle_view_mode(self, view):
        cur = getattr(view, "_mfm_view_mode", "list")
        self._set_column_view_mode(view, "thumb" if cur == "list" else "list",
                                   remember=True)

    def _toggle_flatten(self, view, on, folder_path=None):
        """このカラムの平坦化トグル。ON時はそのカラムの選択フォルダ（無ければ
        そのカラム自身のフォルダ）以下を平坦表示する。平坦は «1カラムのみ» 排他。

        toggled シグナル経由の再入（排他OFFやリセットの setChecked）にも
        安全な構造にしてある。"""
        _mfm_log("toggle_flatten ENTER: on=%s folder=%r" % (on, folder_path))
        if on:
            # 排他制御: 他カラムの平坦ボタンがONなら OFF にする。
            # setChecked(False) が toggled 経由で OFF 処理を正しく走らせる
            prev = self._flatten_view
            if prev is not None and prev is not view:
                self._flatten_view = None
                pb = getattr(prev, "_mfm_flat_btn", None)
                try:
                    if pb is not None:
                        pb.setChecked(False)
                    else:
                        prev._mfm_flatten = False
                except Exception as _e:
                    _swallow(_e, "ui/browser_column_view.py:1144 _toggle_flatten")
            self._flatten_view = view
            view._mfm_flatten = True
            dirs = self._column_selected_dirs(view)
            if not dirs and folder_path and os.path.isdir(folder_path):
                dirs = [folder_path]   # 選択無し→このカラムのフォルダ自身
            _mfm_log("toggle_flatten ON: dirs=%r cb=%s"
                     % ([os.path.basename(d) for d in dirs],
                        callable(self._flat_cb)))
            if dirs:
                self._request_flat(dirs)
            else:
                _mfm_log("toggle_flatten: NO TARGET (folder_path=%r)"
                         % (folder_path,))
        else:
            was_active = (self._flatten_view is view)
            if was_active:
                self._flatten_view = None
            view._mfm_flatten = False
            _mfm_log("toggle_flatten OFF: was_active=%s" % was_active)
            # このカラムが平坦の発生源だった時だけ閉じる（排他OFFや
            # リセット経由の再入では、新しい平坦表示を巻き添えにしない）
            if was_active:
                self._request_flat([])

    def _set_column_view_mode(self, view, mode, remember=False):
        """そのカラムをリスト／サムネイル表示に切り替える。

        remember=True で、そのフォルダの表示モードとして記憶する（r119）。
        «復元して適用する» 時に呼ぶ分では保存しない（同じ値を書き直すだけで
        «最近使った順» が壊れるため）。"""
        try:
            if remember:
                self.remember_view_mode(self._path_for_index(view.rootIndex()),
                                        mode)
            if mode == "thumb":
                view._mfm_view_mode = "thumb"
                view.setViewMode(QListView.IconMode)
                view.setResizeMode(QListView.Adjust)
                view.setWrapping(True)
                view.setSpacing(6)
                # r85: セル寸法はスライダーの保存値から（既定 96px）
                self.set_column_item_size(view, self.column_item_size(view),
                                          save=False)
                # r83: 先読みはサムネ表示に «してから» 行う（リスト表示では走らせない）。
                # r84: 先読みは BrowserPanel 側の処理なのでコールバック経由で呼ぶ
                # （CappedColumnView には _prefetch_thumbs_async は無い）。
                if callable(self._thumb_prefetch_cb):
                    folder = self._path_for_index(view.rootIndex())
                    if folder:
                        self._thumb_prefetch_cb(folder)
            else:
                view._mfm_view_mode = "list"
                view.setViewMode(QListView.ListMode)
                view.setWrapping(False)
                view.setSpacing(0)
                view.setGridSize(QSize())
                view.setItemDelegate(StatusBadgeDelegate(self._path_of_index, view,
                                                is_dir_of_index=self._is_dir_index,
                                                is_expanded_index=self._is_expanded_index))
                # r85: リスト表示のアイコン寸法もスライダーの保存値から
                self.set_column_item_size(view, self.column_item_size(view),
                                          save=False)
            self._reposition_column_header(view)
            # r104: 開いたままのスライダーを新しいモードのレンジ／値へ更新する
            sp = getattr(self, "_size_popup", None)
            if sp is not None and sp.isVisible() and sp._view is view \
                    and sp._btn is not None:
                sp.show_for(view, sp._btn)
        except Exception as e:
            _mfm_warn("set_column_view_mode(%s) failed: %r" % (mode, e))

    def _column_selected_dirs(self, view):
        """そのカラムで選択中のフォルダのフルパス一覧（マージ対象）。"""
        out = []
        try:
            m = self.model()
            for idx in view.selectedIndexes():
                # そのカラムに «見えている» 行だけを数える。選択モデルは
                # モデル全体を張るため、他階層の不可視選択が混入し得る
                if idx.column() != 0 or idx.parent() != view.rootIndex():
                    continue
                src = idx
                sm = m
                while hasattr(sm, "mapToSource"):
                    src = sm.mapToSource(src)
                    sm = sm.sourceModel()
                fp = _safe_file_path(sm, src) if hasattr(sm, "filePath") else ""
                if fp and os.path.isdir(fp) and fp not in out:
                    out.append(fp)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:1228 _column_selected_dirs")
        return out

    def _deepest_selected_dirs(self):
        """表示中カラム全体から、選択されているフォルダをすべて返す。

        重要: QColumnView はカレントまでの祖先を各カラムで選択表示する
        （ナビゲーション連鎖）。この連鎖や選択スナップショット経由で
        «他の選択フォルダの祖先» が混入すると、平坦結果が祖先ツリー全体
        （数千ファイル）に化けて事実上フリーズするため、必ず除外する。"""
        out = []
        if self._selected_dir_paths:
            out = [p for p in sorted(self._selected_dir_paths) if os.path.isdir(p)]
        else:
            try:
                # パンくず（現在位置までの祖先チェーン）は «選択フォルダ» では
                # ないため除外する。含めると空白クリック解除後も平坦ビューが
                # 親フォルダで居座る
                cur_nc = ""
                try:
                    cur = self.currentIndex()
                    cp = self._path_of_index(cur) if cur.isValid() else ""
                    cur_nc = os.path.normcase(os.path.normpath(cp)) if cp else ""
                except Exception:
                    cur_nc = ""
                for view in self.findChildren(QListView):
                    if not hasattr(view, "selectedIndexes"):
                        continue
                    for idx in view.selectedIndexes():
                        if idx.column() != 0 or idx.parent() != view.rootIndex():
                            continue
                        fp = self._path_of_index(idx)
                        if not (fp and os.path.isdir(fp)):
                            continue
                        if cur_nc:
                            nf = os.path.normcase(os.path.normpath(fp))
                            if cur_nc == nf or cur_nc.startswith(nf + os.sep):
                                continue
                        if fp not in out:
                            out.append(fp)
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:1269 _deepest_selected_dirs")
        return self._drop_ancestor_dirs(out)

    @staticmethod
    def _drop_ancestor_dirs(dirs):
        """他の選択フォルダの祖先（親・先祖）にあたるパスを除外して返す。
        例: {tmp, tmp/A, tmp/B} → {tmp/A, tmp/B}（最深のみ残す）。"""
        try:
            norm = {p: os.path.normcase(os.path.normpath(p)) for p in dirs}
            res = []
            for p in dirs:
                base = norm[p] + os.sep
                if any(o != p and norm[o].startswith(base) for o in dirs):
                    continue
                res.append(p)
            return res
        except Exception:
            return list(dirs)

    def _selection_snapshot(self, exclude_view=None, exclude_ancestors_of=None):
        """指定カラム以外の選択状態を保持する。

        exclude_ancestors_of: このパスの祖先（＝操作カラムまでのパンくず）は
        スナップに入れない。入れてしまうと、Ctrlトグルで最後の1件を解除した
        直後にパンくずが「選択フォルダ」として復活し、解除できない／
        勝手に平坦ビューが出る誤動作になる。"""
        snap = set()
        anc_nc = ""
        if exclude_ancestors_of:
            try:
                anc_nc = os.path.normcase(os.path.normpath(exclude_ancestors_of))
            except Exception:
                anc_nc = ""
        try:
            for view in self.findChildren(QListView):
                if view is exclude_view or not hasattr(view, "selectedIndexes"):
                    continue
                for idx in view.selectedIndexes():
                    # 不可視選択（他階層の残骸）はスナップに入れない。
                    # これが混入すると、Ctrlトグルで解除した項目が
                    # スナップ復元で選択に戻る（解除できない不具合の原因）
                    if (idx.isValid() and idx.column() == 0
                            and idx.parent() == view.rootIndex()):
                        fp = self._path_of_index(idx)
                        if not (fp and os.path.isdir(fp)):
                            continue
                        if anc_nc:
                            nf = os.path.normcase(os.path.normpath(fp))
                            if anc_nc == nf or anc_nc.startswith(nf + os.sep):
                                continue   # 操作カラムへのパンくず → 除外
                        snap.add(fp)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:1321 _selection_snapshot")
        return snap

    def _restore_selection_snapshot(self, snap):
        """下階層操作で消えた上位カラムの複数選択を戻す。

        QColumnView はカレントまでの祖先を各カラムで選択表示するため、
        スナップショットにナビゲーション連鎖（祖先）が紛れ込む。統合時に
        «他の選択フォルダの祖先» を追跡から落とし、蓄積汚染を防ぐ。"""
        if snap:
            # 現在追跡中フォルダの«子孫»にあたるスナップは持ち込まない
            # （新しい選択系譜が勝つ。旧下位選択の残骸が復活すると、
            #   祖先除外で新選択自体が結果から落ちる＝動画の症状）
            cur = [os.path.normcase(os.path.normpath(d))
                   for d in self._selected_dir_paths]

            def _under_cur(p):
                np_ = os.path.normcase(os.path.normpath(p))
                return any(np_.startswith(c + os.sep) for c in cur)

            merged = set(self._selected_dir_paths)
            merged.update(p for p in snap
                          if os.path.isdir(p) and not _under_cur(p))
            self._selected_dir_paths = set(self._drop_ancestor_dirs(sorted(merged)))
        self._restore_tracked_selection()

    def _restore_tracked_selection(self):
        QISM = _QtCore.QItemSelectionModel
        paths = [p for p in self._selected_dir_paths if os.path.isdir(p)]
        indexes = [self._proxy_index_for_path(p) for p in paths]
        indexes = [i for i in indexes if i.isValid()]
        # パンくず（現在位置までの祖先チェーン）も全モデルへ焼き込む。
        # QColumnView はカラム再構築時に本体モデルの選択から複製するため、
        # 本体側に連鎖選択が無いと «一つ上の階層の選択が外れる»。
        try:
            cur = self.currentIndex()
            root = self.rootIndex()
            i = cur
            while i.isValid() and i != root:
                indexes.append(i)
                i = i.parent()
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:1363 _restore_tracked_selection")
        if not indexes:
            return
        targets = [v.selectionModel() for v in self.findChildren(QListView)]
        targets.append(self.selectionModel())  # 本体（再構築時の種）にも反映
        for sm in targets:
            try:
                if sm is None:
                    continue
                sel = _QtCore.QItemSelection()
                for idx in indexes:
                    sel.select(idx, idx)
                if not sel.isEmpty():
                    sm.select(sel, QISM.Select | QISM.Rows)
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:1378 _restore_tracked_selection")
        for view in self.findChildren(QListView):
            try:
                view.viewport().update()
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:1383 _restore_tracked_selection")

    def _restore_selection_snapshot_later(self, snap):
        if not snap:
            return
        QTimer.singleShot(0, lambda s=snap: self._restore_selection_snapshot(s))
        QTimer.singleShot(80, lambda s=snap: self._restore_selection_snapshot(s))

    def _proxy_index_for_path(self, path):
        try:
            m = self.model()
            sm = m.sourceModel() if hasattr(m, "sourceModel") else m
            src = sm.index(path) if hasattr(sm, "index") else QModelIndex()
            return m.mapFromSource(src) if hasattr(m, "mapFromSource") else src
        except Exception:
            return QModelIndex()

    @staticmethod
    def _norm_parent(path):
        try:
            return os.path.normcase(os.path.normpath(os.path.dirname(path)))
        except Exception:
            return ""

    def _sync_tracked_selection_for_view(self, view):
        """現在操作中のカラムだけ追跡状態を更新し、他カラムは触らない。"""
        dirs = self._column_selected_dirs(view)
        parents = {self._norm_parent(d) for d in dirs}
        if not parents:
            try:
                idx = view.currentIndex()
                fp = self._path_of_index(idx)
                parent = self._norm_parent(fp) if fp else ""
                parents = {parent} if parent else set()
            except Exception:
                parents = set()
        if parents:
            self._selected_dir_paths = {
                p for p in self._selected_dir_paths
                if self._norm_parent(p) not in parents
            }
        # 新しく選択したフォルダの«子孫»や«祖先»にあたる古い追跡は破棄する。
        # 例: 以前 Assets/CHR 等を選択 → 今回 Assets〜Tools を範囲選択した場合、
        # CHR の残骸が残ると祖先除外で Assets 自体が結果から落ち、平坦カラムが
        # 古い内容のまま更新されない（動画の症状）。新選択の系譜が常に勝つ。
        if dirs:
            norm_new = [os.path.normcase(os.path.normpath(d)) for d in dirs]

            def _is_desc(p):
                np_ = os.path.normcase(os.path.normpath(p))
                return any(np_.startswith(nd + os.sep) for nd in norm_new)

            def _is_anc(p):
                np_ = os.path.normcase(os.path.normpath(p))
                return any(nd.startswith(np_ + os.sep) for nd in norm_new)

            removed_desc = {p for p in self._selected_dir_paths if _is_desc(p)}
            removed_anc = {p for p in self._selected_dir_paths
                           if p not in removed_desc and _is_anc(p)}
            self._selected_dir_paths -= (removed_desc | removed_anc)
            # 子孫の残骸はハイライトも外す。祖先（パンくず）は追跡からのみ
            # 外し、見た目の選択は温存する（「一つ上の階層の選択が外れる」
            # 不具合の修正）
            if removed_desc:
                self._deselect_paths(removed_desc)
        self._selected_dir_paths.update(dirs)

    @staticmethod
    def _scrollbar_extent(view) -> int:
        """そのビューが縦スクロールバーに割く幅（表示していなくても同じ値）。

        r105: 掴む位置をスクロールバーの左に固定するために使う。可視状態で
        計算すると «スクロールバーが出た瞬間にハンドルがずれる» ので、
        常に確保幅で計算する。

        r120: 値はテーマ/スタイルで決まり、ビューごとには変わらない。
        スライド中に «カラム数 × 毎フレーム» 呼ばれる場所なので一度で覚える
        （sizeHint はスタイルへ問い合わせるので、ただではない）。"""
        cached = getattr(CappedColumnView, "_sb_extent_cache", None)
        if cached:
            return cached
        try:
            sb = view.verticalScrollBar()
            w = sb.sizeHint().width() if sb is not None else 0
            if w <= 0:
                w = sb.width() if sb is not None else 0
        except Exception:
            w = 0
        if w <= 0:
            try:
                w = QApplication.style().pixelMetric(QStyle.PM_ScrollBarExtent)
            except Exception:
                w = 16
        w = max(0, int(w))
        if w > 0:
            CappedColumnView._sb_extent_cache = w
        return w

    def _reposition_column_handle(self, view):
        """リサイズハンドルだけを追従させる（r120）。

        **カラムが «動く» だけで位置が変わるのはハンドルだけ。** ハンドルは
        ColumnView のビューポートの子なので、カラムが動けば付いていく必要が
        ある。ヘッダとフッタは «カラム自身の子» なので、カラムが動いても
        カラム内での座標は変わらない。

        QColumnView のスライドはカラムを 1 フレームごとに move する。
        従来はその度にヘッダ・フッタ・ハンドルを全部置き直し、さらに
        raise_() と見出しの省略計算まで走らせていた。カラム 5 本 × 60fps で
        毎秒数百回になり、これがスライドのカクつきの正体だった
        （ユーザー報告 2026-10-02）。
        """
        handle = getattr(view, "_mfm_resize_handle", None)
        if handle is None:
            return
        try:
            # r105: **必ず縦スクロールバーより左** に置く。
            # 従来はカラム右辺を «またぐ» 帯だったため、右端のカラムでは
            # 掴む場所が縦スクロールバーと重なり、掴み損ねてスクロールバーを
            # 動かす／誤って幅を変えて戻せない、という事故になっていた
            # （ユーザー報告 2026-09-30）。
            # 幅はスクロールバーの «表示有無によらず» 確保している分で計算し、
            # 出たり消えたりしても掴む位置が動かないようにする。
            hw = _ColumnResizeHandle.WIDTH
            sb_w = self._scrollbar_extent(view)
            x = view.x() + view.width() - sb_w - hw
            x = max(view.x(), x)          # 極端に細い時でもカラム内に収める
            geo = (x, view.y() + self._COL_HEADER_H, hw,
                   max(1, view.height() - self._COL_HEADER_H))
            vis = view.isVisible()
            if getattr(view, "_mfm_handle_geo", None) == (geo, vis):
                return                    # 変わっていないなら何もしない
            view._mfm_handle_geo = (geo, vis)
            handle.setGeometry(*geo)
            handle.setVisible(vis)
            handle.raise_()
        except RuntimeError:
            pass

    def _reposition_column_header(self, view):
        """ヘッダ・フッタ・ハンドルを配置し直す。

        r120: **同じ位置・同じ大きさなら何もしない。** setGeometry /
        raise_ / setVisible はどれも «呼べば仕事をする» ので、同じ値で
        呼び続けるとレイアウトと再スタックが毎回走る。
        """
        hdr = getattr(view, "_mfm_header", None)
        if hdr is None:
            return
        # ビューポート基準で配置する。view.width() を使うと縦スクロールバーや枠の
        # 下に ⚙ が潜って極小化・クリップされる（カラムに収まらない原因）。
        try:
            vp = view.viewport()
            x = vp.x()
            w = vp.width()
        except Exception:
            x, w = 0, view.width()
        hdr_geo = (x, 0, max(40, w), self._COL_HEADER_H)
        if getattr(view, "_mfm_hdr_geo", None) != hdr_geo:
            view._mfm_hdr_geo = hdr_geo
            hdr.setGeometry(*hdr_geo)
            hdr.raise_()
        # r115: 表示名フッタはカラムの最下部に貼り付ける
        ft = getattr(view, "_mfm_dn_footer", None)
        if ft is not None:
            try:
                ft_geo = (x, max(0, view.height() - self._COL_FOOTER_H),
                          max(40, w), self._COL_FOOTER_H)
                vis = view.isVisible()
                prev = getattr(view, "_mfm_ft_geo", None)
                if prev != (ft_geo, vis):
                    view._mfm_ft_geo = (ft_geo, vis)
                    ft.setGeometry(*ft_geo)
                    ft.setVisible(vis)
                    ft.raise_()
                    # 幅が変わった時だけ見出しの省略を作り直す（r119h / r120）。
                    # 文字幅の計算は安くないので、毎フレーム走らせない。
                    if prev is None or prev[0][2] != ft_geo[2]:
                        self._elide_column_title(view)
            except RuntimeError:
                pass
        self._reposition_column_handle(view)

    # ------------------------------------------------------------------
    # カラム幅（ユーザー変更・保存・自動調整）
    # ------------------------------------------------------------------

    def _integrations(self):
        """IntegrationManager（遅延取得。状態更新時に全カラムを再描画）。"""
        mgr = getattr(self, "_integ_mgr", None)
        if mgr is None:
            from core.integrations import get_manager
            mgr = get_manager()
            self._integ_mgr = mgr
            try:
                mgr.status_updated.connect(self._on_integration_status)
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:1523 _integrations")
        return mgr

    def _on_integration_status(self, _directory: str):
        try:
            for v in self.findChildren(QListView):
                v.viewport().update()
        except RuntimeError:
            pass

    def set_settings(self, sm):
        """幅の保存先（SettingsManager）を登録し、保存済みの幅を適用する。"""
        self._sm_widths = sm
        try:
            saved = sm.get("column_widths", None)
            if isinstance(saved, list) and saved:
                widths = [max(_ColumnResizeHandle.MIN_W,
                              min(_ColumnResizeHandle.MAX_W, int(w)))
                          for w in saved]
            else:
                widths = [self.DEFAULT_COLUMN_W] * 12
            self.setColumnWidths(widths)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:1546 set_settings")

    DEFAULT_COLUMN_W = 300   # 既定幅（従来256。名前の見切れが多いため拡大）

    def _column_views_sorted(self):
        cols = [v for v in self.findChildren(QListView)
                if getattr(v, "_mfm_header", None) is not None and v.isVisible()]
        cols.sort(key=lambda v: v.x())
        return cols

    def set_column_width_for_view(self, view, width: int):
        """指定カラム（表示位置基準）の幅を変更する。"""
        try:
            cols = self._column_views_sorted()
            if view not in cols:
                return
            i = cols.index(view)
            widths = list(self.columnWidths())
            fill = widths[-1] if widths else self.DEFAULT_COLUMN_W
            while len(widths) <= i:
                widths.append(fill)
            widths[i] = int(width)
            self.setColumnWidths(widths)
            # setColumnWidths はカラム自体の幅は変えるが右側カラムの再配置を
            # 行わない（縮めると隙間、広げると重なる）。再配置は «Qt自身の
            # doLayout»（resizeEvent 経由）に任せる。自前で setGeometry すると
            # 非表示カラムやスクロールオフセットと食い違い、カラム群が右へ
            # ずれて左に空白ができる致命的状態になった（2026-09 実機）。
            self._relayout_columns()
            for v in cols:
                self._reposition_column_header(v)
        except Exception as e:
            _mfm_log("set_column_width error: %r" % (e,))

    def _relayout_columns(self):
        """Qt 内部の doLayout（カラムを左詰めで連続配置）を resizeEvent 経由で
        起動する。QColumnView は resizeEvent で doLayout + スクロール範囲更新を
        行うため、同サイズの ResizeEvent を送るのが最も安全な再配置手段。"""
        try:
            from core.compat import QtGui as _QtGui
            ev = _QtGui.QResizeEvent(self.size(), self.size())
            # QColumnView::resizeEvent = doLayout() + updateScrollbars()
            self.resizeEvent(ev)
            # Qt の updateScrollbars は「右に空白ができる過スクロール状態」を
            # そのまま温存する（visibleLength=min(総幅+offset, 表示幅) のため、
            # 縮めた直後は最大値が減らない）。右端に空白があれば左へ詰め、
            # 総幅が表示幅以下なら先頭へ戻し、範囲を再計算させる（r54）。
            if self._normalize_hscroll():
                self.resizeEvent(_QtGui.QResizeEvent(self.size(), self.size()))
        except Exception as e:
            _mfm_log("relayout error: %r" % (e,))

    def _normalize_hscroll(self) -> bool:
        """右側の空白（過スクロール）を解消する。値を変えたら True。"""
        try:
            cols = self._column_views_sorted()
            hbar = self.horizontalScrollBar()
            if not cols or hbar is None:
                return False
            vw = self.viewport().width()
            total = sum(v.width() for v in cols)
            right = cols[-1].x() + cols[-1].width()
            if total <= vw:
                new_val = 0
            elif right < vw:
                new_val = max(0, hbar.value() - (vw - right))
            else:
                return False
            if new_val == hbar.value():
                return False
            hbar.setValue(new_val)
            return True
        except Exception:
            return False

    def _check_column_layout_health(self):
        """レイアウト自己修復（150ms毎）。先頭カラムの左に空白がある＝カラム群が
        右へずれた状態は、スクロールしても見えない階層が生じる致命的な症状。
        原因を問わず、検知したら水平スクロールを先頭に戻し Qt の doLayout で
        左詰めに再配置する。"""
        try:
            cols = [v for v in self.findChildren(QListView)
                    if getattr(v, "_mfm_header", None) is not None
                    and v.isVisible()]
            if not cols:
                return
            first_x = min(v.x() for v in cols)
            hbar = self.horizontalScrollBar()
            # 先頭カラムは常に x<=0（スクロール分だけ負）。正の空白は異常。
            if first_x > 2:
                _mfm_log("layout repair: first column x=%d hbar=%s/%s"
                         % (first_x, hbar.value() if hbar else "-",
                            hbar.maximum() if hbar else "-"))
                _mfm_slow_note("LAYOUT-REPAIR first_x=%d" % first_x)
                if hbar is not None:
                    hbar.setValue(hbar.minimum())
                # 先頭カラムを原点へ寄せてから Qt に連続配置させる
                shift = first_x
                for v in sorted(cols, key=lambda v: v.x()):
                    v.move(v.x() - shift, v.y())
                self._relayout_columns()
                for v in cols:
                    self._reposition_column_header(v)
        except RuntimeError:
            pass
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:1652 _check_column_layout_health")

    def persist_column_widths(self):
        sm = getattr(self, "_sm_widths", None)
        if sm is None:
            return
        try:
            sm.set("column_widths", [int(w) for w in self.columnWidths()])
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:1661 persist_column_widths")

    def fit_width_for_view(self, view) -> int:
        """カラム内の項目名が収まる幅を計算する（最大400件をサンプル）。"""
        try:
            m = view.model()
            root = view.rootIndex()
            fm = view.fontMetrics()
            n = min(m.rowCount(root), 400)
            longest = 0
            for r in range(n):
                idx = m.index(r, 0, root)
                s = idx.data() or ""
                longest = max(longest, fm.horizontalAdvance(str(s)))
            # アイコン + 余白 + 展開矢印 + スクロールバー
            return max(_ColumnResizeHandle.MIN_W,
                       min(_ColumnResizeHandle.MAX_W, longest + 24 + 16 + 20 + 14))
        except Exception:
            return 0

    def _path_for_index(self, index):
        """そのカラムが表示しているフォルダのフルパスを返す（プロキシ→ソース解決）。"""
        m = self.model()
        if m is None or not index.isValid():
            return ""
        idx = index
        src = m
        while hasattr(src, "mapToSource"):
            idx = src.mapToSource(idx)
            src = src.sourceModel()
        return _safe_file_path(src, idx) if hasattr(src, "filePath") else ""

    def eventFilter(self, obj, event):
        et = event.type()
        if et in (_QtCore.QEvent.Resize, _QtCore.QEvent.Show,
                  _QtCore.QEvent.Move, _QtCore.QEvent.Hide):
            view = obj if getattr(obj, "_mfm_header", None) is not None \
                else obj.parent()
            if view is not None and getattr(view, "_mfm_header", None) is not None:
                # r120: スライド中は Move が毎フレーム飛んでくる。その時に
                # 位置が変わるのは «ハンドルだけ»（ヘッダ・フッタはカラム
                # 自身の子なので動かない）。全部置き直すとカクつく。
                if et == _QtCore.QEvent.Move:
                    self._reposition_column_handle(view)
                else:
                    self._reposition_column_header(view)
        elif et == _QtCore.QEvent.Wheel and (event.modifiers() & Qt.ShiftModifier):
            # Shift+ホイールでブラウジングエリアを横スクロール（カラム間移動）
            hbar = self.horizontalScrollBar()
            if hbar is not None:
                d = event.angleDelta().y() or event.angleDelta().x()
                hbar.setValue(hbar.value() - d)
                return True
        elif et == _QtCore.QEvent.MouseButtonDblClick:
            # ── ダブルクリック（r66） ──────────────────────────────────
            # 本パネルは1回目のプレスを自前で消費するため、QAbstractItemView の
            # pressedIndex が更新されない。Qt の mouseDoubleClickEvent は
            # 「pressedIndex == index」の時だけ doubleClicked/activated を出し、
            # それ以外は «通常のプレス» として処理する。結果、1回目のダブル
            # クリックは pressedIndex を埋めるだけで反応せず、2回目で初めて
            # 開く（実機報告: Premiere ファイルが2回目で開く）。ここで自前に
            # activated を発火し、Qt 側の処理は消費する。
            view = obj.parent()
            if view is not None and hasattr(view, "indexAt") and \
                    event.button() == Qt.LeftButton:
                try:
                    pos = event.position().toPoint()
                except AttributeError:
                    pos = event.pos()
                idx = view.indexAt(pos)
                self._cancel_reclick_rename()      # ダブルクリック＝名前変更ではない
                if idx.isValid():
                    self._pending_multi_drag = None
                    self._swallow_release = True
                    _mfm_uilog("double-click: %r" % (idx.data(),))
                    try:
                        self.activated.emit(idx)
                    except Exception as e:
                        _mfm_log("dblclick emit error: %r" % (e,))
                    return True
        elif et in (_QtCore.QEvent.DragEnter, _QtCore.QEvent.DragMove,
                    _QtCore.QEvent.Drop):
            view = obj.parent()
            if view is not None and hasattr(view, "indexAt"):
                if self._handle_view_drag(view, event, et):
                    return True
        elif et == _QtCore.QEvent.MouseButtonPress:
            view = obj.parent()
            if view is not None and hasattr(view, "indexAt"):
                try:
                    pos = event.position().toPoint()
                except AttributeError:
                    pos = event.pos()
                idx = view.indexAt(pos)
                mods = event.modifiers()
                if idx.isValid():
                    # r83: 以降は自己修復より選択の維持を優先する
                    self._mfm_user_selected = True
                self._cancel_reclick_rename()      # 新しいプレスで «ゆっくり2回» は無効
                # QColumnView は «current の親カラム以外» を NoFocus にするため、
                # 末尾カラム（ファイルのある列）をクリックしてもフォーカスが移らず、
                # F2 / Ctrl+Z 等の WidgetWithChildrenShortcut が効かない（実機で
                # 「F2 が実行されない」原因、r62）。プレス時に明示的にフォーカスする
                # （MouseFocusReason なら focusInEvent は current を動かさない）。
                try:
                    if not view.hasFocus():
                        view.setFocus(Qt.MouseFocusReason)
                except Exception as _e:
                    _swallow(_e, "ui/browser_column_view.py:1763 eventFilter")
                if _MFM_DEBUG:
                    # r109: 文字列組み立て自体がプレスごとのコストなので、
                    # デバッグ時以外は «式の評価ごと» 行わない
                    _anchor_dbg = getattr(view, "_mfm_sel_anchor", None)
                    _mfm_log(
                        "MousePress: valid=%s row=%s ctrl=%s shift=%s "
                        "anchor_valid=%s view=%s name=%r"
                        % (idx.isValid(), (idx.row() if idx.isValid() else -1),
                           bool(mods & Qt.ControlModifier),
                           bool(mods & Qt.ShiftModifier),
                           (_anchor_dbg is not None and _anchor_dbg.isValid()),
                           id(view), (idx.data() if idx.isValid() else None)))
                # ── 右クリック（r55）: 選択を壊さない（Explorer準拠） ──────────
                # 従来はボタンを見ずに左クリックと同じ経路（選択済み項目のプレス
                # 消費→リリースで単一選択化）に入り、メニューが出る前に複数選択が
                # 解けていた（Windows はリリース時に ContextMenu イベントが来る）。
                # 選択済み項目 → 何もしない（選択全体がメニュー対象）
                # 未選択項目   → その項目だけを選択（current は動かさない＝ナビしない）
                # 空白         → そのカラムの選択解除（従来通り）
                # プレス/リリースとも消費するが、ContextMenu イベントは
                # QWidgetWindow がマウスイベントの accept 状態に関係なく送るため
                # メニューは従来通り表示される。
                if event.button() == Qt.RightButton:
                    self._pending_multi_drag = None
                    self._swallow_release = True
                    if idx.isValid():
                        sm = view.selectionModel()
                        if sm is None or not sm.isSelected(idx):
                            view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
                            self._clear_multi_state()
                            self._prune_selection_to_single(idx)
                    else:
                        self._clear_column_selection(view)
                    return True
                # Ctrl/Shift+クリックは「ナビゲーションせずに複数選択」を自前で処理する。
                # QColumnView はクリックを単一ナビに横取りするため、ここで捌かないと
                # Shift/Ctrl の複数選択が効かない（マージ用の選択ができない）。
                # r125: **Ctrl を押したまま «既に選択済みの項目» を掴んだ時に、
                # その場でトグルを掛けていた。** Ctrl+クリックで複数選択した
                # 直後、指を Ctrl から離さずにドラッグを始めるのは自然な操作で
                # （Explorer では Ctrl+ドラッグ＝コピー）、そのたびに掴んだ
                # 項目だけが選択から外れ、ドラッグも始まらなかった
                # （ユーザー報告 2026-10-06「D&D をすると一つ選択が外れる」）。
                # Explorer と同じく «離した時に初めてトグル» へ変える。
                # ドラッグに至ったらトグルしない。
                if (idx.isValid() and (mods & Qt.ControlModifier)
                        and not (mods & Qt.ShiftModifier)):
                    sm = view.selectionModel()
                    if sm is not None and sm.isSelected(idx):
                        sel = self._same_column_selection(sm, idx)
                        _mfm_uilog("press(ctrl, selected): %r → トグルは保留、"
                                   "drag候補 %d件" % (idx.data(), len(sel)))
                        self._pending_multi_drag = {
                            "view": view,
                            "pos": pos,
                            "press_idx": _QtCore.QPersistentModelIndex(idx),
                            "indexes": [_QtCore.QPersistentModelIndex(i) for i in sel],
                            # 離した時に «初めて» トグルする印
                            "ctrl_toggle": True,
                            "snapshot": self._selection_snapshot(
                                exclude_view=view,
                                exclude_ancestors_of=self._path_for_index(
                                    view.rootIndex())),
                        }
                        return True
                if idx.isValid() and (mods & (Qt.ControlModifier | Qt.ShiftModifier)):
                    snap = self._selection_snapshot(
                        exclude_view=view,
                        exclude_ancestors_of=self._path_for_index(view.rootIndex()))
                    self._multi_select(view, idx, mods, preserve_snapshot=snap)
                    # 対応するリリースも必ず消費する。素通しすると
                    # ネイティブの clicked が発火してナビゲーション＋
                    # 再選択が走り、トグル解除が打ち消される
                    self._swallow_release = True
                    return True
                # 平坦トグルON: フォルダの単一クリックは «ナビせず» 平坦カラムに出す
                if idx.isValid() and getattr(view, "_mfm_flatten", False):
                    fp = self._path_of_index(idx)
                    if fp and os.path.isdir(fp):
                        QISM = _QtCore.QItemSelectionModel
                        sm = view.selectionModel()
                        if sm is not None:
                            sm.select(idx, QISM.ClearAndSelect | QISM.Rows)
                            sm.setCurrentIndex(idx, QISM.NoUpdate)
                        self._request_flat([fp])
                        return True
                # 選択済みの項目を «修飾キーなし» で押した → ドラッグ候補として
                # プレスを消費し、リリース＝クリック再現／Move閾値＝自前ドラッグ。
                # （QColumnViewは押下で単一ナビ＝選択クリアするため、自前でドラッグを
                #  起動しないと複数選択のD&Dができない。単一選択も同経路に統一：
                #  r21の「プレス素通し＋Moveだけ消費」方式はリリースがドラッグ
                #  ループに食われてビューが押下状態のまま残り、以後のクリックが
                #  効かない＝フリーズに見える不具合の原因だった）
                if idx.isValid():
                    sm = view.selectionModel()
                    if sm is not None and sm.isSelected(idx):
                        # r92: QColumnView は «全カラムで同じ選択モデル» を使うため、
                        # selectedIndexes() には左側カラムのパンくず（今いる階層の
                        # 祖先フォルダ）まで含まれる。そのままドラッグすると
                        # 「1つ選んだのに他のフォルダやファイルまで D&D される」
                        # （ユーザー報告 2026-09-23）。押した項目と «同じカラム»
                        # （＝同じ親）の選択だけを対象にする。
                        sel = self._same_column_selection(sm, idx)
                        _mfm_uilog("press(selected): %r → drag候補 %d件 [%s]"
                                   % (idx.data(), len(sel),
                                      ", ".join(str(i.data()) for i in sel)))
                        if len(sel) >= 1:
                            if not self._is_dir_index(idx):
                                self.note_file_click(idx.data())
                            # ここで即 drag.exec() すると、ただのクリックでもドラッグ扱いになり
                            # 操作が重い。Press では標準の単一選択化だけ止め、Move 閾値を
                            # 超えた時だけ MouseMove 側でD&Dを開始する。
                            view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
                            self._pending_multi_drag = {
                                "view": view,
                                "pos": pos,
                                "press_idx": _QtCore.QPersistentModelIndex(idx),
                                "indexes": [_QtCore.QPersistentModelIndex(i) for i in sel],
                                # r67: 単一選択済みの項目をもう一度クリック
                                # → «ゆっくり2回クリック» で名前変更（Explorer 準拠）
                                "reclick_single": len(sel) == 1 and sel[0] == idx,
                            }
                            return True
                # 何もない所クリック → そのカラムの選択を解除（標準挙動）
                if not idx.isValid():
                    self._clear_column_selection(view)
                    self._swallow_release = True
                    return True
                # ファイルの通常クリック: current は «動かさない»（親フォルダに
                # 留める）。QColumnView に current を渡すとプレビュー列が生成され
                # カラム全体が左へスクロールして使いづらい（指摘あり）。
                # 選択だけ全モデルで単一化し、リリースでクリック相当を再現する
                # （プレス消費＋_pending_multi_drag 経路＝ドラッグ開始も可能）。
                if idx.isValid() and not self._is_dir_index(idx):
                    self.note_file_click(idx.data())
                    _mfm_uilog("press(file): %r → drag候補 1件" % (idx.data(),))
                    view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
                    self._clear_multi_state()
                    self._prune_selection_to_single(idx)
                    self._pending_multi_drag = {
                        "view": view,
                        "pos": pos,
                        "press_idx": _QtCore.QPersistentModelIndex(idx),
                        "indexes": [_QtCore.QPersistentModelIndex(idx)],
                        "file_click": True,
                    }
                    return True
                # 通常クリック＝標準挙動: 複数選択は解除され単一選択になる。
                # r92: 未選択フォルダのドラッグもネイティブに任せない。
                # ネイティブの startDrag は «全カラム共有の選択モデル» を見るため、
                # パンくず（祖先フォルダ）まで一緒にドラッグされてしまう。
                # ファイルと同じく pending 経路に乗せ、_start_multi_drag で
                # «押した項目と同じカラムの選択だけ» をドラッグする。
                _mfm_uilog("press(unselected): %r → drag候補 1件" % (idx.data(),))
                view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
                self._clear_multi_state()
                self._prune_selection_to_single(idx)
                self._pending_multi_drag = {
                    "view": view,
                    "pos": pos,
                    "press_idx": _QtCore.QPersistentModelIndex(idx),
                    "indexes": [_QtCore.QPersistentModelIndex(idx)],
                }
                return True
        elif et == _QtCore.QEvent.MouseMove:
            pending = self._pending_multi_drag
            if pending:
                try:
                    view = pending.get("view")
                    if view is not None:
                        try:
                            pos = event.position().toPoint()
                        except AttributeError:
                            pos = event.pos()
                        start = pending.get("pos")
                        if start is not None:
                            delta = pos - start
                            if delta.manhattanLength() >= QApplication.startDragDistance():
                                indexes = [i for i in pending.get("indexes", []) if i.isValid()]
                                self._pending_multi_drag = None
                                _mfm_uilog("drag-threshold: 自前ドラッグ開始 %d件"
                                           % len(indexes))
                                self._start_multi_drag(view, indexes)
                                return True
                except Exception:
                    self._pending_multi_drag = None
                # r94: 閾値未満の MouseMove も «必ず» 消費する。
                # QAbstractItemView::mouseMoveEvent は «左ボタンが押されたまま
                # 動いた» だけで DragSelectingState に入り、自分が受け取って
                # いない押下位置（= 既定値 0,0 ＝ビュー左上）を矩形選択の起点に
                # するため、素通しするとカーソルまでの範囲＝«対象より上の全て»
                # が選択されてしまう（ユーザー報告 2026-09-23）。
                return True
            if event.buttons() & Qt.LeftButton:
                # r96: 押下を消費した後（右クリック・Ctrl/Shift選択・空白
                # クリック）も、自前ドラッグの最中／直後も、Qt へ渡すと
                # «押下位置 0,0» を起点にした矩形選択が走る（対象より上の
                # 全項目がハイライトされる）。このビューの選択は全て自前で
                # 行っているので、左ボタン中の MouseMove は常に消費する。
                return True
        elif et == _QtCore.QEvent.MouseButtonRelease:
            if getattr(self, "_swallow_release", False):
                self._swallow_release = False
                return True
            if self._pending_multi_drag:
                pending = self._pending_multi_drag
                self._pending_multi_drag = None
                if pending.get("ctrl_toggle"):
                    # r125: Ctrl+押下を保留していた分。ドラッグに至らなかったので
                    # ここで «Ctrl+クリック» として処理する（選択のトグル）。
                    try:
                        view = pending.get("view")
                        p_idx = pending.get("press_idx")
                        if view is not None and p_idx is not None and p_idx.isValid():
                            idx = view.model().index(p_idx.row(), 0, p_idx.parent())
                            if idx.isValid():
                                self._multi_select(
                                    view, idx, Qt.ControlModifier,
                                    preserve_snapshot=pending.get("snapshot"))
                    except Exception as e:
                        _mfm_log("ctrl-toggle on release error: %r" % (e,))
                    return True
                # ドラッグに至らなかった＝ただのシングルクリック。標準挙動どおり
                # 複数選択を解除して単一選択に確定し、通常のナビゲーションを行う。
                # （旧実装はここで握り潰しており「クリックしても解除されない」
                #  壊れ方の原因だった）
                try:
                    view = pending.get("view")
                    p_idx = pending.get("press_idx")
                    _mfm_log("release-click: view=%s idx_valid=%s"
                             % (view is not None,
                                (p_idx is not None and p_idx.isValid())))
                    if view is not None and p_idx is not None and p_idx.isValid():
                        idx = view.model().index(p_idx.row(), 0, p_idx.parent())
                        if idx.isValid():
                            QISM = _QtCore.QItemSelectionModel
                            self._clear_multi_state()
                            # 全モデル（各カラム＋本体）で単一選択へ確定
                            self._prune_selection_to_single(idx)
                            # ファイルは current を «一切» 動かさない（プレビュー列の
                            # 生成＝カラム全体のスクロールを防ぐ）。複数選択後は
                            # カラムの選択モデルが本体と共有されている場合があり、
                            # カラム側の setCurrentIndex でも本体 current が動く。
                            # フォルダは本体 current の変更が子カラム展開を駆動する。
                            is_file = pending.get("file_click") or \
                                not self._is_dir_index(idx)
                            sm = view.selectionModel()
                            if sm is not None and not is_file:
                                sm.setCurrentIndex(idx, QISM.NoUpdate)
                            top = self.selectionModel()
                            if top is not None and not is_file:
                                top.setCurrentIndex(idx, QISM.NoUpdate)
                            _mfm_log("release-click: single-select %r file=%s"
                                     % (idx.data(), is_file))
                            self.clicked.emit(idx)
                            # r67: 既に単一選択されていた項目の再クリック →
                            # ダブルクリック間隔を過ぎても次の操作が無ければ名前変更
                            # （ダブルクリック／次のプレスで取り消す）
                            if pending.get("reclick_single"):
                                self._schedule_reclick_rename(self._path_of_index(idx))
                except Exception as e:
                    _mfm_log("release-click error: %r" % (e,))
                return True
        return super().eventFilter(obj, event)

    # ── r87: カラム間の D&D（移動／コピー） ───────────────────────────
    def _drop_dir_for(self, view, pos):
        """ドロップ先フォルダ。項目の上ならそのフォルダ、空白ならその
        カラムが表示しているフォルダ。ファイルの上に落ちた場合も «そのカラムの
        フォルダ» として扱う（Explorer と同じ）。"""
        try:
            idx = view.indexAt(pos)
            if idx.isValid():
                p = self._path_of_index(idx)
                if p and os.path.isdir(p) and not self._is_link_path(p):
                    return p
            return self._path_for_index(view.rootIndex())
        except Exception:
            return ""

    @staticmethod
    def _is_link_path(path: str) -> bool:
        try:
            return os.path.islink(path)
        except OSError:
            return False

    @staticmethod
    def _urls_to_paths(mime):
        out = []
        try:
            if not mime.hasUrls():
                return out
            for u in mime.urls():
                p = u.toLocalFile()
                if p:
                    out.append(os.path.normpath(p))
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:2019 _urls_to_paths")
        return out

    def _handle_view_drag(self, view, event, et):
        """カラムのビューポートで起きた D&D を自前で処理する（r87）。

        Qt 標準（QFileSystemModel の dropMimeData）に任せると、
        プロキシ越しのカラム表示では «ドロップしても何も起きない» ことがあり、
        さらに «同名がある時に選ばせる» ことができない（黙って連番になる）。
        そのため受け口をここに一本化する。戻り値 True でイベントを消費。"""
        try:
            mime = event.mimeData()
            paths = self._urls_to_paths(mime)
            if not paths:
                return False
            if et == _QtCore.QEvent.Drop:
                try:
                    pos = event.position().toPoint()
                except AttributeError:
                    pos = event.pos()
                dest = self._drop_dir_for(view, pos)
                # Ctrl 押下＝コピー、それ以外は移動（Explorer 準拠）
                move = not bool(event.keyboardModifiers() & Qt.ControlModifier)
                cb = self._file_drop_cb
                _mfm_log("drop: n=%d dest=%r move=%s cb=%s"
                         % (len(paths), dest, move, callable(cb)))
                if not dest or not callable(cb):
                    return False
                event.setDropAction(Qt.MoveAction if move else Qt.CopyAction)
                event.accept()
                # ドロップ処理中にビューを組み替えるとクラッシュするため、
                # イベントを抜けてから実行する
                QTimer.singleShot(0, lambda ps=list(paths), d=dest, mv=move:
                                  cb(ps, d, mv))
                return True
            # DragEnter / DragMove: ファイルなら受け入れる
            event.setDropAction(Qt.MoveAction if not (
                event.keyboardModifiers() & Qt.ControlModifier) else Qt.CopyAction)
            event.accept()
            return True
        except Exception as e:
            _mfm_log("drag/drop error: %r" % (e,))
            return False

    @staticmethod
    def _same_column_selection(sm, idx):
        """押した項目と同じカラム（同じ親）の選択だけを返す（r92）。
        押した項目は必ず含める。"""
        parent = idx.parent()
        sel = []
        try:
            for i in sm.selectedIndexes():
                if i.column() != 0 or i.parent() != parent:
                    continue
                sel.append(i)
        except Exception:
            sel = []
        if not any(i == idx for i in sel):
            sel.append(idx)
        return sel

    def _start_multi_drag(self, view, indexes):
        """複数選択した項目を一括ドラッグする。QFileSystemModel の mimeData を
        使い、Explorer 互換のファイルD&Dにする。"""
        self._mfm_in_drag = True
        try:
            m = view.model()
            fsm = m
            while hasattr(fsm, "sourceModel"):
                fsm = fsm.sourceModel()
            src_idxs = []
            for idx in indexes:
                s = idx
                mm = m
                while hasattr(mm, "mapToSource"):
                    s = mm.mapToSource(s)
                    mm = mm.sourceModel()
                src_idxs.append(s)
            paths = [_safe_file_path(fsm, s) for s in src_idxs
                     if hasattr(fsm, "filePath")]
            paths = [p for p in paths if p]
            # r92: 最終防衛。重複と «他の対象の祖先フォルダ»（パンくず）を落とす。
            # ここを通る全経路（カラム／平坦ビュー）で «選んだものだけ» を保証する。
            seen, uniq = set(), []
            for p in paths:
                k = os.path.normcase(os.path.abspath(p))
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(p)
            paths = self._drop_ancestor_dirs(uniq)
            _mfm_uilog("drag-start: %d件 [%s]"
                       % (len(paths), ", ".join(os.path.basename(x) for x in paths)))
            if not paths:
                return
            # r90: DCC への落下は Manager が引き受ける mime（OLE 同期処理で固まらない）
            # r96: ここで例外が出ると «ドラッグが全く始まらない» のに黙って
            # 終わっていた（実機で drag-exec前 が出ないまま終了）。段階ごとに
            # ログを残し、失敗しても素の URL mime で必ずドラッグを開始する。
            try:
                mime = self.make_drag_mime(paths)
            except Exception as e:
                _mfm_warn("make_drag_mime 失敗（素の mime で続行）: %r" % (e,))
                mime = None
            if mime is None:
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(x) for x in paths])
            _mfm_uilog("drag-mime: %s" % type(mime).__name__)
            drag = QDrag(view)
            drag.setMimeData(mime)
            # r94: ドラッグが «始まったかどうか分からない» との指摘。
            # 掴んでいる対象が見えるゴースト画像をカーソルに付ける。
            try:
                pm = self._drag_pixmap(view, indexes, paths)
                if pm is not None and not pm.isNull():
                    drag.setPixmap(pm)
                    drag.setHotSpot(QPoint(12, pm.height() // 2))
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:2137 _start_multi_drag")
            # r96: PySide2 の QDrag は exec_() しか持たない版がある。
            # exec を直接呼ぶと AttributeError でドラッグが «無かったこと» になる。
            _exec = getattr(drag, "exec", None) or getattr(drag, "exec_", None)
            mfm_blocking_begin("ドラッグ中（OLE DoDragDrop）")
            try:
                _exec(Qt.CopyAction | Qt.MoveAction, Qt.MoveAction)
            finally:
                mfm_blocking_end()
            # ドラッグ終了後: ドロップ先がMayaのウィンドウなら通知する
            self._notify_drag_finished(paths, mime)
        except Exception as e:
            # r96: ここを握り潰していたため «ドラッグが始まらない» 原因が
            # ログに残らなかった（実機で drag-exec前 が出ないまま終了）。
            import traceback as _tb
            _mfm_warn("drag-start FAILED: %r\n%s" % (e, _tb.format_exc()))
        finally:
            self._mfm_in_drag = False

    def _drag_pixmap(self, view, indexes, paths):
        """ドラッグ中のゴースト画像（r94）。先頭項目のアイコン＋名前、
        2件以上なら «+N» を添える。掴んでいる対象を視覚的に示すため。"""
        from core.theme_engine import qss_vars
        from core.compat import QPen
        try:
            tv = qss_vars()
        except Exception:
            tv = {}
        pal = view.palette()
        # 色はトークン（テーマ）から取り、取れない時だけパレットへ退避する
        # （ベタ書きするとテーマ切替に追従しない）
        def _c(role, fallback):
            v = tv.get(role)
            return QColor(v) if v else QColor(fallback)
        fg = _c("on_surface", pal.windowText().color())
        bg = _c("surface_container_high", pal.window().color())
        bd = _c("primary", pal.highlight().color())
        name = os.path.basename(paths[0]) if paths else ""
        extra = ("  +%d" % (len(paths) - 1)) if len(paths) > 1 else ""
        icon = None
        try:
            for i in indexes:
                if i.isValid():
                    icon = i.data(Qt.DecorationRole)
                    break
        except Exception:
            icon = None
        font = view.font()
        fm = QFontMetrics(font)
        text = name + extra
        tw = fm.horizontalAdvance(text) if hasattr(fm, "horizontalAdvance") else fm.width(text)
        h = max(24, fm.height() + 8)
        w = min(320, 12 + 16 + 6 + tw + 12)
        try:
            dpr = view.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        pt = QPainter(pm)
        pt.setRenderHint(QPainter.Antialiasing, True)
        bg.setAlpha(235)
        pt.setPen(QPen(bd, 1))
        pt.setBrush(bg)
        pt.drawRoundedRect(QRect(0, 0, int(w) - 1, int(h) - 1), 6, 6)
        x = 8
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(pt, QRect(x, (h - 16) // 2, 16, 16))
        x += 16 + 6
        pt.setPen(fg)
        pt.setFont(font)
        pt.drawText(QRect(x, 0, int(w) - x - 8, h),
                    Qt.AlignVCenter | Qt.AlignLeft, text)
        pt.end()
        return pm

    def _notify_drag_finished(self, paths, mime=None):
        """D&D終了時、カーソル直下がMaya（別プロセス）ならコールバックへ通知。
        自アプリ内へのドロップ（ファイル移動等）は対象外。

        r90: «Manager が引き受けた» 落下だけを実行する。DCC に実ファイルを渡して
        ネイティブに処理させた場合は、Manager からは送らない（二重実行防止）。"""
        try:
            cb = self._maya_drop_cb
            if not paths or not callable(cb):
                return
            if QApplication.widgetAt(QCursor.pos()) is not None:
                return   # 自アプリ内へのドロップ
            app = _cursor_over_dcc_window()
            if not app:
                return
            if mime is not None:
                handled = list(getattr(mime, "handled", []) or [])
                if not handled and not getattr(mime, "served_real_to_dcc", False):
                    # DCC がデータを取りに来なかった場合もここで判定する
                    decide = getattr(mime, "_mfm_decide", None)
                    if callable(decide):
                        handled = list(decide()[1] or [])
                if not handled:
                    _mfm_log("dcc-drop: %s は Manager の対象外（DCC に任せる）" % app)
                    return
                paths = handled          # r91: Manager が引き受けた分だけ実行
            _mfm_log("dcc-drop detected: app=%s %d paths" % (app, len(paths)))
            cb(list(paths), app)
        except Exception as e:
            _mfm_log("dcc-drop notify error: %r" % (e,))

    def _path_of_index(self, idx):
        """カラムビューのプロキシindex → ソースの実パス。"""
        try:
            m = self.model()
            src = idx
            sm = m
            while hasattr(sm, "mapToSource"):
                src = sm.mapToSource(src)
                sm = sm.sourceModel()
            return _safe_file_path(sm, src) if hasattr(sm, "filePath") else ""
        except Exception:
            return ""

    def _multi_select(self, view, idx, mods, preserve_snapshot=None):
        """Ctrl/Shift+クリックで、ナビゲーションせず列内の複数選択を行う。"""
        sm = view.selectionModel()
        if sm is None:
            return
        QISM = _QtCore.QItemSelectionModel
        model = view.model()
        if mods & Qt.ShiftModifier:
            anchor = getattr(view, "_mfm_sel_anchor", None)
            if anchor is not None and anchor.isValid():
                a = model.index(anchor.row(), 0, anchor.parent())
                _src = "anchor"
            else:
                a = sm.currentIndex()
                _src = "currentIndex(fallback)"
            if not a.isValid():
                a = idx
                _src += "+idx"
            parent = idx.parent()
            r1, r2 = sorted([a.row(), idx.row()])
            top = model.index(r1, 0, parent)
            bot = model.index(r2, 0, parent)
            _same_parent = (a.parent() == parent)
            _mfm_log("Shift-select: a_src=%s a_row=%s idx_row=%s range=[%d..%d] "
                     "same_parent=%s anchor_par_valid=%s idx_par_valid=%s"
                     % (_src, a.row(), idx.row(), r1, r2, _same_parent,
                        a.parent().isValid(), parent.isValid()))
            # 標準挙動: Shift範囲選択は既存選択を解除して選び直す。
            # ただし «パンくず»（クリック階層の祖先チェーン）は温存する
            # （「一つ上の階層の選択が外れる」不具合の修正）。
            # 全モデル（このカラム・他カラム・本体）へ一括適用する。
            self._select_exclusively(sm, top, bot, idx)
            _selrows = sorted({i.row() for i in sm.selectedIndexes() if i.column() == 0})
            _mfm_log("Shift-select result: selected_rows=%s (count=%d)"
                     % (_selrows, len(_selrows)))
        else:  # Ctrl
            # トグル判定は «全モデルのどれかで選択されているか» を基準にする。
            # QColumnView はカラムの選択モデルを内部で差し替えるため、
            # view 側モデルの Toggle だけだと「非選択→選択」に化けて
            # 解除できないケースがある（選択1件のCtrlトグル不具合の原因）
            _was = any(m.isSelected(idx) for m in self._all_selection_models())
            _state = QISM.Deselect if _was else QISM.Select
            sm.select(idx, _state | QISM.Rows)
            self._broadcast_select(idx, _state, exclude=sm)
            view._mfm_sel_anchor = _QtCore.QPersistentModelIndex(idx)
            _selrows = sorted({i.row() for i in sm.selectedIndexes() if i.column() == 0})
            _mfm_log("Ctrl-toggle: idx_row=%s -> selected_rows=%s" % (idx.row(), _selrows))
        # 結果表示はツリー統合ではなく、常に右側の平坦ビューへ出す。
        # 順序が重要: 先に«操作したカラムの現状»で追跡を更新してから
        # スナップショットを統合する。逆順だと、Ctrlトグルで解除した項目が
        # 追跡の再選択で即復活する（「Ctrlで解除できない」不具合の原因）。
        self._sync_tracked_selection_for_view(view)
        # 冗長な子カラム抑制: current をクリック項目の«親»へ退避。
        # 順序が重要:
        #  - 選択同期(_sync)より後（先に行うとカラム再構築が選択読み取りと競合）
        #  - スナップショット復元より前（復元はcurrentのパンくずを焼き込むため、
        #    current がクリック項目のままだと、Ctrlで解除した直後の項目が
        #    パンくずとして即再選択される＝「解除できない」不具合）
        self._park_current_at_parent(sm, idx)
        self._restore_selection_snapshot(preserve_snapshot)
        dirs = self._deepest_selected_dirs()
        _mfm_log("multi_select flat: selected_dirs=%d %r flat_cb=%s"
                 % (len(dirs), [os.path.basename(d) for d in dirs], callable(self._flat_cb)))
        # レイアウト安定化: 操作中カラムの画面上の位置を固定する
        # （平坦カラム出現やカラム再構築で視点が飛ぶのを防ぐ）
        self._anchor_column_x(view)
        # 平坦ビューが自動で出るのは «複数選択(2件以上)» の時だけ。
        # 1件だけの選択で出すのは誤動作（単一は平坦ボタン/トグルで明示的に）
        self._request_flat(dirs if len(dirs) >= 2 else [])
        self._restore_selection_snapshot_later(preserve_snapshot)

    def _anchor_column_x(self, view):
        """操作中カラムのルートパスと画面上のx位置を記録し、
        レイアウト変更後に同じ位置へ戻す（視点の揺れ防止）。"""
        try:
            self._anchor_info = (self._path_for_index(view.rootIndex()), view.x())
        except Exception:
            self._anchor_info = None
        # QColumnView の scrollTo は current変更後も遅延して複数回走るため、
        # 落ち着くまで数回に分けて位置を戻す
        for ms in (60, 160, 300, 520):
            QTimer.singleShot(ms, self._restore_anchor_column_x)

    def _restore_anchor_column_x(self):
        info = getattr(self, "_anchor_info", None)
        if not info:
            return
        path, x0 = info
        try:
            target = None
            for v in self.findChildren(QListView):
                if (v.isVisible() and v.model() is not None
                        and self._path_for_index(v.rootIndex()) == path):
                    target = v
                    break
            if target is None:
                return
            hb = self.horizontalScrollBar()
            if hb is None:
                return
            # 元のx位置を基本にしつつ、ペイン縮小後も操作カラム全体が
            # 見えるようにクランプする（右端で見切れるのを防ぐ）
            vpw = self.viewport().width()
            desired = min(x0, max(0, vpw - target.width()))
            dx = target.x() - desired
            if dx:
                hb.setValue(max(0, min(hb.value() + dx, hb.maximum())))
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:2366 _restore_anchor_column_x")

    def _all_selection_models(self):
        """全カラムの選択モデル＋本体の選択モデル（重複除去済み）。

        QColumnView は createColumn 時に本体の選択モデルを«複製»して各カラムへ
        渡すため、選択操作は全モデルへ同期しないと、カラム再構築時に
        どこかへ残った古い選択が復活する。"""
        models = []
        seen = set()
        try:
            for v in self.findChildren(QListView):
                sm = v.selectionModel()
                if sm is not None and id(sm) not in seen:
                    seen.add(id(sm))
                    models.append(sm)
            top = self.selectionModel()
            if top is not None and id(top) not in seen:
                models.append(top)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:2386 _all_selection_models")
        return models

    def _broadcast_select(self, idx, state, exclude=None):
        """単一項目の Select/Deselect を全モデルへ伝播する。"""
        QISM = _QtCore.QItemSelectionModel
        for m in self._all_selection_models():
            if m is exclude:
                continue
            try:
                m.select(idx, state | QISM.Rows)
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:2398 _broadcast_select")

    def _select_exclusively(self, sm, top_idx, bot_idx, clicked_idx):
        """全モデルで «範囲のみ選択» にする（パンくず＝クリック階層の
        祖先チェーンの選択は温存）。標準の Shift 範囲選択の実装。"""
        QISM = _QtCore.QItemSelectionModel
        parent = top_idx.parent()
        r1, r2 = top_idx.row(), bot_idx.row()
        cp = self._path_of_index(clicked_idx)
        nc = os.path.normcase(os.path.normpath(cp)) if cp else ""
        rng = _QtCore.QItemSelection(top_idx, bot_idx)
        for m in self._all_selection_models():
            try:
                for i in list(m.selectedIndexes()):
                    if i.column() != 0:
                        continue
                    if i.parent() == parent and r1 <= i.row() <= r2:
                        continue      # 新しい範囲内 → 残す
                    fp = self._path_of_index(i)
                    nf = os.path.normcase(os.path.normpath(fp)) if fp else ""
                    if nf and nc.startswith(nf + os.sep):
                        continue      # パンくず（祖先）→ 温存
                    m.select(i, QISM.Deselect | QISM.Rows)
                m.select(rng, QISM.Select | QISM.Rows)
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:2423 _select_exclusively")

    def _park_current_at_parent(self, sm, idx):
        """複数選択中は current を «クリック項目の親» に退避する。

        current を項目自身にすると QColumnView がその子カラムを開いてしまい
        «冗長な子カラム» が出る。以前の «幅0に畳む» 方式は、ナビゲーションで
        カラムが作り直されると内部幅テーブル（インデックス対応）が汚染され、
        無関係なカラムまで消える事故を起こした（動画の症状）ため全廃。
        current の退避なら QColumnView 自身が右側の子カラムを畳んでくれる。
        選択は NoUpdate で維持し、カラム再構築後にハイライトを再適用する。"""
        QISM = _QtCore.QItemSelectionModel
        try:
            # 視点固定: park による current 変更で QColumnView が横スクロール
            # （scrollTo）して操作対象を見失うのを防ぐため、一時的に自動
            # スクロールを止める（常時OFFは新規カラムの表示を壊すため不可）
            self.setAutoScroll(False)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:2441 _park_current_at_parent")
        try:
            parent = idx.parent()
            target = parent if parent.isValid() else idx
            sm.setCurrentIndex(target, QISM.NoUpdate)
            # 本体側の current も揃える（カラム構成は本体currentが決める）
            top = self.selectionModel()
            if top is not None and top is not sm:
                top.setCurrentIndex(target, QISM.NoUpdate)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:2451 _park_current_at_parent")
        QTimer.singleShot(250, lambda: self.setAutoScroll(True))
        # QColumnViewの非同期なカラム再構築の後に、選択ハイライトを描き直す
        QTimer.singleShot(0, self._restore_tracked_selection)
        QTimer.singleShot(80, self._restore_tracked_selection)

    def _is_dir_index(self, idx) -> bool:
        """プロキシindexがフォルダか（I/Oなし: QFileSystemModel のキャッシュ情報）。
        不明な場合はフォルダ扱い（従来のネイティブ経路に落とす）。"""
        try:
            m = self.model()
            i = idx
            while hasattr(m, "mapToSource"):
                i = m.mapToSource(i)
                m = m.sourceModel()
            if not i.isValid() or not i.parent().isValid():
                return True      # ドライブ階層は触らない（フォルダ扱い）
            return bool(m.isDir(i))
        except Exception:
            return True

    def _prune_after_native_click(self, p_idx):
        """ネイティブの通常クリック直後に、本体・他カラムの残存選択を
        クリック項目（＋祖先パンくず）だけに掃除する。"""
        try:
            if p_idx is None or not p_idx.isValid():
                return
            idx = self.model().index(p_idx.row(), 0, p_idx.parent())
            if idx.isValid():
                self._prune_selection_to_single(idx)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:2482 _prune_after_native_click")

    def _prune_selection_to_single(self, idx):
        """クリック項目とその祖先(パンくず)以外の選択を全モデルから外し、
        クリック項目を選択状態にする（標準のシングルクリック挙動）。
        各カラムの独立選択モデルと本体モデルの両方を掃除する。"""
        QISM = _QtCore.QItemSelectionModel
        path = self._path_of_index(idx)
        nc = os.path.normcase(os.path.normpath(path)) if path else ""
        models = {v.selectionModel() for v in self.findChildren(QListView)}
        models.add(self.selectionModel())
        models.discard(None)
        for m in models:
            try:
                for i in list(m.selectedIndexes()):
                    if i.column() != 0:
                        continue
                    fp = self._path_of_index(i)
                    nf = os.path.normcase(os.path.normpath(fp)) if fp else ""
                    keep = bool(nf) and (nf == nc or nc.startswith(nf + os.sep))
                    if not keep:
                        m.select(i, QISM.Deselect | QISM.Rows)
                m.select(idx, QISM.Select | QISM.Rows)
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:2506 _prune_selection_to_single")
        for v in self.findChildren(QListView):
            try:
                v.viewport().update()
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:2511 _prune_selection_to_single")

    def _clear_column_selection(self, view):
        """カラムの空白クリック: そのカラム内の選択をすべて解除する。"""
        QISM = _QtCore.QItemSelectionModel
        root = view.rootIndex()
        col_path = ""
        # r108: filePath() はネットワーク先で止まり得る。追跡中のフォルダが
        # 無ければそもそも不要なので、その時は呼ばない。
        if getattr(self, "_selected_dir_paths", None):
            try:
                col_path = self._path_for_index(root) or ""
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:2524 _clear_column_selection")
        for m in self._all_selection_models():
            try:
                for i in list(m.selectedIndexes()):
                    if i.column() == 0 and i.parent() == root:
                        m.select(i, QISM.Deselect | QISM.Rows)
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:2531 _clear_column_selection")
        # 追跡からこのカラム直下のフォルダを外す
        try:
            if col_path:
                nc = os.path.normcase(os.path.normpath(col_path))
                self._selected_dir_paths = {
                    p for p in self._selected_dir_paths
                    if self._norm_parent(p) != nc
                }
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:2541 _clear_column_selection")
        view._mfm_sel_anchor = None
        # 選択を解除したカラムより下（右）の階層カラムは削除する。
        # current をこのカラムのルート（＝このカラムのフォルダ）へ退避すると
        # QColumnView が右側の子カラムを畳んでくれる
        try:
            QISM = _QtCore.QItemSelectionModel
            if root.isValid():
                try:
                    self.setAutoScroll(False)
                except Exception as _e:
                    _swallow(_e, "ui/browser_column_view.py:2552 _clear_column_selection")
                smv = view.selectionModel()
                if smv is not None:
                    smv.setCurrentIndex(root, QISM.NoUpdate)
                top = self.selectionModel()
                if top is not None:
                    top.setCurrentIndex(root, QISM.NoUpdate)
                QTimer.singleShot(250, lambda: self.setAutoScroll(True))
                # 再構築後にパンくずを再適用
                QTimer.singleShot(0, self._restore_tracked_selection)
                QTimer.singleShot(80, self._restore_tracked_selection)
        except Exception as _e:
            _swallow(_e, "ui/browser_column_view.py:2564 _clear_column_selection")
        dirs = self._deepest_selected_dirs()
        _mfm_log("clear_column_selection: col=%r remain_dirs=%d"
                 % (os.path.basename(col_path), len(dirs)))
        # 解除後も «他所に2件以上の複数選択が残っている» 時だけ平坦を維持。
        # 1件以下なら平坦ビューは閉じる（解除で平坦が出る誤動作の修正）
        self._request_flat(dirs if len(dirs) >= 2 else [])

    def _clear_multi_state(self):
        """通常クリック時に複数選択の追跡状態をリセット（標準の操作感）。
        実際の選択解除は QColumnView 標準のクリック処理（ClearAndSelect）が行う。"""
        self._selected_dir_paths = set()

    def _deselect_paths(self, paths):
        """追跡から外れたフォルダの選択ハイライトも外す（見た目と結果の一致）。"""
        QISM = _QtCore.QItemSelectionModel
        for p in paths:
            try:
                idx = self._proxy_index_for_path(p)
                if not idx.isValid():
                    continue
                sms = [v.selectionModel() for v in self.findChildren(QListView)]
                sms.append(self.selectionModel())  # 本体側も忘れずに
                for sm in sms:
                    if sm is not None and sm.isSelected(idx):
                        sm.select(idx, QISM.Deselect | QISM.Rows)
            except Exception as _e:
                _swallow(_e, "ui/browser_column_view.py:2591 _deselect_paths")

    def currentChanged(self, current: QModelIndex, previous: QModelIndex):
        super().currentChanged(current, previous)
        if self._max_depth <= 0 or not current.isValid():
            return
        # ルートから current までの祖先チェーンを構築
        root = self.rootIndex()
        chain = []
        idx = current
        while idx.isValid() and idx != root:
            chain.append(idx)
            idx = idx.parent()
        # 可視カラム数 = チェーン長（root直下=1カラム目）
        if len(chain) > self._max_depth:
            new_root = _QtCore.QPersistentModelIndex(chain[-1])
            QTimer.singleShot(0, lambda: self._apply_root_shift(new_root))

    def _apply_root_shift(self, persistent_root):
        if not persistent_root.isValid() or self.model() is None:
            return
        idx = self.model().index(persistent_root.row(),
                                 persistent_root.column(),
                                 persistent_root.parent())
        if idx.isValid():
            self.setRootIndex(idx)


# ---------------------------------------------------------------------------
# Browser Panel
# ---------------------------------------------------------------------------

