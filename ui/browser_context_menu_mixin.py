# -*- coding: utf-8 -*-
"""BrowserPanel の 右クリックメニュー（項目／フォルダ／平坦ビュー）と DCC・連携のアクション（r146 で分離）。

ui/browser_panel.py が 4,400 行を超えて読めなくなっていたので、
責務ごとに Mixin へ分けた。**挙動は変えていない**（メソッド名も
呼び出し側もそのまま）。BrowserPanel がこれを継承する。
他の Mixin や本体の属性（self._fs_model など）を前提にしているので、
単体では使わない。
"""
from core.diag import swallow as _swallow  # r112
from core.diag import diag_off as _diag_off  # r146 切り分け用
from core.i18n import tr  # r118: モジュールレベルで提供（ローカル import 不要）

import os
import struct
import threading
from pathlib import Path
from typing import List, Optional, Callable

from core.compat import (
    Qt, Signal, QObject,
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QLineEdit, QToolButton,
    QSplitter, QListView,
    QSizePolicy, QFrame, QAbstractItemView, QFileSystemModel, QMenu, QAction, QMessageBox, QFileDialog, QInputDialog, QDialog,
    QModelIndex, QSize, QRect, QPixmap, QDir, QFileInfo, QUrl, QMimeData, QPoint,
    QTimer, QKeySequence, QDrag, )
from core.compat import QtCore as _QtCore
from core.compat import is_alive
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
from core.path_guard import PathProber, DriveScanner, invalidate_cache
# r146: ここを «メソッドの中だけ» で import していたため、_copy_dialog /
# _move_dialog が NameError で無反応になっていた（r145 の取りこぼし）。
# 共通ダイアログのヘルパはモジュール先頭で取り込む。
from ui.dialog_util import pick_directory, pick_open_file  # noqa: F401
from core.file_operations import (
    open_with_default_app, reveal_in_explorer,
    copy_items, move_items, get_file_type_category, format_size,
    resolve_windows_shortcut,
    is_read_only, set_read_only,
    OperationCancelled as _OperationCancelled,
    MAYA_EXTENSIONS
)
from core.thumbnail_generator import ThumbnailManager

from ui.dcc_header import glyph_icon, integration_icon  # r142: アイコン列を揃える
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
from ui.browser_column_view import (  # noqa: F401  （再エクスポート）
    CappedColumnView, _DccAwareMime, _ColumnSizePopup,
    _ColumnResizeHandle, _SizeButtonHover,
)



from ui.browser_models import FileFilterProxyModel  # noqa: F401


def _bp():
    """本体モジュール（ui.browser_panel）を実行時に引く。

    テストは ``ui.browser_panel.QMenu`` などを差し替えて挙動を観察する。
    Mixin が自分の名前空間の QMenu を使うと、その差し替えが効かなくなる
    ので、差し替えの対象になる名前は **本体モジュール経由** で引く。
    （読み込みの循環を避けるため、関数の中で import する。）"""
    import ui.browser_panel as _m
    return _m


