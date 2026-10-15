# -*- coding: utf-8 -*-
"""BrowserPanel の 名前変更（その場編集／ダイアログ／新規作成）と、その後の表示追従（r146 で分離）。

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


class RenameMixin:
    """名前変更（その場編集／ダイアログ／新規作成）と、その後の表示追従。"""

    @staticmethod
    def _unique_new_name(folder: str, base: str, ext: str = "") -> str:
        """Explorer 風: «新しいフォルダ» → «新しいフォルダ (2)» …"""
        name = base + ext
        n = 2
        while os.path.lexists(os.path.join(folder, name)):
            name = "%s (%d)%s" % (base, n, ext)
            n += 1
        return name

    def _create_new_item(self, kind: str, folder: str = None):
        """kind: "folder" / "text"。作成後はそのままインライン名前変更モードへ。
        QFileSystemModel が新項目を表示するまで（ウォッチャ経由・非同期）少し
        待ってから _rename_inline を開く。"""
        folder = folder or self._active_column_folder()
        if not folder or not os.path.isdir(folder):
            self.status_message.emit(tr("作成先フォルダを特定できません",
                                       "Cannot determine the target folder"))
            return
        try:
            if kind == "folder":
                name = self._unique_new_name(folder, "新しいフォルダ")
                path = os.path.join(folder, name)
                os.mkdir(path)
            else:
                name = self._unique_new_name(folder, "新しいテキスト ドキュメント", ".txt")
                path = os.path.join(folder, name)
                with open(path, "x", encoding="utf-8"):
                    pass
        except Exception as e:
            QMessageBox.critical(self, tr("新規作成", "New item"), str(e))
            return
        self.status_message.emit(tr("作成: %s", "Created: %s") % name)
        self._last_created_path = path
        self._refresh_flat_view()
        self._rename_when_visible(path)

    def _rename_when_visible(self, path: str, tries: int = 25):
        """新規項目がカラムに現れたらインライン名前変更を開く（最大 ~2.5 秒待つ）。"""
        def attempt(left):
            try:
                idx = self._column_view._proxy_index_for_path(path)
                if idx.isValid():
                    views = [v for v in self._column_view.findChildren(QListView)
                             if getattr(v, "_mfm_header", None) is not None and v.isVisible()
                             and v.rootIndex() == idx.parent()
                             and v.visualRect(idx).isValid() and not v.visualRect(idx).isEmpty()]
                    if views:
                        # 見えるように選択してから編集（列はスライドさせない）
                        try:
                            self._column_view._prune_selection_to_single(idx)
                            views[0].scrollTo(idx)
                        except Exception as _e:
                            _swallow(_e, "ui/browser_panel.py:2093 attempt")
                        self._rename_inline(path)
                        return
            except Exception as e:
                _mfm_log("rename_when_visible error: %r" % (e,))
            if left > 0:
                QTimer.singleShot(100, lambda: attempt(left - 1))
            else:
                # カラムに出てこない（フィルタ等）→ ダイアログで名前変更
                self._rename_dialog([path])
        attempt(tries)

    def _rename_path(self, src: str, dst: str):
        """リネーム本体（r98）。Windows の «アクセスが拒否されました» は
        監視ハンドル／OneDrive 同期が原因のことが多いので、手放し＋再試行＋
        シェル経由フォールバックまで面倒を見る。"""
        from core.file_operations import rename_path as _rn
        ok, err = _rn(src, dst,
                      release_cb=lambda lv=1: self._release_fs_handles(src, lv))
        if not ok:
            _mfm_warn("rename failed: %s -> %s : %r" % (src, dst, err))
        # r146（ユーザー指示 2026-10-15）:
        #   「リネームでも先出しのカラム再サーチの様な動きをします。
        #     この挙動は本来どんな操作の結果でも起きてはならないものとして
        #     全て修正してください。」
        # release_cb が走ると見張りが落ちる（Windows のハンドルを手放すため
        # 必要）。**落としたら必ず付け直す。** 付け直さないと以後その場所の
        # 変化が届かず «古い名前が残る» ことになる。
        self._rearm_fs_watch([os.path.dirname(src.rstrip("/\\")),
                              os.path.dirname(dst.rstrip("/\\"))])
        return ok, err

    @staticmethod
    def _rename_error_text(src: str, err) -> str:
        werr = getattr(err, "winerror", None)
        if werr in (5, 32):
            return ("名前を変更できませんでした（アクセスが拒否されました）。\n\n"
                    "%s\n\n"
                    "このフォルダ／ファイルを他のプログラムが使用中の可能性が"
                    "あります。よくある原因:\n"
                    "・OneDrive が同期中（同期の完了を待つ／一時停止する）\n"
                    "・中のファイルをアプリ（Maya 等）が開いている\n"
                    "・エクスプローラーや端末が同じフォルダを開いている"
                    % (err,))
        return str(err)

    def _rename_inline(self, path: str = None) -> bool:
        """F2 / 右クリック「名前変更」: カラムの項目をその場で編集する（r61）。
        QFileSystemModel は readOnly=False なので EditRole の setData がそのまま
        リネームになる（カラムは NoEditTriggers のためクリックでは開かない）。
        カラム内に見つからない時（平坦ビュー等）はダイアログへ退避。"""
        try:
            if path is None:
                targets = self._operation_targets()
                if len(targets) != 1:
                    self.status_message.emit(tr("名前変更は1件だけ選択してください",
                                            "Select exactly one item to rename"))
                    return False
                path = targets[0]
            idx = self._column_view._proxy_index_for_path(path)
            if idx.isValid():
                views = [v for v in self._column_view.findChildren(QListView)
                         if getattr(v, "_mfm_header", None) is not None and v.isVisible()
                         and v.rootIndex() == idx.parent()]
                if not views and self._thumb_view.isVisible():
                    views = [self._thumb_view]
                if views:
                    # current は動かさない（ファイルを current にすると
                    # プレビュー列生成＝カラムのスライドを誘発する）。
                    # Qt の view.edit() は使わない: QFileSystemModel::flags は
                    # 書き込み権限の無いファイル（Perforce 同期の read-only）に
                    # ItemIsEditable を付けず、edit() が黙って失敗する（実機で
                    # 「F2 が効かない」原因）。自前の QLineEdit をその場に重ねる。
                    self._open_inline_editor(views[0], idx, path)
                    return True
        except Exception as e:
            _mfm_log("rename inline error: %r" % (e,))
        self._rename_dialog([path] if path else [])
        return False

    def _open_inline_editor(self, view, idx, path: str):
        """項目の矩形に QLineEdit を重ねて名前を編集する。Enter=確定 / Esc=取消 /
        フォーカス喪失=確定（Explorer 準拠）。確定は os.rename（モデルの flags に
        依存しない）。"""
        old_editor = getattr(self, "_inline_editor", None)
        if old_editor is not None:
            try:
                old_editor.close()
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:2380 _open_inline_editor")
        # r146: 開いた瞬間に «ゆっくり 2 回クリック» の予約を捨てる。
        # 残っていると、打っている最中に編集欄を作り直してしまう。
        self._inline_editing = True
        try:
            self._column_view.set_inline_editing(True)
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _open_inline_editor(reclick off)")
        rect = view.visualRect(idx)
        if not rect.isValid():
            self._rename_dialog([path])
            return
        vp = view.viewport()
        # r146（実機 2026-10-15）: **編集欄はカラムの子にしない。**
        # カラム（とそのビューポート）は、モデルの見張りを付け直したり
        # 表示を読み直したりすると作り直される。その巻き添えで編集欄ごと
        # 消え、打ちかけの名前が失われていた（回帰スイートでは
        # «Internal C++ object already deleted» として出た）。
        # パネルの子にして、カラムの作り直しから切り離す。
        ed = QLineEdit(self)
        ed.setObjectName("mfmInlineRename")
        name = os.path.basename(path.rstrip("/\\"))
        ed.setText(name)
        # アイコン分を空けて項目の上に重ねる。エディタは «不透明»（QSS
        # #mfmInlineRename）にして下の項目名が透けないようにする。
        try:
            isz = view.iconSize().width()
        except Exception:
            isz = -1
        dec = (isz if isz > 0 else 16) + 6
        ed.setAutoFillBackground(True)
        # 位置はカラムの座標なので、パネルの座標へ移し替える
        try:
            tl = self.mapFromGlobal(vp.mapToGlobal(rect.topLeft()))
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py _open_inline_editor(map)")
            tl = rect.topLeft()
        ed.setGeometry(tl.x() + dec, tl.y(),
                       max(80, rect.width() - dec), rect.height())
        ed.show()
        ed.raise_()
        ed.setFocus(Qt.OtherFocusReason)
        # Explorer 同様、拡張子を除いた部分を選択
        stem_len = len(name) if os.path.isdir(path) else len(os.path.splitext(name)[0])
        ed.setSelection(0, stem_len if stem_len > 0 else len(name))
        self._inline_editor = ed
        state = {"done": False}

        def finish(commit: bool):
            if state["done"]:
                return
            state["done"] = True
            new_name = ed.text().strip()
            try:
                ed.hide()
                ed.deleteLater()
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py:2417 finish")
            self._inline_editor = None
            self._inline_editing = False
            try:
                self._column_view.set_inline_editing(False)
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py finish(reclick on)")
            if not commit or not new_name or new_name == name:
                return
            if any(c in new_name for c in '\\/:*?"<>|'):
                QMessageBox.warning(self, tr("名前変更", "Rename"),
                                    tr("ファイル名に使えない文字が含まれています。",
                                       "The name contains characters that cannot be used."))
                return
            new_path = os.path.join(os.path.dirname(path), new_name)
            if os.path.lexists(new_path):
                QMessageBox.warning(self, tr("名前変更", "Rename"),
                                    tr("同名の項目が既にあります:\n%s",
                                       "An item with that name already exists:\n%s")
                                    % new_name)
                return
            # r146: 走っているコピー／移動／削除の対象は触らせない
            if not self._busy_guard([path, new_path], tr("名前変更", "Rename")):
                return
            from core.undo_stack import RenameOp
            ok, err = self._rename_path(path, new_path)
            if not ok:
                QMessageBox.critical(self, tr("名前変更", "Rename"),
                                     self._rename_error_text(path, err))
                return
            try:
                self._undo_stack().push(RenameOp(path, new_path))
                self._on_fs_file_renamed(os.path.dirname(path), name, new_name, record=False)
            except Exception as e:
                _mfm_log("rename post error: %r" % (e,))

        ed.returnPressed.connect(lambda: finish(True))

        def _on_focus_out():
            """フォーカスが外れた＝確定（Explorer 準拠）。

            r146（実機 2026-10-15）: ただし **誰も受け取っていない時は別**。
            カラムが組み直されたりして «こちらの都合で» 焦点が外れただけの
            時まで確定すると、打ちかけの名前が勝手に決まってしまう
            （回帰スイートでは、打っている最中に編集欄が消える形で出た）。
            受け手がいない時は焦点を取り戻して編集を続ける。"""
            # r146（実機 2026-10-15）: **こちらの都合で焦点が外れた時は確定
            # しない。** カラムは、見張りの付け直しや読み直しで作り直される。
            # その拍子に焦点が移っただけで «確定» にすると、打ちかけの名前が
            # 勝手に決まり、編集欄も消える（打った内容が失われる）。
            # 確定するのは «別のウィンドウへ移った» 時だけにする。
            # 同じウィンドウの中にいる限りは編集を続けられる
            # （決めるのは Enter、やめるのは Esc）。
            app = QApplication.instance()
            aw = app.activeWindow() if app is not None else None
            mine = self.window()
            if aw is not None and aw is not mine:
                finish(True)            # 別のウィンドウへ移った＝確定
                return
            def _refocus():
                try:
                    if not state["done"] and is_alive(ed) and ed.isVisible():
                        ed.setFocus(Qt.OtherFocusReason)
                except RuntimeError:
                    pass
            QTimer.singleShot(0, _refocus)
        ed.editingFinished.connect(_on_focus_out)

        # Tab / Shift+Tab: 確定して同じカラムの次／前の項目の名前変更へ（r67）
        def _neighbor_path(step: int):
            """確定前に «同じカラムの隣» の項目を調べる。

            r146（実機 2026-10-14、faulthandler のスタックで特定）:
              ここが ACCESS_VIOLATION（3221225477）で落ちていた。
              捕まえた ``idx`` は «エディタを開いた時の» index で、その後に
              カラムが組み替わると、ビューのモデルも index も別物になる。
              **違うモデルの index を rowCount に渡す** と、Qt は
              「index from wrong model passed to mapToSource」を出した上で
              中身の分からないポインタを辿り、運が悪いとプロセスごと落ちる。
              毎回 «今のビューのモデル» で引き直し、食い違っていたら何も
              しない（Tab での移動を諦めるだけで、落ちるよりずっとよい）。
            """
            try:
                if not is_alive(view):
                    return None
                m = view.model()
                if m is None or not is_alive(m):
                    return None
                cur = self._column_view._proxy_index_for_path(path)
                if not cur.isValid() or cur.model() is not m:
                    return None
                parent = cur.parent()
                row = cur.row() + step
                if row < 0 or row >= m.rowCount(parent):
                    return None
                return self._resolve_path(m.index(row, 0, parent))
            except Exception as _e:
                _swallow(_e, "ui/browser_panel.py _neighbor_path")
                return None

        def _commit_and_move(step: int):
            nxt = _neighbor_path(step)
            finish(True)
            if nxt:
                QTimer.singleShot(60, lambda: self._rename_inline(nxt))

        class _KeyFilter(_QtCore.QObject):
            def eventFilter(_s, obj, ev):
                if ev.type() == _QtCore.QEvent.KeyPress:
                    k = ev.key()
                    if k == Qt.Key_Escape:
                        finish(False)
                        return True
                    if k in (Qt.Key_Return, Qt.Key_Enter):
                        # r72: ここで消費する。QLineEdit の returnPressed に任せると
                        # キーが親の QListView へ伝播して activated が発火し、
                        # _on_item_activated → _set_current_path でパス欄が
                        # フォルダへ戻る（テストで確定）。実機ではさらに
                        # 「Enter でファイルが開く／フォルダに入る」事故になる。
                        finish(True)
                        return True
                    if k == Qt.Key_Backtab or (k == Qt.Key_Tab and
                                               ev.modifiers() & Qt.ShiftModifier):
                        _commit_and_move(-1)
                        return True
                    if k == Qt.Key_Tab:
                        _commit_and_move(+1)
                        return True
                return False
        ed._mfm_keys = _KeyFilter(ed)
        ed.installEventFilter(ed._mfm_keys)

    def _on_fs_file_renamed(self, folder: str, old_name: str, new_name: str,
                            record: bool = True):
        """リネームの反映: パス欄を追従し、必要なら Undo 記録を積む。
        QFileSystemModel.fileRenamed（setData 経由）からは record=True、
        自前のインライン編集（os.rename 済み・記録済み）からは record=False。"""
        try:
            old_p = os.path.join(folder, old_name).replace("\\", "/")
            new_p = os.path.join(folder, new_name).replace("\\", "/")
            if self._addr_bar.text().replace("\\", "/") == old_p:
                self._addr_bar.setText(new_p)
            if record:
                from core.undo_stack import RenameOp
                self._undo_stack().push(RenameOp(old_p, new_p))
            self.status_message.emit(
                tr("名前変更: %s → %s", "Renamed: %s -> %s") % (old_name, new_name))
            # r98: 表示中のフォルダ自身／その祖先を変更した場合は新しい場所へ追従
            cur = (self._current_path or "").replace("\\", "/").rstrip("/")
            o = old_p.rstrip("/")
            if cur == o or cur.startswith(o + "/"):
                self.navigate_to(new_p + cur[len(o):])
                return
            # r98: リネーム後は «新しい名前の項目» を選択状態にする
            self._select_when_visible(os.path.join(folder, new_name))
            # r146（ユーザー報告 2026-10-13）:
            #   「リネームしたら元のファイル名が表示されたまま
            #     新しいファイルの様に結果がでてきます。」
            # QFileSystemModel は «自分が知らないところで起きた rename» を
            # 取りこぼすことがある（監視を手放した直後・ネットワーク/クラウド
            # ドライブ・短時間に連続した変更）。取りこぼすと古い名前の項目が
            # 残ったまま新しい名前が増えるので «新規作成されたように» 見える。
            # 見えている結果を正としてモデル側を確かめ、ずれていたら読み直す。
            self._verify_rename_applied(folder, old_name, new_name)
        except Exception as _e:
            _swallow(_e, "ui/browser_panel.py:2509 _on_fs_file_renamed")

    def _verify_rename_applied(self, folder: str, old_name: str, new_name: str):
        """モデルに古い名前が残っていないか確かめ、残っていたら読み直す（r146）。"""
        def _check():
            try:
                old_p = os.path.join(folder, old_name)
                if os.path.lexists(old_p):
                    return            # 実際にまだある（別物）＝ここの出番ではない
                m = self._fs_model
                idx = m.index(old_p)
                stale = bool(idx.isValid())
                if not stale:
                    return
                _mfm_log("rename: モデルに古い名前が残っていたので読み直す %r"
                         % (old_p,))
                self._resync_dir(folder)
            except Exception as e:
                _mfm_log("rename verify error: %r" % (e,))
        QTimer.singleShot(self.RENAME_VERIFY_MS, _check)

    def _rename_dialog(self, paths: List[str]):
        if len(paths) != 1:
            return
        old = Path(paths[0])
        new_name, ok = QInputDialog.getText(
            self, "名前変更", "新しい名前:", text=old.name
        )
        if ok and new_name:
            new_path = old.parent / new_name
            if not self._busy_guard([str(old), str(new_path)],
                                    tr("名前変更", "Rename")):
                return
            from core.undo_stack import RenameOp
            ok, err = self._rename_path(str(old), str(new_path))
            if not ok:
                QMessageBox.critical(self, tr("名前変更", "Rename"),
                                     self._rename_error_text(str(old), err))
                return
            try:
                self._undo_stack().push(RenameOp(str(old), str(new_path)))
                # r98: 選択・パス欄追従・ステータス表示は共通の後処理へ
                self._on_fs_file_renamed(str(old.parent), old.name, new_name,
                                         record=False)
            except Exception as e:
                _mfm_log("rename post error: %r" % (e,))
