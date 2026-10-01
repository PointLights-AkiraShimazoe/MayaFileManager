# -*- coding: utf-8 -*-
"""ファイル一覧のフィルタ／ソート用プロキシモデル（r110 で分離）。"""
from core.diag import swallow as _swallow  # r112

import os
import threading

from core.compat import (
    Qt, QObject,
    QApplication, QSortFilterProxyModel,
    QMenu, QInputDialog, QModelIndex, QRect, QPixmap, QFileInfo, QPoint,
    QTimer, QDrag, )
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

from ui.browser_delegates import _badge_tooltip  # noqa: F401
from ui.browser_util import (  # noqa: F401  （再エクスポート）
    _cursor_over_maya_window, _cursor_over_dcc_window, _time_mod, _MFM_T0,
    _MFM_STARTUP_LOG, _MFM_FREEZE_LOG, _re_mod, _DRIVE_IN_LABEL_RE,
    _safe_file_path, _SafeIconProvider, _mfm_slow_note, _MFM_UI_LOG,
    _MFM_UILOG_ON, _mfm_warn, _mfm_uilog, _MFM_BLOCKING,
    mfm_blocking_begin, mfm_blocking_end, mfm_blocking_reason,
    _mfm_timeline, _MFM_LOG_PATH, _MFM_DEBUG, _mfm_log,
)