class ContextMenuMixin:
    """右クリックメニュー（項目／フォルダ／平坦ビュー）と DCC・連携のアクション。"""

    def _show_context_menu(self, pos: QPoint):
        paths = self._get_selected_paths()
        sender = self.sender()
        # Explorer準拠の対象決定。各カラムの選択モデルは本体と別物のため、
        # 右クリック時点で本体側の選択が同期済みとは限らない。従来は
        # 「本体の選択リスト」をそのまま使っており、.ma を右クリックしても
        # 対象が古い選択やパンくず（祖先フォルダ）になり、Mayaメニューが
        # 出たり出なかったりした。
        # カーソル直下の項目は «カラム（子QListView）の座標系» で解決する。
        # QColumnView.indexAt() は各カラムのヘッダ分のビューポートマージン
        # （_COL_HEADER_H=52px）を考慮せず、1〜2行ズレた項目/無効を返すため、
        # 「.ma を右クリックしてもMayaメニューが出ない（再選択で出る）」の
        # 原因になっていた（r34の実装ミス、2026-09-07）。
        cursor_path = ""
        column_dir = ""          # r70: 空白を右クリックした時のカラムのフォルダ
        gpos = (sender.viewport().mapToGlobal(pos)
                if hasattr(sender, "viewport") else self.mapToGlobal(pos))
        try:
            # グローバル座標を含むビューポートを持つカラムを探し、そのカラムの
            # 座標系で indexAt する（カーソル位置APIに依存しない決定的な方法）
            lv_hit = None
            for lv in self._column_view.findChildren(QListView):
                if not lv.isVisible() or lv.model() is None:
                    continue
                vp = lv.viewport()
                top_left = vp.mapToGlobal(QPoint(0, 0))
                if (top_left.x() <= gpos.x() < top_left.x() + vp.width()
                        and top_left.y() <= gpos.y() < top_left.y() + vp.height()):
                    lv_hit = lv
                    break
            if lv_hit is not None:
                idx = lv_hit.indexAt(lv_hit.viewport().mapFromGlobal(gpos))
                if idx.isValid():
                    cursor_path = self._resolve_path(idx)
                else:
                    root = lv_hit.rootIndex()
                    if root.isValid():
                        column_dir = self._resolve_path(root)
            elif hasattr(sender, "indexAt"):
                idx = sender.indexAt(pos)
                if idx.isValid():
                    cursor_path = self._resolve_path(idx)
        except Exception:
            cursor_path = ""
        if cursor_path:
            if cursor_path not in paths:
                # 未選択の項目を右クリック → その項目だけを対象にする
                paths = [cursor_path]
            else:
                # 選択済み項目を右クリック → 選択全体を対象にするが、
                # パンくず（他の選択の祖先フォルダ）は対象から外す
                # （_drop_ancestor_dirs は CappedColumnView 側の静的メソッド）
                paths = CappedColumnView._drop_ancestor_dirs(paths)
        elif column_dir:
            # r70: カラムの空白を右クリック → そのフォルダのメニュー
            # （DCC からの保存／書き出し・貼り付け）。右ボタンのプレスで
            # カラムの選択は解除済み（r55）なので本体の選択は使わない。
            self._popup_folder_context_menu(column_dir, gpos)
            return
        if not paths:
            return
        self._popup_context_menu(paths, gpos)

    # ── r70: DCC からの保存／書き出し ─────────────────────────────────
    def _add_dcc_save_actions(self, menu, target: str, exts_hint=None):
        """«シーンを保存»«選択を書き出し» を «1つの DCC 分だけ» メニューへ
        （ユーザー指示: 4つに分けず、ヘッダで選択中の DCC を出す）。
        DCC の決定は dcc_for_path: .ma/.mb → Maya、.blend → Blender、
        フォルダ／共通形式 → 選択中の DCC（_dcc_target_provider）。
        target はフォルダ（空白右クリック）または単一ファイル（ダイアログの
        ファイル名欄に入る）。実行は _dcc_callback(app, action, [target])
        → MainWindow 側でダイアログ→DCC へ送信。"""
        from core import dcc_save
        from core.file_operations import dcc_for_path
        dcc_cb = getattr(self, "_dcc_callback", None)
        if not callable(dcc_cb) or not target:
            return False
        ext = "" if os.path.isdir(target) else Path(target).suffix.lower()
        app = self.current_dcc_target()
        if ext:
            app = dcc_for_path(target, app)
            if ext not in dcc_save.exts_for(app):
                return False
        # r125: ここもアプリ名を外し、アイコンで «どの DCC か» を示す。
        # «...» は «ダイアログが開く» の意（Windows の作法）なので残す。
        from ui.dcc_header import dcc_app_icon
        ic = dcc_app_icon(app)
        a = menu.addAction(ic, tr("シーンを保存...", "Save Scene..."))
        a.setData((app, "save_scene"))
        a.triggered.connect(lambda _c=False, ap=app: dcc_cb(ap, "save_scene", [target]))
        a = menu.addAction(ic, tr("選択を書き出し...", "Export Selection..."))
        a.setData((app, "export_selection"))
        a.triggered.connect(lambda _c=False, ap=app: dcc_cb(ap, "export_selection", [target]))
        return True

    def current_dcc_target(self) -> str:
        """ヘッダのスイッチで選択中の DCC（MainWindow が provider を登録。既定 maya）。"""
        fn = getattr(self, "_dcc_target_provider", None)
        try:
            v = fn() if callable(fn) else None
        except Exception:
            v = None
        return "blender" if v == "blender" else "maya"

    def set_dcc_target_provider(self, fn):
        self._dcc_target_provider = fn

    def _popup_folder_context_menu(self, folder: str, global_pos):
        """カラムの空白（項目の無い所）の右クリックメニュー（r70）。"""
        menu = _bp().QMenu(self)
        # r73: 新規作成（作成後はそのまま名前変更モード）
        nf = menu.addAction(glyph_icon("📁"), tr("新規フォルダ", "New folder") + "\tCtrl+Shift+N")
        nf.triggered.connect(lambda _c=False: self._create_new_item("folder", folder))
        nt = menu.addAction(glyph_icon("📄"), tr("新規テキスト ドキュメント", "New text document") + "\tCtrl+Shift+T")
        nt.triggered.connect(lambda _c=False: self._create_new_item("text", folder))
        menu.addSeparator()
        if self._add_dcc_save_actions(menu, folder):
            menu.addSeparator()
        paste_act = menu.addAction(glyph_icon("📋"), tr("貼り付け", "Paste") + "\tCtrl+V")
        mime = QApplication.clipboard().mimeData()
        paste_act.setEnabled(bool(mime and mime.hasUrls()))
        paste_act.triggered.connect(lambda _c=False: self._clipboard_paste_into(folder))
        menu.addSeparator()
        reveal_act = menu.addAction(glyph_icon("📁"), tr("エクスプローラーで表示", "Show in Explorer"))
        reveal_act.triggered.connect(lambda _c=False, t=folder: self._reveal_path(t))
        copy_path_act = menu.addAction(glyph_icon("📋"), tr("フォルダパスをコピー", "Copy folder path"))
        copy_path_act.triggered.connect(lambda _c=False: self._copy_paths_to_clipboard([folder]))
        menu.addSeparator()
        # r116: カラムの空白を右クリック → そのカラムの表示名をまとめて編集。
        # カラム全体が対象なので、項目メニューよりこちらの方が自然な入口。
        dn_act = menu.addAction(glyph_icon("🏷"), tr("表示名の変更...", "ChangeDisplayEdit..."))
        dn_act.triggered.connect(
            lambda _c=False, d=folder: self._open_display_names(d))
        try:
            menu.exec_(global_pos)
        except AttributeError:
            menu.exec(global_pos)
        finally:
            # r124: メニューは «self の子» なので、捨てないと右クリックの回数だけ
            # 溜まる。溜まった分の QAction が持つショートカット（Delete など）が
            # 多重登録になり、Qt が «あいまい» として弾く／別の項目に当たるため、
            # キー操作が «効かない／二度目で効く» 壊れ方になっていた。
            try:
                menu.deleteLater()
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py menu.deleteLater")

    def _run_integration_action(self, cb, extra=None, label="", paths=None):
        """連携操作の実行（外部GUIの起動は即返る／CLIは内部でワーカー化済み）。
        r58: extra["confirm"] があれば実行前に Yes/No 確認。結果は manager の
        action_finished（→ _on_integration_action_finished）で通知される。
        数秒後に状態を再取得して表示へ反映する。"""
        extra = extra or {}
        if extra.get("confirm"):
            ret = QMessageBox.question(
                self, "⎇ " + (label or tr("連携", "Integration")), extra["confirm"],
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        try:
            cb()
        except Exception as e:
            _mfm_log("integration action error: %r" % (e,))
        try:
            # r72: 対象ファイルのフォルダ全部（平坦ビューは複数階層）を再取得
            targets = list(paths or []) + [self._current_path]
            QTimer.singleShot(3000, lambda: self._refresh_integration_status(targets))
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:1790 _run_integration_action")

    def _refresh_integration_status(self, paths):
        try:
            from core.integrations import get_manager
            mgr = get_manager()
            dirs = set()
            for p in paths or []:
                if p:
                    dirs.add(os.path.dirname(p))
                    dirs.add(p)
            for d in dirs:
                mgr.refresh(d)
        except Exception as e:
            _mfm_log("integration refresh error: %r" % (e,))

    def _show_flat_context_menu(self, pos: QPoint):
        """平坦ビューの右クリックメニュー（通常カラムと同仕様）。"""
        try:
            paths = self._flat_col.selected_paths()
        except Exception:
            paths = []
        if not paths:
            return
        gpos = self._flat_col._view.viewport().mapToGlobal(pos)
        self._popup_context_menu(paths, gpos)

    def _open_display_names(self, directory: str):
        """表示名の編集ウィンドウを開く（r116）。

        **失敗を黙って握り潰さない。** 以前はここで例外が出ると «押しても
        何も起きない» になり、原因が追えなかった。"""
        try:
            if not directory or not os.path.isdir(directory):
                QMessageBox.information(
                    self, "表示名",
                    "対象のフォルダを特定できませんでした。")
                return
            self._column_view._open_display_name_dialog(directory)
        except Exception as e:
            import traceback
            _mfm_warn("display name dialog failed: %r\n%s"
                      % (e, traceback.format_exc()))
            QMessageBox.critical(self, tr("表示名", "Display Names"),
                                 tr("表示名ウィンドウを開けませんでした:\n%s",
                                    "Could not open the display-name window:\n%s") % e)

    def _display_name_target(self, paths):
        """表示名を編集する «対象ディレクトリ»（r115）。

        カラムが表示しているフォルダ（＝右クリックした項目の親）を対象にする。
        そこに並ぶサブフォルダの表示名をまとめて編集するため。"""
        try:
            if paths:
                first = paths[0].rstrip("/\\")
                parent = os.path.dirname(first)
                if parent and os.path.isdir(parent):
                    return parent
            cur = self._current_path or ""
            return cur if os.path.isdir(cur) else ""
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _display_name_target")
            return ""

    def _popup_context_menu(self, paths, global_pos):
        menu = _bp().QMenu(self)
        is_maya = all(Path(p).suffix.lower() in MAYA_EXTENSIONS for p in paths)
        is_single = len(paths) == 1
        is_dir = is_single and os.path.isdir(paths[0])

        # ── 複数フォルダの平坦結果表示（2つ以上フォルダ選択時） ─────────
        sel_dirs = [p for p in paths if os.path.isdir(p)]
        if len(sel_dirs) >= 2:
            flat_act = menu.addAction(glyph_icon("▤"), tr("選択フォルダを平坦結果表示", "Show selection as flat list"))
            flat_act.triggered.connect(lambda: self._on_flat_request(sel_dirs))
            menu.addSeparator()

        # ── DCC actions（Maya / Blender、r65） ─────────────────────────
        # 送り先を明示するため _dcc_callback(app, action, paths) を使う。
        # 「開く」はネイティブ形式かつ単一のみ。インポート/リファレンス(リンク)は
        # 複数対応（リファレンスはデフォルトNamespaceでダイアログ無し）。
        from core.file_operations import (BLENDER_EXTENSIONS)
        exts = {Path(p).suffix.lower() for p in paths}
        dcc_cb = getattr(self, "_dcc_callback", None)
        n = len(paths)
        cnt = ("（%d 件）" % n) if n > 1 else ""
        cnt_en = (" (%d files)" % n) if n > 1 else ""
        added_dcc = False
        # r80: 交換形式（.fbx/.obj/.abc/.usd…）はヘッダで選択中の DCC 分だけ出す。
        # ネイティブ形式（.ma/.mb → Maya、.blend → Blender）は選択中 DCC に関係なく出す。
        # 以前は交換形式で Maya と Blender の両方が並んでいた（仕様外）。
        dcc_target = self.current_dcc_target()
        show_maya = bool(exts & MAYA_EXTENSIONS) or dcc_target == "maya"
        show_blender = bool(exts & BLENDER_EXTENSIONS) or dcc_target == "blender"
        # r91: 各項目は «そのコマンドの対象形式» のときだけ出す（core/dcc_caps.py）。
        # 送信側（MainWindow._dcc_accepts）と同じ表なので «見えるのに送れない» は無い。
        from core import dcc_caps
        # r125: 項目名からアプリ名を外し、«どの DCC か» はアイコンだけで示す
        # （ユーザー指示 2026-10-06）。同じ語が 5 行並ぶのを避け、動作だけを読む。
        from ui.dcc_header import dcc_app_icon
        if callable(dcc_cb) and exts and show_maya:
            m_added = False
            if is_single and exts <= dcc_caps.exts("maya", "open"):
                a = menu.addAction(dcc_app_icon("maya"), tr("開く", "Open"))
                a.setData(("maya", "open"))       # r125: 項目名から DCC が消えたので
                a.triggered.connect(lambda _c=False: dcc_cb("maya", "open", list(paths)))
                m_added = True
            if exts <= dcc_caps.exts("maya", "import"):
                a = menu.addAction(dcc_app_icon("maya"),
                                   tr("インポート" + cnt, "Import" + cnt_en))
                a.setData(("maya", "import"))
                a.triggered.connect(lambda _c=False: dcc_cb("maya", "import", list(paths)))
                m_added = True
            if exts <= dcc_caps.exts("maya", "reference"):
                a = menu.addAction(dcc_app_icon("maya"),
                                   tr("リファレンス" + cnt, "Reference" + cnt_en))
                a.setData(("maya", "reference"))
                # 単一は Namespace を確認（従来どおり）。複数は一括（ダイアログ無し）
                ref_action = "reference_ask" if is_single else "reference"
                a.triggered.connect(lambda _c=False: dcc_cb("maya", ref_action, list(paths)))
                m_added = True
            added_dcc = added_dcc or m_added
        if callable(dcc_cb) and exts and show_blender:
            b_items = []
            if is_single and exts <= dcc_caps.exts("blender", "open"):
                b_items.append((tr("開く", "Open"), "open"))
            if exts <= dcc_caps.exts("blender", "import"):
                # Blender の取り込みは Append、参照は Link。アプリ側の呼び名を使う。
                b_items.append((tr("アペンド" + cnt, "Append" + cnt_en), "import"))
            if exts <= dcc_caps.exts("blender", "reference"):
                b_items.append((tr("リンク" + cnt, "Link" + cnt_en), "reference"))
            if b_items and added_dcc:
                menu.addSeparator()
            for text, act in b_items:
                a = menu.addAction(dcc_app_icon("blender"), text)
                a.setData(("blender", act))
                a.triggered.connect(lambda _c=False, ac=act: dcc_cb("blender", ac, list(paths)))
            added_dcc = added_dcc or bool(b_items)
        if added_dcc:
            menu.addSeparator()
        # r70: 単一のシーン/書き出し形式ファイル → その名前で保存／書き出し
        if is_single and not is_dir and self._add_dcc_save_actions(menu, paths[0]):
            menu.addSeparator()

        # ── General actions ───────────────────────────────────────────
        if is_single:
            open_ext_act = menu.addAction(glyph_icon("🖥"), tr("関連付けアプリで開く", "Open with default app"))
            open_ext_act.triggered.connect(lambda: _bp().open_with_default_app(paths[0]))

            reveal_act = menu.addAction(glyph_icon("📁"), tr("エクスプローラーで表示", "Show in Explorer"))
            reveal_act.triggered.connect(lambda _c=False, t=paths[0]: self._reveal_path(t))
            menu.addSeparator()

        # ── 外部サービス連携（該当ワークスペースのプロバイダだけ表示） ────
        try:
            from core.integrations import get_manager
            mgr = get_manager()
            groups = mgr.actions_for(paths) if mgr.enabled else []
        except Exception:
            groups = []
        if groups:
            for grp in groups:
                # r142: 第 3 要素はプロバイダの key（古い形式は 2 要素）
                label, acts = grp[0], grp[1]
                pkey = grp[2] if len(grp) > 2 else ""
                sub = menu.addMenu(integration_icon(pkey), label)
                sub.setToolTipsVisible(True)
                for item in acts:
                    text, cb = item[0], item[1]
                    extra = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
                    if text is None:
                        sub.addSeparator()
                        continue
                    a = sub.addAction(text)
                    # r58: 事前チェック（実行不可＝灰色＋理由 / 確認文）
                    if extra.get("enabled") is False:
                        a.setEnabled(False)
                        if extra.get("tooltip"):
                            a.setToolTip(extra["tooltip"])
                            a.setText(text + tr("（不可）", " (unavailable)"))
                        continue
                    a.triggered.connect(
                        lambda _c=False, f=cb, e=extra, l=label, ps=list(paths):
                        self._run_integration_action(f, e, l, ps))
            refresh_act = menu.addAction(glyph_icon("🔄"), tr("連携状態を更新", "Refresh integration status"))
            refresh_act.triggered.connect(
                lambda: self._refresh_integration_status(paths))
            menu.addSeparator()

        # ── パスをコピー（単数/複数対応） ─────────────────────────────
        copy_path_act = menu.addAction(glyph_icon("📋"), tr("ファイルパスをコピー", "Copy file path"))
        copy_path_act.triggered.connect(lambda: self._copy_paths_to_clipboard(paths))
        menu.addSeparator()

        # ── Bookmark ─────────────────────────────────────────────────
        bm_act = menu.addAction(glyph_icon("⭐"), tr("ブックマークに追加", "Add to bookmarks"))
        bm_act.triggered.connect(lambda: self._add_to_bookmarks(paths))
        menu.addSeparator()

        # ── File ops ─────────────────────────────────────────────────
        copy_act = menu.addAction(glyph_icon("📋"), tr("コピー...", "Copy..."))
        copy_act.triggered.connect(lambda: self._copy_dialog(paths))

        move_act = menu.addAction(glyph_icon("✂"), tr("移動...", "Move..."))
        move_act.triggered.connect(lambda: self._move_dialog(paths))

        dup_act = menu.addAction(glyph_icon("⧉"), tr("複製...", "Duplicate...") + "\tCtrl+D")
        dup_act.triggered.connect(lambda _c=False: self._duplicate_selected(list(paths)))

        rename_act = menu.addAction(glyph_icon("✏"), tr("名前変更", "Rename") + "\tF2")
        rename_act.triggered.connect(
            lambda _c=False: self._rename_inline(paths[0]) if paths else None)
        rename_act.setEnabled(is_single)

        # r101: 選択したものだけをバッチリネームの対象にする
        # （ツールメニューからだと «現在の選択» を拾い直すため、右クリックした
        #  対象と食い違うことがあった）
        batch_act = menu.addAction(glyph_icon("✏"), tr("バッチリネーム...", "Batch Rename..."))
        batch_act.triggered.connect(
            lambda _c=False, ps=list(paths): self.batch_rename_requested.emit(ps))

        # r115: 表示名（実体名はそのままで、カラムへ出す名前だけ変える）
        dn_dir = self._display_name_target(paths)
        dn_act = menu.addAction(glyph_icon("🏷"), tr("表示名の変更...", "ChangeDisplayEdit..."))
        dn_act.triggered.connect(
            lambda _c=False, d=dn_dir: self._open_display_names(d))
        dn_act.setEnabled(bool(dn_dir))

        menu.addSeparator()

        del_act = menu.addAction(glyph_icon("🗑"), tr("削除", "Delete"))
        del_act.triggered.connect(lambda: self._delete_confirm(paths))
        # r124: setShortcut は «本物のショートカット登録» なので使わない
        # （メニューを開くたびに Delete が増え、本体の Delete と衝突した）。
        # 他の項目と同じく表示だけにする。
        del_act.setText(del_act.text() + "\tDelete")

        menu.addSeparator()

        # ── Properties ───────────────────────────────────────────────
        # r124: 複数選択でも出す（読み取り専用の変更だけは複数でも行える）。
        prop_act = menu.addAction(glyph_icon("ℹ"),
                                  tr("プロパティ" + cnt,
                                     "Properties" + cnt_en))
        prop_act.triggered.connect(lambda _c=False, ps=list(paths):
                                   self._show_properties(ps))

        try:
            menu.exec_(global_pos)
        except AttributeError:
            menu.exec(global_pos)
        finally:
            # r124: メニューは «self の子» なので、捨てないと右クリックの回数だけ
            # 溜まる。溜まった分の QAction が持つショートカット（Delete など）が
            # 多重登録になり、Qt が «あいまい» として弾く／別の項目に当たるため、
            # キー操作が «効かない／二度目で効く» 壊れ方になっていた。
            try:
                menu.deleteLater()
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py menu.deleteLater")

    def _add_to_bookmarks(self, paths: List[str]):
        # 実際の登録は MainWindow 側の BookmarkManager で行う（signalで依頼）
        if paths:
            self.bookmark_requested.emit(list(paths))
            self.status_message.emit(
                tr("ブックマークに追加: %d 件", "Bookmarked %d item(s)") % len(paths))

    def _copy_paths_to_clipboard(self, paths: List[str]):
        """選択中のフルパスをクリップボードへコピー（複数は改行区切り）。"""
        from core.compat import QApplication
        if not paths:
            return
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText("\n".join(paths))
        self.status_message.emit(
                tr("パスをコピー: %d 件", "Copied %d path(s)") % len(paths))