class FileFilterProxyModel(QSortFilterProxyModel):
    """Filters by filename substring and controls sort column."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter = ""
        self._show_hidden = False
        # 隠し属性でも常に表示するパス集合（normcase済み）。
        # ショートカット先が AppData 等の隠しフォルダを経由する場合に、
        # その経路の祖先だけを表示してカラムチェーンを構築可能にする。
        self._force_visible = set()
        # カラム別(親パス別)のフィルタ/ソート状態。
        #   _col_filters: normcase(親パス) -> フィルタ文字列(小文字)
        #   _col_sorts:   normcase(親パス) -> (key, ascending)  key in name/type/date/size
        self._col_filters = {}
        self._col_excludes = {}   # 親パス別「排他フィルタ」（一致を除外）
        self._col_sorts = {}
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(True)
        # isHidden(=stat) の結果キャッシュ。filterAcceptsRow はモデル内の
        # «全読み込み済み行» に対して invalidateFilter の度に呼ばれるため、
        # 毎回statするとネットワーク先で1回のナビが数十秒級になる（実測21秒、
        # mfm_freeze.log で捕捉）。隠し属性は滅多に変わらないので永続キャッシュ。
        # さらに «初回のstatもUIスレッドでは行わない»（ジャンクション先が
        # ネットワークだと1件statに秒単位かかり、キャッシュ空の初回パスで
        # 10〜20秒フリーズした実測あり）。未判定の行はいったん表示し、
        # ワーカースレッドで判定 → 隠しと判明した時だけ一括で再評価する。
        self._hidden_cache = {}
        self._hidden_pending = set()        # 判定待ちの normcase パス
        self._hidden_raw = {}               # normcase パス → 実パス（stat用）
        self._hidden_lock = threading.Lock()
        self._hidden_worker_running = False
        self._hidden_found = False          # 隠しが新たに見つかったら再評価
        self._hidden_timer = QTimer(self)
        self._hidden_timer.setSingleShot(True)
        self._hidden_timer.setInterval(250)
        self._hidden_timer.timeout.connect(self._flush_hidden_results)

    @staticmethod
    def _norm(p: str) -> str:
        try:
            return os.path.normcase(os.path.normpath(p)) if p else ""
        except Exception:
            return ""

    def set_filter_string(self, text: str):
        self._filter = text.lower()
        self.invalidateFilter()

    def set_show_hidden(self, show: bool):
        self._show_hidden = show
        with self._hidden_lock:      # 表示切替時は属性を取り直す
            self._hidden_cache.clear()
            self._hidden_pending.clear()
            self._hidden_raw.clear()
        self.invalidateFilter()

    # --- 隠し属性の非同期解決（UIスレッドでのstat禁止） -------------------
    def _queue_hidden_probe(self, norm_path: str, raw_path: str):
        """UIスレッドから呼ぶ。判定待ちに積み、ワーカーとフラッシュを起動。"""
        need_worker = False
        with self._hidden_lock:
            if norm_path not in self._hidden_pending:
                self._hidden_pending.add(norm_path)
                self._hidden_raw[norm_path] = raw_path
            if not self._hidden_worker_running:
                self._hidden_worker_running = True
                need_worker = True
        if need_worker:
            threading.Thread(target=self._hidden_worker, daemon=True,
                             name="mfm-hidden-probe").start()
        if not self._hidden_timer.isActive():
            self._hidden_timer.start()

    def _hidden_worker(self):
        """ワーカースレッド: 判定待ちパスの隠し属性を lstat で解決する。"""
        import stat as _stat
        while True:
            with self._hidden_lock:
                if not self._hidden_pending:
                    self._hidden_worker_running = False
                    return
                norm = self._hidden_pending.pop()
                raw = self._hidden_raw.pop(norm, norm)
            hidden = False
            try:
                st = os.lstat(raw)
                attrs = getattr(st, "st_file_attributes", 0)
                hidden = bool(attrs & 2)   # FILE_ATTRIBUTE_HIDDEN
            except OSError:
                hidden = False
            with self._hidden_lock:
                if len(self._hidden_cache) > 200000:
                    self._hidden_cache.clear()
                self._hidden_cache[norm] = hidden
                if hidden:
                    self._hidden_found = True

    def _flush_hidden_results(self):
        """UIスレッド: 判定結果を反映。隠しが見つかった時だけ再フィルタ。"""
        with self._hidden_lock:
            busy = self._hidden_worker_running or bool(self._hidden_pending)
            found = self._hidden_found
            self._hidden_found = False
        if found:
            self.invalidateFilter()
        if busy:
            self._hidden_timer.start()

    def set_force_visible(self, paths):
        """隠し属性でも表示する祖先パス集合を設定する。
        内容が変わらない場合は invalidateFilter を «スキップ» する
        （invalidateFilter はモデル全行の再評価＝ナビ毎に呼ぶと重い）。"""
        new = set(self._norm(p) for p in paths)
        if new == self._force_visible:
            return
        self._force_visible = new
        self.invalidateFilter()

    # --- カラム別フィルタ/ソート ----------------------------------------
    def set_column_filter(self, parent_path: str, text: str):
        key = self._norm(parent_path)
        if text:
            self._col_filters[key] = text.lower()
        else:
            self._col_filters.pop(key, None)
        self.invalidateFilter()

    def get_column_filter(self, parent_path: str) -> str:
        return self._col_filters.get(self._norm(parent_path), "")

    def set_column_exclude(self, parent_path: str, text: str):
        key = self._norm(parent_path)
        if text:
            self._col_excludes[key] = text.lower()
        else:
            self._col_excludes.pop(key, None)
        self.invalidateFilter()

    def get_column_exclude(self, parent_path: str) -> str:
        return self._col_excludes.get(self._norm(parent_path), "")

    def set_column_sort(self, parent_path: str, key: str, ascending: bool = True):
        self._col_sorts[self._norm(parent_path)] = (key, ascending)
        # invalidate() は QFileSystemModel の非同期 populate と競合してクラッシュし得るため、
        # sort(-1)→sort(0) で安全に再ソートを強制する。
        self.sort(-1)
        self.sort(0, Qt.AscendingOrder)

    def get_column_sort(self, parent_path: str):
        return self._col_sorts.get(self._norm(parent_path), ("name", True))

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        # ホットパス注意: 本メソッドは invalidateFilter の度にモデル内の
        # «全読み込み済み行» へ呼ばれる。stat等のI/Oや不要なパス正規化を
        # 行うと1回のナビで数十秒フリーズする（2026-09 実測21秒）。
        # ドライブ階層（ルート直下の行 = C:/, W:/ …）は判定せず必ず通す。
        # 切断済みドライブマッピングの行に isDir()/filePath() 等で触れると
        # QFileInfo が stat を試みて約21秒ブロックする（SLOW記録 'W:/' 'X:/'
        # で確定。2026-09）。ドライブは隠し判定もフィルタも不要。
        if not source_parent.isValid():
            return True
        source_model = self.sourceModel()
        # 各Qt呼び出しの所要時間を計測し、0.5秒超なら記録する（PySideの
        # C++呼び出しはGILを離さないため、外部サンプラでは特定できない）
        _pc = _time_mod.perf_counter
        _t0 = _pc()
        index = source_model.index(source_row, 0, source_parent)
        _t1 = _pc()
        name = source_model.fileName(index)
        _t2 = _pc()
        if _t2 - _t0 > 0.5:
            _mfm_slow_note("filterAcceptsRow index()=%.2fs fileName()=%.2fs "
                           "parent=%r name=%r"
                           % (_t1 - _t0, _t2 - _t1,
                              _safe_file_path(source_model, source_parent), name))

        fp = None
        protected = False
        if self._force_visible:
            try:
                _t3 = _pc()
                raw_fp = _safe_file_path(source_model, index)
                _t4 = _pc()
                if _t4 - _t3 > 0.5:
                    _mfm_slow_note("filterAcceptsRow filePath()=%.2fs path=%r"
                                   % (_t4 - _t3, raw_fp))
                fp = self._norm(raw_fp)
            except Exception:
                fp = ""
            protected = fp in self._force_visible  # 現在ナビ中の経路は常に表示

        # Hidden files（隠し属性 or ドット名）。isHidden は stat を伴うため
        # キャッシュ必須（隠し属性は滅多に変わらない）。
        if not self._show_hidden and not protected:
            if name.startswith("."):
                return False
            if fp is None:
                try:
                    fp = self._norm(_safe_file_path(source_model, index))
                except Exception:
                    fp = ""
            is_hidden = self._hidden_cache.get(fp)
            if is_hidden is None:
                # 未判定はいったん表示し、statはワーカーで行う（UIスレッドで
                # statするとネットワーク先で初回パスが数十秒フリーズする）。
                # 隠しと判明した時だけ後から一括で再評価される。
                try:
                    raw = _safe_file_path(source_model, index)
                except Exception:
                    raw = ""
                if raw:
                    self._queue_hidden_probe(fp, raw)
                is_hidden = False
            if is_hidden:
                return False

        # フィルタ類が全て空なら以降の計算は不要（最頻ケースの早期リターン）
        if not (self._filter or self._col_filters or self._col_excludes):
            return True

        name_l = name.lower()

        # 全体フィルタ（ディレクトリはナビ維持のため常に通す）
        if self._filter and not source_model.isDir(index):
            if not self._fuzzy_match(self._filter, name_l):
                return False

        # カラム別フィルタ／排他フィルタ（その親=カラムにのみ適用。現在ナビ中の
        # 経路の祖先は保護してチェーンを壊さない）。
        # 一致は «部分一致(substring)»。例: "c00" は "c010" にはヒットしない。
        if (self._col_filters or self._col_excludes) \
                and source_parent.isValid() and not protected:
            pkey = self._norm(_safe_file_path(source_model, source_parent))
            cf = self._col_filters.get(pkey)
            if cf and cf not in name_l:
                return False
            ex = self._col_excludes.get(pkey)
            if ex and ex in name_l:
                return False   # 排他フィルタに一致 → 除外

        return True

    def hasChildren(self, parent=QModelIndex()):
        """空フォルダでも子カラムを出す（r62、ユーザー指示）。
        QSortFilterProxyModel の既定は「フェッチ済みで行が0（または全て
        フィルタ除外）」なら False を返し、QColumnView はその場合ヘッダの無い
        «プレビュー列»（空の QWidget）を出すため「子カラムが出ない」ように見えた。
        フォルダなら常に True を返して通常のカラム（ヘッダ付き・空）を作らせる。
        ドライブ階層（親が無効）の行には触れない（21秒ブロックの罠）。"""
        try:
            if parent.isValid():
                sp = self.mapToSource(parent)
                if sp.isValid() and sp.parent().isValid():
                    sm = self.sourceModel()
                    if sm is not None and sm.isDir(sp):
                        return True
        except Exception as _e:
            _swallow(_e, "ui/browser_models.py:313 hasChildren")
        return super().hasChildren(parent)

    # ── 表示名（r115） ────────────────────────────────────────────────
    # 実体名とは別に «カラムへ出す名前» を持てる。パスに関わる処理は一切
    # 影響を受けない（表示だけを差し替える）。
    # 親カラム単位で対応表をキャッシュし、行ごとの stat を避ける。
    _alias_cache = {}          # 親の internalId -> (dir, {実体名: 表示名} or None)

    def invalidate_display_names(self):
        self._alias_cache.clear()
        try:
            from core import display_names
            display_names.invalidate()
        except Exception as _e:
            _swallow(_e, "ui/browser_models.py invalidate_display_names")

    def _alias_for_parent(self, source_parent):
        """その親ディレクトリの表示名対応表。無ければ None。"""
        if not source_parent.isValid():
            return None
        key = source_parent.internalId()
        hit = self._alias_cache.get(key)
        if hit is not None:
            return hit[1]
        sm = self.sourceModel()
        d = _safe_file_path(sm, source_parent)
        m = None
        if d:
            from core import display_names
            m = display_names.alias_map(d)
        self._alias_cache[key] = (d, m)
        return m

    def data(self, index, role=Qt.DisplayRole):
        # 表示名: DisplayRole だけ差し替える（実体名は EditRole 等に残る）
        if role == Qt.DisplayRole and index.column() == 0:
            try:
                si = self.mapToSource(index)
                m = self._alias_for_parent(si.parent())
                if m:
                    real = self.sourceModel().fileName(si)
                    alias = m.get(real)
                    if alias:
                        return alias
            except Exception as _e:
                _swallow(_e, "ui/browser_models.py data(display-name)")
        # ツールチップに連携状態を付加（辞書参照のみ・ホバー時だけ呼ばれる）
        if role == Qt.ToolTipRole:
            # 表示名が効いている項目は «実体名» を必ず見せる（r115）
            try:
                si = self.mapToSource(index)
                m = self._alias_for_parent(si.parent())
                if m:
                    real = self.sourceModel().fileName(si)
                    if m.get(real):
                        from core.i18n import tr
                        base = super().data(index, role)
                        head = tr("実際の名前: %s", "Actual name: %s") % real
                        return head + (("\n" + str(base)) if base else "")
            except Exception as _e:
                _swallow(_e, "ui/browser_models.py data(tooltip-real-name)")
            try:
                from core.integrations import get_manager
                mgr = get_manager()
                if mgr.enabled:
                    sm = self.sourceModel()
                    si = self.mapToSource(index)
                    p = _safe_file_path(sm, si)
                    hit = mgr.status_for(p) if p else None
                    if hit:
                        # isDir はキャッシュ参照（ルート直下＝ドライブ行には触れない）
                        is_dir = bool(si.parent().isValid() and sm.isDir(si))
                        tip = _badge_tooltip(hit[0], hit[1], is_dir)
                        if tip:
                            base = super().data(index, role)
                            return ((str(base) + "\n") if base else "") + tip
            except Exception as _e:
                _swallow(_e, "ui/browser_models.py:335 data")
        return super().data(index, role)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """カラム別ソート。フォルダ優先＋親パス別のキー(name/type/date/size)。
        QFileInfo(stat)は再ソート中に不安定なため、モデルのキャッシュ値/DisplayRoleを使う。"""
        try:
            sm = self.sourceModel()
            parent = left.parent()
            if not parent.isValid():
                # ドライブ階層: isDir()/data() は切断ドライブで stat（21秒）を
                # 起こすため使わず、fileName（I/Oなし）の比較のみ
                return sm.fileName(left).lower() < sm.fileName(right).lower()
            ppath = self._norm(_safe_file_path(sm, parent))
            key, asc = self._col_sorts.get(ppath, ("name", True))
            an = (sm.data(left) or "").lower()
            bn = (sm.data(right) or "").lower()
            # フォルダは常に先頭（昇順/降順に関わらず）
            ld, rd = sm.isDir(left), sm.isDir(right)
            if ld != rd:
                return ld
            if key == "type":
                a = an.rsplit(".", 1)[-1] if "." in an else ""
                b = bn.rsplit(".", 1)[-1] if "." in bn else ""
                if a == b:
                    a, b = an, bn
            elif key == "date":
                a, b = sm.lastModified(left), sm.lastModified(right)
            elif key == "size":
                a, b = sm.size(left), sm.size(right)
            else:  # name
                a, b = an, bn
            return (a < b) if asc else (a > b)
        except Exception:
            return False

    @staticmethod
    def _fuzzy_match(pattern: str, name: str) -> bool:
        """
        部分一致 or 順序保存サブシーケンス一致 (N-3 ファジー検索)。
        例: 'chrahair' → 'chr_A_hair_sim_v012.ma' にヒット
        """
        if pattern in name:
            return True
        it = iter(name)
        return all(c in it for c in pattern)


# ---------------------------------------------------------------------------
# 外部サービス連携: 状態バッジ（アイコン右下の小さな丸）
# ---------------------------------------------------------------------------

# 状態 → (色ロール名, グリフ, ツールチップ)。実際の色は design_tokens.json の
# status_* から引く（テーマ追従。r82）。Mercury の «有彩色は1つ» は装飾に対する
# 規則で、意味を持つ状態色はその例外として彩度を落として使う。
