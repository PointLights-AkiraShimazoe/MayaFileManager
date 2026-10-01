"""
MainWindow の DCC 連携部（Mixin）
===========
The central QMainWindow that hosts all panels as dockable widgets.

Layout (default)
----------------
  ┌─────────────────────────────────────────────────────┐
  │  MenuBar                                            │
  │  ToolBar (quick-nav buttons, Maya ver, action mode) │
  ├──────────────┬──────────────────────────┬───────────┤
  │  Bookmark    │                          │ History   │
  │  Panel       │  Browser Panel           │ Panel     │
  │  (dock-left) │  (central)               │(dock-right│
  │              │                          │           │
  │              │                          │           │
  ├──────────────┴──────────────────────────┴───────────┤
  │  StatusBar (path | Maya version | message)          │
  └─────────────────────────────────────────────────────┘
"""
from core.diag import swallow as _swallow  # r112

import os
from pathlib import Path
from typing import Optional, List

from core.compat import (
    Qt, Signal, QObject,
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QComboBox, QToolButton, QMenu, QMenuBar, QSplitter, QMessageBox, QFileDialog, QInputDialog, QDialog,
    QApplication, QPushButton, QTextEdit,
    QTimer
)
from core.settings_manager import SettingsManager
from core.bookmark_manager import BookmarkManager
from core.thumbnail_generator import ThumbnailManager
from core.maya_version import (
    MayaInstallation, find_installed_maya_versions,
    is_running_inside_maya, get_current_maya_version,
    launch_maya
)
from core.maya_bridge import (MayaBridge, escape_path, scan_open_ports,
                              find_free_port, IDENTIFY_CODE, parse_identify)
from core.blender_version import (BlenderInstallation, find_installed_blender_versions,
                                  launch_blender)
from core import blender_bridge as _bl
from core.file_operations import dcc_for_path
from core.i18n import tr
def _tv():
    """テーマトークン（色・形状・書体）。**遅延 import** すること。
    トップレベルで core.theme_engine から名前を取り込むと、Maya 内の
    ホットリロードや部分再読込で «partially initialized module» に当たり
    ImportError（cannot import name 'qss_vars'）になる（r82 で実害）。"""
    from core.theme_engine import qss_vars
    return qss_vars()
from ui.preset_editor import ReferencePresetEditor
from ui.settings_dialog import SettingsDialog
from ui.batch_rename_dialog import BatchRenameDialog
from ui.reference_editor import ReferenceEditor


# ---------------------------------------------------------------------------
# History Panel (inline – keeps it self-contained)
# ---------------------------------------------------------------------------



class MainWindowDccMixin:
    """MainWindow の **DCC 連携・ブリッジ・フリーズ監視** 部分（r114）。

    MainWindow から «メソッドをそのまま» 移しただけで、処理は変えていない。
    self._xxx は MainWindow.__init__ が用意する前提（Mixin なので同じ
    インスタンスに属する）。MainWindow 以外から継承しないこと。
    """

    def _dcc_label(self, dcc=None) -> str:
        return "Blender" if (dcc or self._dcc) == "blender" else "Maya"

    def _populate_version_combos(self):
        """Maya / Blender のバージョンコンボをそれぞれ埋める（r67: 両方常時表示）。"""
        mc, bc = self._maya_combo, self._blender_combo
        mc.blockSignals(True)
        mc.clear()
        # r119: 起動プロファイルがあればそれを並べる（同じバージョンの
        # 引数違いを «別の行» として選べるようにするため。ユーザー指示）。
        # 無ければ従来どおり «検出されたバージョンそのまま»。
        self._maya_profiles = []
        try:
            profiles = self._sm.get_maya_launch_profiles()
        except Exception as _e:
            _swallow(_e, "ui/main_window_dcc.py _populate_version_combos")
            profiles = []
        by_ver = {str(i.version): i for i in self._maya_installs}
        if profiles:
            for prof in profiles:
                inst = by_ver.get(str(prof.get("version")))
                label = prof.get("label") or (
                    "Maya %s" % prof.get("version"))
                if inst is None:
                    label += tr("（未検出）", " (not found)")
                mc.addItem(label, inst)
                tip = tr("Maya %s", "Maya %s") % prof.get("version")
                if prof.get("args"):
                    tip += "\n" + prof["args"]
                mc.setItemData(mc.count() - 1, tip, Qt.ToolTipRole)
                self._maya_profiles.append(prof)
        else:
            for inst in reversed(self._maya_installs):
                mc.addItem(f"Maya {inst.version}", inst)
                self._maya_profiles.append(None)
        if not mc.count():
            mc.addItem(tr("Maya（未検出）", "Maya (not found)"), None)
            self._maya_profiles.append(None)
        sel = -1
        if self._maya_inst:
            for i in range(mc.count()):
                if mc.itemData(i) is self._maya_inst:
                    sel = i
        if sel >= 0:
            mc.setCurrentIndex(sel)
        if not isinstance(self._maya_inst, MayaInstallation):
            d = mc.currentData()
            if isinstance(d, MayaInstallation):
                self._maya_inst = d
        mc.blockSignals(False)

        bc.blockSignals(True)
        bc.clear()
        for inst in reversed(self._blender_installs):
            # r80: Store 版と通常インストール版が同じ版で同居した時に区別できるよう
            # WindowsApps 配下は「(Store)」を添える（ツールチップに exe パス）
            store = "windowsapps" in str(inst.executable).lower()
            bc.addItem(f"Blender {inst.version}" + (" (Store)" if store else ""), inst)
            bc.setItemData(bc.count() - 1, str(inst.executable), Qt.ToolTipRole)
        if not self._blender_installs:
            bc.addItem(tr("Blender（未検出）", "Blender (not found)"), None)
        d = bc.currentData()
        self._blender_inst = d if isinstance(d, BlenderInstallation) else None
        bc.blockSignals(False)

    def _on_blender_version_changed(self, idx: int):
        inst = self._blender_combo.itemData(idx)
        self._blender_inst = inst if isinstance(inst, BlenderInstallation) else None

    def _on_dcc_switched(self, dcc: str):
        dcc = "blender" if dcc == "blender" else "maya"
        if dcc == self._dcc:
            return
        self._dcc = dcc
        self._sm.set("dcc_target", dcc, save=False)
        self.statusBar().showMessage(
            tr("操作対象を %s に切り替えました", "Target DCC: %s") % self._dcc_label())

    def _launch_dcc(self):
        if self._dcc == "blender":
            self._launch_blender()
        else:
            self._launch_maya()

    def _launch_blender(self):
        inst = self._blender_inst
        if not inst:
            QMessageBox.warning(self, "Blender",
                                tr("Blender が見つかりません。ポータブル版は環境変数 "
                                   "MFM_BLENDER_EXE に blender.exe のパスを設定してください。",
                                   "Blender not found. For a portable build, set "
                                   "MFM_BLENDER_EXE to blender.exe."))
            return
        try:
            port = _bl.find_free_port()
            launch_blender(inst, command_port=port)
            self._bl_bridge.set_port(port)
            self._sm.set("blender_command_port", int(port), save=False)
            self.statusBar().showMessage(
                tr("Blender %s を起動しました（連携ポート :%d）",
                   "Launched Blender %s (bridge port :%d)") % (inst.version, port))
            for delay in (6000, 15000, 30000):
                QTimer.singleShot(delay, self._refresh_connections)
        except Exception as e:
            QMessageBox.critical(self, tr("起動エラー", "Launch Error"), str(e))

    def _refresh_connections(self):
        """Maya / Blender 両方の接続一覧を更新する（r67: 2つのコンボを常時表示）。"""
        if not self._inside_maya:
            self._refresh_maya_connections()
        self._refresh_blender_connections()

    # ---- 起動済みMaya/Blenderの列挙・接続先切替 ---------------------------

    # r78: 識別できた接続の記憶 {port: (label, pid)}。識別がタイムアウトしても
    # （Maya がシーン読込・レンダー・モーダル中で応答できない）一覧から消さず、
    # 前回のラベルに「…」を付けて残す。従来は 1 回の無応答で一覧から落ち、
    # 「Maya の数は変わらないのに更新のたびに増減する／送っても反応しない」に
    # 見えていた（2026-09-16 実機、mfm_maya.log）。
    def _refresh_maya_connections(self):
        """連携ポートを開いているMayaをスキャンし、識別情報つきで一覧化する。
        ソケットI/Oは全てワーカースレッドで行う（UIを止めない）。
        r78: スキャンが走行中なら重ねて走らせない（Maya の commandPort へ
        同時に複数接続すると応答が乱れる）。"""
        import threading
        if self._scan_running.get("maya"):
            return
        self._scan_running["maya"] = True

        def _run():
            items = []
            reported = []          # Maya が答えた userAppDir（UI 側で照合）
            try:
                from core.maya_bridge import (bridge_log, usersetup_path,
                                              is_usersetup_installed,
                                              APP_DIR_CODE, parse_app_dir)
                ports = scan_open_ports()
                try:
                    bridge_log("scan: open ports=%r / userSetup=%r installed=%s"
                               % (ports, usersetup_path(), is_usersetup_installed()))
                except Exception as _e:
                    _swallow(_e, "ui/main_window.py:879 _run")
                for port in ports:
                    label = None
                    try:
                        ok, reply = MayaBridge(port).send_python(
                            IDENTIFY_CODE, timeout=1.0)
                        # 応答にはスクリプト出力等が混入し得るため厳格に検証
                        # （生の応答をそのままラベルにするとレイアウトが壊れる）
                        parsed = parse_identify(reply) if ok else None
                        try:
                            bridge_log("port %d: ok=%s reply=%r parsed=%r"
                                       % (port, ok, (reply or "")[:120], parsed))
                        except Exception as _e:
                            _swallow(_e, "ui/main_window.py:892 _run")
                        pid = None
                        if parsed:
                            ver, scene, pid = parsed
                            scene = os.path.basename(scene)[:40] or \
                                tr("無題", "untitled")
                            label = f"Maya {ver[:20]} — {scene} (:{port})"
                            self._known_conns["maya"][port] = (label, pid)
                            # r119: userSetup.py の «本当の置き場» は Maya に
                            # 聞く。既知フォルダ（OneDrive 等）が Maya の
                            # userAppDir と一致しない環境があり、推測で書くと
                            # «入れたのに効かない» になる。一度聞けば足りる。
                            if not reported:
                                try:
                                    ok2, rep2 = MayaBridge(port).send_python(
                                        APP_DIR_CODE, timeout=1.0)
                                    d = parse_app_dir(rep2) if ok2 else None
                                    if d:
                                        # 照合は UI スレッドで（ダイアログを出し得る）
                                        reported.append(d)
                                except Exception as _e:
                                    _swallow(_e, "ui/main_window_dcc.py _run(appdir)")
                    except Exception:
                        pid = None
                    if label is None and port in self._known_conns["maya"]:
                        # 応答なし＝ビジー。前回の識別を保ち «…» を付ける
                        klabel, kpid = self._known_conns["maya"][port]
                        label, pid = klabel + " …", kpid
                    # (ポート, 表示ラベル, Mayaと確認できたか, PID, dcc)
                    items.append((port, label or f"Maya? (:{port})",
                                  label is not None, pid, "maya"))
                for port in list(self._known_conns["maya"]):
                    if port not in ports:
                        self._known_conns["maya"].pop(port, None)   # ポートが閉じた
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:913 _run")
            finally:
                self._scan_running["maya"] = False
            self._bridge_notify.conn_list.emit(items, "maya")
            if reported:
                # ワーカーからは UI を触らない。次のイベントループで照合する。
                d0 = reported[0]
                QTimer.singleShot(
                    0, lambda d=d0: self._check_bridge_location_against_maya(d))

        threading.Thread(target=_run, daemon=True,
                         name="mfm-maya-scan").start()

    def _refresh_blender_connections(self):
        import threading
        if self._scan_running.get("blender"):
            return
        self._scan_running["blender"] = True

        def _run():
            items = []
            try:
                from core.maya_bridge import bridge_log
                ports = _bl.scan_open_ports()
                bridge_log("scan(blender): open ports=%r" % (ports,))
                for port in ports:
                    label = None
                    pid = None
                    try:
                        ok, reply = _bl.BlenderBridge(port).send_python(
                            _bl.IDENTIFY_CODE, timeout=1.5)
                        parsed = parse_identify(reply) if ok else None
                        bridge_log("blender port %d: ok=%s reply=%r parsed=%r"
                                   % (port, ok, (reply or "")[:120], parsed))
                        if parsed:
                            ver, scene, pid = parsed
                            scene = os.path.basename(scene)[:40] or tr("無題", "untitled")
                            label = f"Blender {ver[:20]} — {scene} (:{port})"
                            self._known_conns["blender"][port] = (label, pid)
                    except Exception as _e:
                        _swallow(_e, "ui/main_window.py:948 _run")
                    if label is None and port in self._known_conns["blender"]:
                        klabel, kpid = self._known_conns["blender"][port]
                        label, pid = klabel + " …", kpid
                    items.append((port, label or f"Blender? (:{port})",
                                  label is not None, pid, "blender"))
                for port in list(self._known_conns["blender"]):
                    if port not in ports:
                        self._known_conns["blender"].pop(port, None)
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:958 _run")
            finally:
                self._scan_running["blender"] = False
            self._bridge_notify.conn_list.emit(items, "blender")

        threading.Thread(target=_run, daemon=True, name="mfm-blender-scan").start()

    def _on_conn_list(self, items, dcc: str = None):
        """接続一覧の反映（DCC ごとのコンボへ）。items の第5要素が dcc。
        空リストは dcc 引数で判別（スキャン側が emit 時に渡す）。"""
        if dcc is None:
            dcc = items[0][4] if items and len(items[0]) > 4 else "maya"
        combo = self._bl_conn_combo if dcc == "blender" else getattr(self, "_conn_combo", None)
        if combo is None:
            return
        bridge = self._bl_bridge if dcc == "blender" else self._bridge
        port_key = "blender_command_port" if dcc == "blender" else "maya_command_port"
        cur_port = bridge.port
        combo.blockSignals(True)
        combo.clear()
        if not items:
            combo.addItem(tr("（%s なし）", "(no %s)") % self._dcc_label(dcc), None)
            combo.setEnabled(False)
            # r119: «起動しているのに出ない» が一番多い詰まり方なので、
            # 原因と次の一手をここで示す（メニューまで辿り着けない人が多い）
            if dcc != "blender":
                combo.setToolTip(tr(
                    "起動中の Maya が見つかりません。\n"
                    "連携ポートは Maya の起動時に開きます。Manager より先に\n"
                    "起動した Maya は、ツール →「起動中の Maya を今すぐ接続...」\n"
                    "で、再起動せずに接続できます。",
                    "No running Maya found.\n"
                    "The bridge port opens when Maya starts. For a Maya that\n"
                    "was already running, use Tools > \u201cConnect a running\n"
                    "Maya now...\u201d to connect without restarting it."))
        else:
            if dcc != "blender":
                combo.setToolTip("")
            combo.setEnabled(True)
            pids = {it[0]: it[3] for it in items if it[3]}
            if dcc == "blender":
                self._bl_conn_pids = pids
            else:
                self._conn_pids = pids
            sel = -1
            first_ok = -1
            for i, it in enumerate(items):
                port, label, identified = it[0], it[1], it[2]
                combo.addItem(label, port)
                combo.setItemData(i, label, Qt.ToolTipRole)
                if port == cur_port:
                    sel = i
                if identified and first_ok < 0:
                    first_ok = i
            if sel < 0:
                # 現在の接続先が一覧に無い場合のみ切り替える。
                # 切替先は «識別できたポート» を優先（別アプリのポートへ
                # 送ってしまうと「何も起きない」ため）。
                sel = first_ok if first_ok >= 0 else 0
                new_port = combo.itemData(sel)
                bridge.set_port(new_port)
                self._sm.set(port_key, int(new_port), save=False)
            combo.setCurrentIndex(sel)
        combo.blockSignals(False)

    def _on_conn_changed(self, idx: int, dcc: str = None):
        dcc = dcc or self._dcc
        combo = self._bl_conn_combo if dcc == "blender" else getattr(self, "_conn_combo", None)
        if combo is None:
            return
        port = combo.itemData(idx)
        if port is None:
            return
        if dcc == "blender":
            self._bl_bridge.set_port(int(port))
            self._sm.set("blender_command_port", int(port), save=False)
        else:
            self._bridge.set_port(int(port))
            self._sm.set("maya_command_port", int(port), save=False)
        self.statusBar().showMessage(
            tr("%s 接続先を切替: %s", "%s connection switched: %s")
            % (self._dcc_label(dcc), combo.itemText(idx)))

    def _focus_connected_dcc(self):
        if self._dcc == "blender":
            pid = self._bl_conn_pids.get(self._bl_bridge.port)
            self._focus_pid_window(pid, "Blender", self._refresh_blender_connections)
        else:
            self._focus_connected_maya()

    def _install_freeze_watchdog(self):
        """常時有効: UIスレッドが1.5秒以上止まったら、その瞬間のメインスレッドの
        スタックを記録する（フリーズ原因の特定用）。

        出力先は **ユーザープロファイル直下**（~/.maya_file_manager/logs）。
        r117: 以前はツールフォルダ直下に書いていたため、**ツールを置いた
        ドライブが無応答になると記録そのものがブロック**され、肝心な時に
        何も残らなかった（2026-10-01、D: 無応答時に実際に発生）。"""
        import threading
        import time as _time
        import sys as _sys
        import traceback as _tb
        from ui.browser_panel import _mfm_log, _MFM_FREEZE_LOG
        freeze_log = _MFM_FREEZE_LOG

        def _dump(text):
            try:
                with open(freeze_log, "a", encoding="utf-8") as f:
                    import datetime
                    f.write("[%s] %s\n"
                            % (datetime.datetime.now().strftime("%H:%M:%S"),
                               text))
            except OSError:
                pass
            _mfm_log(text)

        beat = [_time.monotonic()]

        # ── ネイティブ方式（GIL不要）: faulthandler の遅延ダンプを UI の
        # ハートビート毎に再アームする。UIスレッドが1.5秒止まるとCレベルの
        # 監視スレッドが «フリーズ最中の» 全スレッドのPythonスタックを書く。
        # （Pythonスレッドのサンプラは PySide の C++呼び出し中はGILを取れず、
        #  フリーズ終了後の位置しか記録できない＝真犯人の直前行しか見えない）
        try:
            import faulthandler
            self._fh_file = open(freeze_log, "a", encoding="utf-8")
            self._fh_file.write("\n[%s] === faulthandler 監視開始 ===\n"
                                % __import__("datetime").datetime.now()
                                .strftime("%H:%M:%S"))
            self._fh_file.flush()
            # クラッシュ（アクセス違反等）時にも Python スタックを同じログへ
            # 書く（r64。ネイティブダイアログでのプロセス消滅の切り分け用）
            try:
                faulthandler.enable(file=self._fh_file, all_threads=True)
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:1077 _dump")

            def _rearm():
                try:
                    faulthandler.dump_traceback_later(
                        1.5, repeat=False, file=self._fh_file, exit=False)
                except Exception as _e:
                    _swallow(_e, "ui/main_window.py:1084 _rearm")
            _rearm()
        except Exception:
            _rearm = None

        def _heartbeat():
            beat[0] = _time.monotonic()
            if _rearm is not None:
                _rearm()

        t = QTimer(self)
        t.setInterval(200)
        t.timeout.connect(_heartbeat)
        t.start()
        self._wd_timer = t
        main_id = threading.get_ident()

        def _watch():
            last_dump = 0.0
            while True:
                _time.sleep(0.5)
                now = _time.monotonic()
                stalled = now - beat[0]
                if stalled > 1.5 and now - last_dump > 5.0:
                    # r108: ドラッグ等 «意図して UI スレッドを握っている» 区間は
                    # 記録しない（本物のフリーズが埋もれるため）
                    try:
                        from ui.browser_panel import mfm_blocking_reason
                        if mfm_blocking_reason():
                            continue
                    except Exception as _e:
                        _swallow(_e, "ui/main_window.py:1115 _watch")
                    last_dump = now
                    try:
                        frm = _sys._current_frames().get(main_id)
                        if frm is not None:
                            _dump(
                                "=== UIフリーズ検出 (%.1f秒停止) メインスレッド ===\n%s"
                                % (stalled, "".join(_tb.format_stack(frm))))
                    except Exception as _e:
                        _swallow(_e, "ui/main_window.py:1124 _watch")

        threading.Thread(target=_watch, daemon=True,
                         name="mfm-freeze-watchdog").start()

    BRIDGE_PROMPT_KEY = "maya_bridge_prompt"      # "" / "later" / "never" / "done"
    # 場所の食い違いを聞くのは 1 セッションに 1 回だけ（クラス属性＝既定値）
    _bridge_mismatch_asked = False

    def _maybe_offer_maya_bridge(self):
        """未導入なら «こちらから» 連携の導入を持ちかける（r119）。

        従来はツールメニューの奥にあり、存在に気付けなかった。
        「入れたつもりがない」まま «起動済みの Maya が出ない» に悩む経路を
        断つのが目的。«今後聞かない» を選べば二度と出さない。"""
        try:
            from core.maya_bridge import is_usersetup_installed
            if is_usersetup_installed():
                return
            if str(self._sm.get(self.BRIDGE_PROMPT_KEY, "") or "") in (
                    "never", "done"):
                return
            box = QMessageBox(self)
            box.setWindowTitle(tr("Maya連携のセットアップ", "Set Up Maya Bridge"))
            box.setText(tr(
                "Maya 連携がまだ設定されていません。\n\n"
                "設定すると、マネージャー以外から起動した Maya も\n"
                "「接続:」リストに出て、ファイルを送れるようになります。\n\n"
                "今すぐ設定しますか？",
                "The Maya bridge is not set up yet.\n\n"
                "Once it is, Mayas launched outside this manager also appear\n"
                "in the Connect list so you can send files to them.\n\n"
                "Set it up now?"))
            yes = box.addButton(tr("設定する", "Set up"), QMessageBox.AcceptRole)
            later = box.addButton(tr("あとで", "Later"), QMessageBox.RejectRole)
            box.addButton(tr("今後聞かない", "Don't ask again"),
                          QMessageBox.DestructiveRole)
            box.exec_() if hasattr(box, "exec_") else box.exec()
            clicked = box.clickedButton()
            if clicked is yes:
                self._sm.set(self.BRIDGE_PROMPT_KEY, "later")
                self._install_maya_bridge()
            elif clicked is later:
                self._sm.set(self.BRIDGE_PROMPT_KEY, "later")
            else:
                self._sm.set(self.BRIDGE_PROMPT_KEY, "never")
        except Exception as _e:
            _swallow(_e, "ui/main_window_dcc.py _maybe_offer_maya_bridge")

    def _install_maya_bridge(self):
        """連携スニペットの書き込み（r119: 場所と対象を選ばせる）。

        セットアップ時に Maya が起動していないことの方が多いので、
        «Maya に聞く» に頼らず «実在するバージョンフォルダ» で決める。
        後で Maya が繋がった時の食い違いは
        _check_bridge_location_against_maya() が拾う。"""
        from ui.maya_bridge_dialog import MayaBridgeDialog
        from core.maya_bridge import install_usersetup
        dlg = MayaBridgeDialog(self)
        # モーダルのまま（例外）: 選んだ «書き込み先» を受け取って
        # その場で userSetup.py を書く。一度きりのセットアップ操作。
        ret = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
        if not ret:
            return
        targets = dlg.selected_targets()
        if not targets:
            QMessageBox.information(
                self, tr("Maya連携", "Maya Bridge"),
                tr("書き込み先が選ばれていません。",
                   "No install target was selected."))
            return
        written, failed = [], []
        for ver in targets:
            try:
                written.append(install_usersetup(ver))
            except Exception as e:
                failed.append("%s: %s" % (ver or tr("共通", "all"), e))
        if written:
            self._sm.set(self.BRIDGE_PROMPT_KEY, "done")
        if failed:
            QMessageBox.critical(
                self, tr("インストール失敗", "Install Failed"),
                "\n".join(failed))
            return
        QMessageBox.information(
            self, tr("完了", "Done"),
            tr("インストールしました:\n%s\n\n"
               "次回以降に起動した Maya が自動で接続可能になります。\n"
               "今 開いている Maya は「起動中の Maya を今すぐ接続...」で繋げます。",
               "Installed:\n%s\n\nMayas launched from now on will be "
               "connectable automatically.\nFor a Maya that is already "
               "running, use \u201cConnect a running Maya now...\u201d.")
            % "\n".join(written))

    def _check_bridge_location_against_maya(self, reported_dir: str):
        """Maya が繋がった時、«連携を入れた場所» と Maya の設定フォルダが
        食い違っていないか確かめる（r119）。

        セットアップは Maya 未起動で行われることが多く、その時点では
        場所を確定できない。食い違っていれば «効かない連携» が残るので、
        繋がった今こそ気付かせる。聞くのは 1 回だけ。"""
        from core.maya_bridge import (installed_targets, maya_app_dir,
                                      set_maya_app_dir, is_usersetup_installed)
        try:
            if not reported_dir or self._bridge_mismatch_asked:
                return
            before = maya_app_dir()
            if os.path.normcase(os.path.normpath(before)) == \
                    os.path.normcase(os.path.normpath(reported_dir)):
                return
            had = bool(installed_targets())      # 前の場所に入っていたか
            set_maya_app_dir(reported_dir, confirmed=True)
            if is_usersetup_installed(None) or installed_targets():
                return                           # 新しい場所にも既に入っている
            self._bridge_mismatch_asked = True
            if not had:
                return                           # そもそも未導入なら初回案内に任せる
            ret = QMessageBox.question(
                self, tr("Maya連携の場所", "Maya Bridge Location"),
                tr("接続した Maya は別の設定フォルダを使っています。\n\n"
                   "　連携を入れた場所: %s\n"
                   "　この Maya の場所: %s\n\n"
                   "このままでは連携が効きません。こちらにも入れますか？",
                   "The Maya that just connected uses a different user folder.\n\n"
                   "　Bridge installed in: %s\n"
                   "　This Maya uses:      %s\n\n"
                   "The bridge will not work as is. Install it there too?")
                % (before, reported_dir),
                QMessageBox.Yes | QMessageBox.No)
            if ret == QMessageBox.Yes:
                self._install_maya_bridge()
        except Exception as _e:
            _swallow(_e, "ui/main_window_dcc.py _check_bridge_location_against_maya")

    def _connect_running_maya(self):
        """«既に起動している» Maya を、再起動せずに接続リストへ出す（r119）。

        userSetup.py のスニペットは «次回の Maya 起動から» しか効かない。
        作業中の Maya に後からポートを開かせる手段は commandPort だけで、
        その commandPort がまだ無いので外からは一切触れない。
        → スクリプトエディタに貼る 1 本を渡すのが唯一の道。"""
        from core.maya_bridge import open_port_snippet
        code = open_port_snippet()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("起動中の Maya を今すぐ接続",
                              "Connect a running Maya now"))
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(tr(
            "作業中の Maya は、再起動しないと連携ポートが開きません。\n"
            "下のコードを Maya のスクリプトエディタ（Python タブ）へ貼って\n"
            "実行すると、その場で接続できるようになります。\n"
            "（シーンには一切影響しません）",
            "A Maya that is already running has no bridge port until it is\n"
            "restarted. Paste the code below into Maya's Script Editor\n"
            "(Python tab) and run it to connect right away.\n"
            "(It does not touch your scene.)")))
        box = QTextEdit(dlg)
        box.setPlainText(code)
        box.setReadOnly(True)
        box.setLineWrapMode(QTextEdit.NoWrap)
        box.setMinimumSize(560, 220)
        lay.addWidget(box)
        row = QHBoxLayout()
        row.addStretch()
        copy_btn = QPushButton(tr("📋 コピー", "📋 Copy"), dlg)
        copy_btn.setAutoDefault(False)
        rescan_btn = QPushButton(tr("🔄 貼り付けた — 再スキャン",
                                    "🔄 Pasted — rescan"), dlg)
        rescan_btn.setAutoDefault(False)
        close_btn = QPushButton(tr("閉じる", "Close"), dlg)
        close_btn.setAutoDefault(False)
        row.addWidget(copy_btn)
        row.addWidget(rescan_btn)
        row.addWidget(close_btn)
        lay.addLayout(row)

        def _copy():
            cb = QApplication.clipboard()
            if cb is not None:
                cb.setText(code)
            self.statusBar().showMessage(
                tr("コードをコピーしました。Maya のスクリプトエディタで実行してください。",
                   "Copied. Run it in Maya's Script Editor."), 8000)
        copy_btn.clicked.connect(_copy)
        rescan_btn.clicked.connect(self._refresh_maya_connections)
        close_btn.clicked.connect(dlg.accept)
        _copy()                      # 開いた時点でクリップボードへ入れておく
        # r119: 非モーダル。Maya へ貼りに行って戻ってくる窓なので、
        # 開いている間マネージャーが固まるのは具合が悪い。
        from ui.dialog_util import show_tool_window
        show_tool_window(self, "_connect_running_dlg", lambda: dlg)

    def _focus_connected_maya(self):
        """接続中のMayaのウィンドウを最前面に出す（Windows専用）。
        PIDは接続スキャン時の識別応答から取得済み。"""
        pid = self._conn_pids.get(self._bridge.port)
        self._focus_pid_window(pid, "Maya", self._refresh_maya_connections)

    def _focus_pid_window(self, pid, app_name: str, rescan):
        if not pid:
            self.statusBar().showMessage(
                tr("%sのPIDが未取得です。⟳で再スキャンしてください。",
                   "%s PID unknown. Rescan with ⟳ first.") % app_name)
            rescan()
            return
        if os.name != "nt":
            return
        try:
            import ctypes
            import ctypes.wintypes as wt
            u32 = ctypes.windll.user32
            found = []

            @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
            def _enum(hwnd, _lp):
                wpid = wt.DWORD(0)
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                # 可視のトップレベル（オーナー無し）＝メインウィンドウ候補
                if (wpid.value == pid and u32.IsWindowVisible(hwnd)
                        and not u32.GetWindow(hwnd, 4)):   # GW_OWNER=4
                    found.append(hwnd)
                return True

            u32.EnumWindows(_enum, 0)
            if not found:
                self.statusBar().showMessage(
                    tr("%sのウィンドウが見つかりません（PID %d）",
                       "%s window not found (PID %d)") % (app_name, pid))
                return
            hwnd = found[0]
            if u32.IsIconic(hwnd):
                u32.ShowWindow(hwnd, 9)    # SW_RESTORE
            u32.SetForegroundWindow(hwnd)
        except Exception as e:
            self.statusBar().showMessage(
                tr("最前面化に失敗: %s", "Failed to bring to front: %s") % e)

    def _bridge_send_async(self, code: str, label: str, timeout: float = 6.0,
                           bridge=None):
        """送信〜応答待ちをワーカースレッドで行う（UIを絶対に止めない）。
        Mayaがビジーだと commandPort の応答は数秒〜返ってこないことがあり、
        UIスレッドで recv を待つとクリックのたびにフリーズする。"""
        import threading
        bridge = bridge or self._bridge

        def _run():
            ok, reply = bridge.send_python(code, timeout=timeout)
            self._bridge_notify.done.emit(ok, label, reply)

        threading.Thread(target=_run, daemon=True,
                         name="mfm-dcc-send").start()

    def _on_bridge_done(self, ok: bool, label: str, reply):
        if ok:
            msg = tr("送信: %s", "Sent: %s") % label
            if reply is None:
                # r78: 送れたが応答が無い＝DCC 側がビジー（シーン読込中・レンダー中・
                # ダイアログ表示中）。黙っていると「何も反応しない」に見える。
                msg += tr("  — 応答なし（DCC 側が処理中かダイアログ待ちです。DCC の画面を確認してください）",
                          "  — no reply (the DCC is busy or waiting on a dialog; check its window)")
            elif reply and reply not in ("None", "0", "1"):
                msg += f"  →  {str(reply)[:120]}"
            self.statusBar().showMessage(msg, 15000 if reply is None else 0)
            if reply and str(reply).startswith("Error:"):
                QMessageBox.warning(self, tr("連携エラー", "Bridge Error"),
                                    "%s\n\n%s" % (label, str(reply)[:1500]))
        else:
            QMessageBox.critical(
                self, tr("連携エラー", "Bridge Error"), str(reply))

    # ---- Blender 連携（r65） -------------------------------------------------

    def _blender_send_or_prompt(self, code: str, label: str, log=None) -> bool:
        """log=(操作名, パス) を渡すと Blender 側の Info エディタにログを残す（r88）。"""
        if log:
            from core.dcc_log import wrap_blender
            code = wrap_blender(code, log[0], log[1])
        if self._bl_bridge.is_connected(timeout=0.3):
            self.statusBar().showMessage(
                tr("Blenderへ送信中: %s …", "Sending to Blender: %s …") % label)
            self._bridge_send_async(code, "Blender: " + label, timeout=8.0,
                                    bridge=self._bl_bridge)
            return True
        ver = self._blender_inst.version if self._blender_inst else ""
        ret = QMessageBox.question(
            self, tr("Blender未接続", "Blender Not Connected"),
            tr("連携できるBlenderが見つかりません。\n"
               "連携できるのは、このマネージャーの「起動」から起動したBlender、\n"
               "またはツールメニューで連携をインストール済みのBlenderです。\n\n"
               "Blender %s を今すぐ起動しますか？",
               "No connectable Blender found.\nOnly Blender launched from this "
               "manager, or with the bridge installed (Tools menu), can be controlled."
               "\n\nLaunch Blender %s now?") % ver,
            QMessageBox.Yes | QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._launch_blender()
        return False

    def _blender_open(self, path: str):
        self._blender_send_or_prompt(_bl.code_open(path),
                                     tr("開く %s", "Open %s") % Path(path).name,
                                     log=(tr("開く", "Open"), path))

    def _blender_import(self, path: str):
        self._blender_send_or_prompt(_bl.code_import(path),
                                     tr("インポート %s", "Import %s") % Path(path).name,
                                     log=(tr("インポート", "Import"), path))

    def _blender_link(self, path: str):
        self._blender_send_or_prompt(_bl.code_link(path),
                                     tr("リンク %s", "Link %s") % Path(path).name,
                                     log=(tr("リンク", "Link"), path))

    def _detect_blender(self):
        """設定 blender_exe（ユーザー指定）＋自動検出（標準配置・他ドライブ・
        レジストリ・Store エイリアス）。結果は mfm_startup.log にも残す（r71）。"""
        from ui.browser_panel import _mfm_timeline
        extra = [self._sm.get("blender_exe", "") or ""]
        try:
            installs = find_installed_blender_versions(extra)
        except Exception as e:
            installs = []
            _mfm_timeline("blender detect error: %r" % (e,))
        try:
            from core.blender_version import detection_report
            _mfm_timeline("blender detect: " + detection_report(extra, installs).replace("\n", " | "))
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:1302 _detect_blender")
        return installs

    def _locate_blender(self):
        """ツールメニュー「Blender の場所を指定...」: 自動検出で出ない Blender
        （標準外の場所／ポータブル版）の blender.exe を設定 blender_exe に記憶し、
        バージョン一覧とバッジのアイコンを更新する。"""
        start = ""
        if self._blender_inst:
            start = str(self._blender_inst.executable.parent)
        path, _ = QFileDialog.getOpenFileName(
            self, tr("blender.exe を選択", "Select blender.exe"), start,
            "Blender (blender.exe blender);;All (*)", options=QFileDialog.DontUseNativeDialog)
        if not path:
            return
        self._sm.set("blender_exe", path, save=True)
        self._blender_installs = self._detect_blender()
        self._populate_version_combos()
        # 指定したものを選択
        for i in range(self._blender_combo.count()):
            d = self._blender_combo.itemData(i)
            if isinstance(d, BlenderInstallation) and \
                    str(d.executable).lower() == os.path.normpath(path).lower():
                self._blender_combo.setCurrentIndex(i)
                self._blender_inst = d
                break
        try:
            m_exe = self._maya_inst.executable if self._maya_inst else None
            b_exe = self._blender_inst.executable if self._blender_inst else None
            self._dcc_hdr.set_app_icons(m_exe, b_exe)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:1333 _locate_blender")
        self.statusBar().showMessage(tr("Blender: %s", "Blender: %s") % path)

    def _install_blender_bridge(self):
        vers = [i.version for i in getattr(self, "_blender_installs", []) if i.version != "?"]
        dirs = _bl.startup_dirs(vers)
        if not dirs:
            QMessageBox.warning(self, "Blender",
                                tr("Blender の設定フォルダが見つかりません。",
                                   "Blender config folder not found."))
            return
        msg = tr(
            "全ての Blender が起動時に連携ポートを自動で開くように、\n"
            "以下へ mfm_bridge.py を配置します:\n%s\n\n"
            "これにより、マネージャー以外から起動した Blender も\n"
            "「接続:」リストに表示されます（次回の Blender 起動から有効）。\n\n実行しますか？",
            "Places mfm_bridge.py in the folders below so every Blender opens a\n"
            "bridge port at startup:\n%s\n\nBlenders launched outside this manager "
            "will then appear in the Connect list (from the next launch).\n\nProceed?"
        ) % "\n".join(str(d) for d in dirs)
        if QMessageBox.question(self, tr("Blender連携のインストール", "Install Blender Bridge"),
                                msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            written = _bl.install_startup(vers)
            QMessageBox.information(self, tr("完了", "Done"),
                                    tr("インストールしました:\n%s", "Installed:\n%s")
                                    % "\n".join(written))
        except Exception as e:
            QMessageBox.critical(self, tr("インストール失敗", "Install Failed"), str(e))

    # ---- 形式に応じた送り先の振り分け（r65） ----------------------------------
    # .blend → Blender / .ma .mb → Maya / 共通形式 → 選択中の DCC。
    # app を明示された時（D&D の落下先ウィンドウ）はそれを優先する。

    def _can_take_over_drop(self, paths, app: str):
        """DCC のウィンドウへの落下を «Manager がブリッジ経由で» 処理するか（r90）。

        True の時、DCC には空のファイル一覧が渡り、DCC 自身のドロップ処理は
        走らない。Windows の D&D は落とし先の処理が終わるまでドラッグ元を
        止めるため、DCC が落としたスクリプトを同期実行してダイアログを出すと
        Manager が固まる（install.py で 250 秒以上停止した実例）。
        ブリッジに繋がっていない／扱えない形式なら False（従来どおり DCC に任せる）。
        ※ Windows の COM 呼び出しの中で呼ばれるので、重い処理は禁止。"""
        # r91: 戻り値は «Manager が引き受けるファイルの一覧»（空＝引き受けない）。
        # 各ファイルは «実行するコマンドの対象か» で判定する（.py に «開く» を
        # 送らない）。引き受けないファイルは DCC に実ファイルとして渡る。
        from core import dcc_caps
        if self._inside_maya or not paths:
            return []
        bridge = self._bl_bridge if app == "blender" else self._bridge
        try:
            if not bridge.is_connected(timeout=0.2):
                return []
        except Exception:
            return []
        action = self._sm.get("dnd_action", "none")
        taken = []
        for p in paths:
            if app == "maya" and dcc_caps.supports("maya", "run_script", p):
                taken.append(p)               # スクリプトは Maya と同じく実行
            elif action != "none" and dcc_caps.supports(app, action, p):
                taken.append(p)
        return taken

    def _maya_run_script(self, path: str):
        """Maya へ落とした .py / .mel を «Maya と同じ作法で» 実行する（r90）。
        .py は Maya 標準の executeDroppedPythonFile（onMayaDroppedPythonFile を
        呼ぶ）を使い、無い版では同等の処理をする。.mel は source。
        インストーラがダイアログを出しても Manager は待たない（非同期送信）。
        ダイアログが裏に隠れないよう、送信後に Maya を前面に出す。"""
        if not self._dcc_accepts("run_script", path, "maya"):
            return
        if not self._dcc_once("run_script", path, "maya"):
            return
        p = escape_path(path)
        if os.path.splitext(path)[1].lower() == ".mel":
            inner = ("import maya.mel as _mel\n"
                     "_mel.eval('source \"%s\"')\n" % p)
        else:
            inner = (
                "import os, sys, runpy\n"
                "_p = '%s'\n"
                "_d = os.path.dirname(_p)\n"
                "try:\n"
                "    import maya.app.general.executeDroppedPythonFile as _edp\n"
                "    _edp.executeDroppedPythonFile(_p, '')\n"
                "except ImportError:\n"
                "    if _d not in sys.path:\n"
                "        sys.path.insert(0, _d)\n"
                "    _g = runpy.run_path(_p, run_name='__mfm_dropped__')\n"
                "    _f = _g.get('onMayaDroppedPythonFile')\n"
                "    if callable(_f):\n"
                "        _f('')\n" % p)
        inner = ("try:\n" + "".join("    " + l + "\n" for l in inner.splitlines())
                 + "except Exception as _e:\n"
                 "    _mfm_result = 'Failed: ' + str(_e)\n")
        code = "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result'))[1])({})" % inner
        if self._maya_send_or_prompt(
                code, tr("スクリプト実行 %s", "Run script %s") % Path(path).name,
                log=(tr("スクリプト実行", "Run script"), path)):
            QTimer.singleShot(400, self._focus_connected_maya)

    def _dcc_once(self, kind: str, path: str, app: str) -> bool:
        """«1操作＝1回» を保証するガード（r86）。

        同じ (種別, パス, 送り先) が 1.2 秒以内に再度来たら False を返して捨てる。
        Maya へのリファレンスが同一ファイルで2回実行される報告（2026-09-18）への
        対策。クリック動作・右クリック・D&D のどの経路から来ても、DCC へ送る
        直前のここを必ず通るので、発火元がどれでも止まる。
        捨てた時はログに残すので、mfm ログを見れば «どの経路が二重に呼んでいるか»
        を後から特定できる。"""
        import time as _time_mod
        from ui.browser_panel import _mfm_log
        try:
            key = (kind, os.path.normcase(os.path.abspath(path)), app or "")
        except Exception:
            key = (kind, path, app or "")
        now = _time_mod.monotonic()
        last_key, last_t = getattr(self, "_last_dcc_send", (None, 0.0))
        if key == last_key and (now - last_t) < 1.2:
            _mfm_log("dcc-send: 二重発火を抑止 kind=%s app=%s dt=%.3f path=%r"
                     % (kind, app, now - last_t, path))
            self.statusBar().showMessage(
                tr("連続した同一操作を1回にまとめました: %s",
                   "Merged a repeated action into one: %s") % Path(path).name, 4000)
            return False
        self._last_dcc_send = (key, now)
        return True

    def _dcc_accepts(self, command: str, path: str, app: str) -> bool:
        """«そのコマンドの対象か» の関所（r91、ユーザー指示: 対象外には絶対に
        反応しない）。対象外ならステータスに理由を出して False。"""
        from core import dcc_caps
        from ui.browser_panel import _mfm_log
        if dcc_caps.supports(app, command, path):
            return True
        name = "Blender" if app == "blender" else "Maya"
        msg = tr("スキップ: %s は %s の「%s」の対象外です",
                 "Skipped: %s is not a target of %s \"%s\"") % (
            Path(path).name, name, dcc_caps.command_label(command))
        _mfm_log("dcc-gate: " + msg)
        try:
            self.statusBar().showMessage(msg, 6000)
        except Exception as _e:
            _swallow(_e, "ui/main_window.py:1478 _dcc_accepts")
        return False

    def _dcc_open(self, path: str, app: str = None):
        target = app or dcc_for_path(path, self._dcc)
        if not self._dcc_accepts("open", path, target):
            return
        if not self._dcc_once("open", path, target):
            return
        if target == "blender":
            self._blender_open(path)
        else:
            self._maya_open(path)

    def _dcc_import(self, path: str, app: str = None):
        target = app or dcc_for_path(path, self._dcc)
        if not self._dcc_accepts("import", path, target):
            return
        if not self._dcc_once("import", path, target):
            return
        if target == "blender":
            self._blender_import(path)
        else:
            self._maya_import(path)

    def _dcc_reference(self, path: str, app: str = None, ask_ns: bool = True):
        target = app or dcc_for_path(path, self._dcc)
        if not self._dcc_accepts("reference", path, target):
            return
        if not self._dcc_once("reference", path, target):
            return
        if target == "blender":
            self._blender_link(path)
        else:
            self._maya_reference(path, ask_ns=ask_ns)

    def _maya_send_or_prompt(self, code: str, label: str, log=None) -> bool:
        """起動済みMaya（commandPort）へPythonコードを送る。未接続なら
        Mayaの起動を提案する。送信を開始できたら True。
        log=(操作名, パス) を渡すと Maya のスクリプトエディタにログを残す（r88）。"""
        if log:
            from core.dcc_log import wrap_maya
            code = wrap_maya(code, log[0], log[1])
        if self._bridge.is_connected(timeout=0.3):
            self.statusBar().showMessage(
                tr("Mayaへ送信中: %s …", "Sending to Maya: %s …") % label)
            self._bridge_send_async(code, label)
            return True
        ver = self._maya_inst.version if self._maya_inst else ""
        _port_hint = (f'  cmds.commandPort(name=":{self._bridge.port}", '
                      'sourceType="python")')
        ret = QMessageBox.question(
            self, tr("Maya未接続", "Maya Not Connected"),
            tr("連携できるMayaが見つかりません。\n"
               "連携できるのは、このマネージャーの「起動」ボタンから起動したMayaです。\n"
               "（手動起動のMayaと繋ぐ場合は、Mayaのスクリプトエディタで\n"
               "%s\nを実行してください）\n\n"
               "Maya %s を今すぐ起動しますか？",
               "No connectable Maya found.\n"
               "Only Maya launched from this manager's Launch button can be "
               "controlled.\n(To connect a manually launched Maya, run\n"
               "%s\nin Maya's Script Editor)\n\n"
               "Launch Maya %s now?") % (_port_hint, ver),
            QMessageBox.Yes | QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._launch_maya()
            self.statusBar().showMessage(
                tr("Mayaを起動中です。起動完了後にもう一度実行してください。",
                   "Launching Maya. Please retry after it finishes starting."))
        return False

    def _maya_local_setproject(self, path: str):
        """Maya 内起動時: workspace.mel があれば «プロジェクトをセットするか» を
        確認する（リモート時と同じコードをプロセス内で実行。r89）。"""
        from core.maya_project import setproject_code
        code = setproject_code(path)
        if code:
            try:
                exec(code, {})
            except Exception as _e:
                _swallow(_e, "ui/main_window.py:1558 _maya_local_setproject")

    def _maya_local_log(self, action: str, path: str, what: str, err: str = ""):
        """Maya 内起動時のログ（スクリプトエディタ）。what: start/done/fail/cancel。"""
        from core.dcc_log import maya_local, maya_local_messages
        m = maya_local_messages(action, path)
        if what == "fail":
            maya_local(m["fail"] + err, "error")
        elif what == "cancel":
            maya_local(m["cancel"], "warning")
        else:
            maya_local(m[what], "info")

    def _maya_open(self, path: str):
        if not self._inside_maya:
            p = escape_path(path)
            # 未保存確認は «Maya側で» confirmDialog を出す。
            # マネージャーからのリモート照会（recv待ち）はMayaビジー時に
            # UIをフリーズさせるため行わない。
            _msg = tr("現在のシーンに未保存の変更があります。\\n破棄して開きますか？",
                      "The current scene has unsaved changes.\\nDiscard and open?")
            from core.maya_project import setproject_code
            inner = (
                "import maya.cmds as cmds\n"
                "_r = 'open'\n"
                "if cmds.file(q=True, modified=True):\n"
                "    _r = cmds.confirmDialog(title='Maya File Manager',"
                " message=u'%s',"
                " button=['open', 'cancel'], defaultButton='cancel',"
                " cancelButton='cancel', dismissString='cancel')\n"
                "if _r == 'open':\n"
                % _msg
                # r89: workspace.mel があれば «プロジェクトをセットするか» を確認
                + setproject_code(path, indent="    ")
                + "    cmds.file('%s', open=True, force=True, ignoreVersion=True)\n"
                "else:\n"
                "    _mfm_result = 'Cancelled'\n"
                % p
            )
            code = "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result'))[1])({})" % inner
            self._maya_send_or_prompt(
                code, tr("開く %s", "Open %s") % Path(path).name,
                log=(tr("開く", "Open"), path))
            return
        act = tr("開く", "Open")
        self._maya_local_log(act, path, "start")
        try:
            import maya.cmds as cmds
            if cmds.file(query=True, modified=True):
                ret = QMessageBox.question(
                    self, "未保存の変更",
                    "現在のシーンを保存しますか？",
                    QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
                )
                if ret == QMessageBox.Cancel:
                    self._maya_local_log(act, path, "cancel")
                    return
                if ret == QMessageBox.Save:
                    cmds.file(save=True)
            self._maya_local_setproject(path)
            cmds.file(path, open=True, force=True, ignoreVersion=True)
            self._maya_local_log(act, path, "done")
        except Exception as e:
            self._maya_local_log(act, path, "fail", str(e))
            QMessageBox.critical(self, tr("エラー", "Error"), str(e))

    def _maya_import(self, path: str):
        if not self._inside_maya:
            p = escape_path(path)
            # r91: 形式ごとの取り込みプラグインを必ず先にロードする
            #（obj/abc/usd はプラグイン未ロードだと «未対応形式» で失敗する）
            from core.dcc_caps import MAYA_PLUGIN_FOR_EXT
            plug = MAYA_PLUGIN_FOR_EXT.get(Path(path).suffix.lower())
            body = ("    cmds.loadPlugin('%s', quiet=True)\n" % plug) if plug else ""
            if Path(path).suffix.lower() == ".fbx":
                body += "    cmds.file('%s', i=True, type='FBX')\n" % p
            else:
                body += ("    cmds.file('%s', i=True, ignoreVersion=True,"
                         " mergeNamespacesOnClash=False)\n" % p)
            # エラーはMaya側のダイアログで表示（リファレンスと同方針）
            from core.maya_project import setproject_code
            inner = (
                "import maya.cmds as cmds\n"
                + setproject_code(path) +      # r89: プロジェクトのセット確認
                "try:\n" + body +
                "except Exception as _e:\n"
                "    cmds.confirmDialog(title='Maya File Manager',"
                " message=u'%s\\n' + str(_e), button=['OK'])\n"
                "    _mfm_result = 'Failed: ' + str(_e)\n"
                % tr("インポートに失敗しました:", "Failed to import:")
            )
            code = "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result'))[1])({})" % inner
            self._maya_send_or_prompt(
                code, tr("インポート %s", "Import %s") % Path(path).name,
                log=(tr("インポート", "Import"), path))
            return
        act = tr("インポート", "Import")
        self._maya_local_log(act, path, "start")
        try:
            import maya.cmds as cmds
            self._maya_local_setproject(path)
            ext = Path(path).suffix.lower()
            from core.dcc_caps import MAYA_PLUGIN_FOR_EXT
            if MAYA_PLUGIN_FOR_EXT.get(ext):
                try:
                    cmds.loadPlugin(MAYA_PLUGIN_FOR_EXT[ext], quiet=True)
                except Exception as _e:
                    _swallow(_e, "ui/main_window.py:1665 _maya_import")
            if ext == ".fbx":
                from core.file_operations import fbx_import_maya
                fbx_import_maya(path)
            else:
                cmds.file(path, i=True, ignoreVersion=True,
                          mergeNamespacesOnClash=False)
            self._maya_local_log(act, path, "done")
        except Exception as e:
            self._maya_local_log(act, path, "fail", str(e))
            QMessageBox.critical(self, tr("インポートエラー", "Import Error"), str(e))

    def _on_maya_drop(self, action: str, paths, app: str = None):
        """ブラウザから Maya/Blender のウィンドウへD&Dされた時のアクション実行。
        app は落下先のプロセス（"maya"/"blender"）。右クリックの一括経路からは
        None（形式と選択中 DCC で決める）。リファレンスはダイアログを出さず
        デフォルトNamespaceを使う（複数ファイルのD&Dで連続ダイアログにならないように）。"""
        from ui.browser_panel import _mfm_log
        # r86: 同一パスの重複を除去（選択やD&Dで同じパスが2つ入る経路がある）。
        # 「1操作＝1回」の最終的な保証は _dcc_once（DCC 送信の直前）で行う。
        seen = set()
        uniq = []
        for p in (paths or []):
            if not p:
                continue
            k = os.path.normcase(os.path.abspath(p))
            if k in seen:
                _mfm_log("dcc-dispatch: 重複パスを除去 action=%s path=%r" % (action, p))
                continue
            seen.add(k)
            uniq.append(p)
        paths = uniq
        if not paths:
            return
        if action in ("save_scene", "export_selection"):          # r70
            self._dcc_save_dialog(app or self._dcc,
                                  "export" if action == "export_selection" else "save",
                                  paths[0])
            return
        if action == "run_script":                                  # r90
            for p in paths:
                self._maya_run_script(p)
            return
        if action == "open":
            from core import dcc_caps
            tgt = [p for p in paths if dcc_caps.supports(
                app or dcc_for_path(p, self._dcc), "open", p)]
            self._dcc_open((tgt or paths)[0], app)
        elif action == "import":
            for p in paths:
                self._dcc_import(p, app)
        elif action == "reference":
            for p in paths:
                self._dcc_reference(p, app, ask_ns=False)
        elif action == "reference_ask":      # 右クリック単一: Namespace を確認
            self._dcc_reference(paths[0], app, ask_ns=True)

    # ---- DCC からの保存／書き出し（r70） ------------------------------------

    def _connected_scene_name(self, dcc: str) -> str:
        """接続コンボのラベル «Maya 2026 — scene.ma (:20261)» からシーン名を取る
        （スキャン時点のスナップショット。リモート照会はしない）。"""
        combo = self._bl_conn_combo if dcc == "blender" else getattr(self, "_conn_combo", None)
        if combo is None or combo.itemData(combo.currentIndex()) is None:
            return ""
        label = combo.currentText()
        if " — " not in label:
            return ""
        scene = label.split(" — ", 1)[1].rsplit(" (:", 1)[0].strip()
        return "" if scene in (tr("無題", "untitled"), "untitled") else scene

    def _dcc_save_dialog(self, dcc: str, mode: str, target: str):
        """右クリック「シーンを保存」「選択を書き出し」。target はフォルダ
        （空白右クリック）または単一ファイル（名前欄の初期値）。"""
        from ui.save_dialog import SaveDialog
        dcc = "blender" if dcc == "blender" else "maya"
        if os.path.isdir(target):
            folder, initial = target, ""
        else:
            folder, initial = os.path.dirname(target), os.path.basename(target)
        if not initial and mode == "save":
            initial = self._connected_scene_name(dcc)
        dlg = SaveDialog(dcc, mode, folder, initial, self._sm, parent=self.window())
        # モーダルのまま（例外）: 決まった保存先を使って直後に DCC へ
        # 保存/書き出しを送る。返り値を待たないと処理が組めない。
        try:
            ret = dlg.exec_()
        except AttributeError:
            ret = dlg.exec()
        if ret != QDialog.Accepted:
            return
        path, code = dlg.result_path(), dlg.dcc_code()
        label = (tr("選択を書き出し %s", "Export Selection %s") if mode == "export"
                 else tr("シーンを保存 %s", "Save Scene %s")) % Path(path).name
        act = (tr("選択を書き出し", "Export Selection") if mode == "export"
               else tr("シーンを保存", "Save Scene"))
        if dcc == "blender":
            self._blender_send_or_prompt(code, label, log=(act, path))
        elif self._inside_maya:
            try:
                from core.dcc_log import wrap_maya
                res = eval(wrap_maya(code, act, path), {})   # プロセス内で実行（ログ付き）
                if str(res).startswith("Error:"):
                    QMessageBox.warning(self, tr("連携エラー", "Bridge Error"), str(res))
                else:
                    self.statusBar().showMessage(label)
            except Exception as e:
                QMessageBox.critical(self, tr("連携エラー", "Bridge Error"), str(e))
        else:
            self._maya_send_or_prompt(code, label, log=(act, path))

    def _maya_reference(self, path: str, ask_ns: bool = True):
        # デフォルトNamespace: ファイル名を「.」区切りした先頭（例: chr_A.v012.ma → chr_A）
        default_ns = Path(path).name.split(".")[0] or "ref"
        if not self._inside_maya:
            if ask_ns:
                ns, ok = QInputDialog.getText(
                    self, "Namespace",
                    tr("Namespace を入力:", "Enter namespace:"),
                    text=default_ns)
                if not ok:
                    return
            else:
                ns = default_ns
            ns = ((ns or default_ns).replace("'", "").replace('"', "").strip()
                  or default_ns)
            p = escape_path(path)
            # エラーはMaya側のダイアログで表示する（応答は待たない方針のため、
            # 握りつぶすと「何も起きない」ように見えてしまう）
            from core.maya_project import setproject_code
            from core.dcc_caps import MAYA_PLUGIN_FOR_EXT
            _plug = MAYA_PLUGIN_FOR_EXT.get(Path(path).suffix.lower())
            inner = (
                "import maya.cmds as cmds\n"
                + setproject_code(path)     # r89: % 書式の外（後続リテラルだけが書式対象）
                + (("try:\n    cmds.loadPlugin(%r, quiet=True)\n"
                    "except Exception:\n    pass\n" % _plug) if _plug else "") +  # r91
                "try:\n"
                "    cmds.file('%s', reference=True, namespace='%s',"
                " ignoreVersion=True, mergeNamespacesOnClash=False)\n"
                "except Exception as _e:\n"
                "    cmds.confirmDialog(title='Maya File Manager',"
                " message=u'%s\\n' + str(_e), button=['OK'])\n"
                "    _mfm_result = 'Failed: ' + str(_e)\n"
                % (p, ns,
                   tr("リファレンスに失敗しました:", "Failed to reference:"))
            )
            code = "(lambda _ns: (exec(%r, _ns), _ns.get('_mfm_result'))[1])({})" % inner
            self._maya_send_or_prompt(
                code, tr("リファレンス %s", "Reference %s") % Path(path).name,
                log=(tr("リファレンス", "Reference") + " [ns:%s]" % ns, path))
            return
        try:
            import maya.cmds as cmds
            if ask_ns:
                ns, ok = QInputDialog.getText(
                    self, "Namespace",
                    tr("Namespace を入力:", "Enter namespace:"),
                    text=default_ns)
                if not ok:
                    return
            else:
                ns = default_ns
            act = tr("リファレンス", "Reference") + " [ns:%s]" % (ns or default_ns)
            self._maya_local_setproject(path)
            self._maya_local_log(act, path, "start")
            from core.dcc_caps import MAYA_PLUGIN_FOR_EXT
            _plug = MAYA_PLUGIN_FOR_EXT.get(Path(path).suffix.lower())
            if _plug:
                try:
                    cmds.loadPlugin(_plug, quiet=True)
                except Exception as _e:
                    _swallow(_e, "ui/main_window.py:1835 _maya_reference")
            try:
                cmds.file(path, reference=True, namespace=ns or default_ns,
                          ignoreVersion=True, mergeNamespacesOnClash=False)
            except Exception as e:
                self._maya_local_log(act, path, "fail", str(e))
                raise
            self._maya_local_log(act, path, "done")
        except Exception as e:
            QMessageBox.critical(self, tr("リファレンスエラー", "Reference Error"), str(e))

    # ------------------------------------------------------------------
    # Maya version (standalone)
    # ------------------------------------------------------------------

    def _on_maya_version_changed(self, idx: int):
        """ヘッダーの «起動する Maya» を切り替えた時。

        r119: ここで set_maya_version() を呼んではいけない。
        これは «次に起動する Maya を選ぶ» だけの操作なのに、
        «バージョン別の履歴/ブックマーク» の箱まで切り替えてしまい、
        プルダウンを触っただけでブックマークが消えたように見えていた
        （ユーザー指摘 2026-10-01）。スタンドアロンが主な使い方なので
        実害が大きい。バージョン別の箱は «Maya の中で動いている時» に
        だけ意味を持たせる（起動時に一度だけ決める）。"""
        inst = self._maya_combo.itemData(idx)
        if isinstance(inst, MayaInstallation):
            self._maya_inst = inst
            self._sm.set("last_maya_version", str(inst.version), save=False)
        elif isinstance(inst, BlenderInstallation):
            self._blender_inst = inst

    def _current_maya_profile(self):
        """コンボで選ばれている起動プロファイル（無ければ None）。"""
        try:
            i = self._maya_combo.currentIndex()
            profs = getattr(self, "_maya_profiles", [])
            if 0 <= i < len(profs):
                return profs[i]
        except Exception as _e:
            _swallow(_e, "ui/main_window_dcc.py _current_maya_profile")
        return None

    def _maya_launch_args(self):
        """起動に渡す追加引数。共通引数 → プロファイル個別の順に並べる。"""
        import shlex
        out = []
        for text in (str(self._sm.get("maya_extra_args", "") or ""),
                     str((self._current_maya_profile() or {}).get("args", ""))):
            text = text.strip()
            if not text:
                continue
            try:
                out += shlex.split(text, posix=False)
            except ValueError:
                out += text.split()
        return out

    def _open_maya_launch_setup(self):
        """Maya の起動設定（バージョン・引数・表示名）。r119。"""
        # r119: 非モーダル。返り値ではなく accepted で受ける。
        from ui.maya_launch_dialog import MayaLaunchDialog
        from ui.dialog_util import show_tool_window
        versions = [str(i.version) for i in reversed(self._maya_installs)]

        def _make():
            d = MayaLaunchDialog(self._sm, versions, self)
            d.accepted.connect(self._populate_version_combos)
            return d
        show_tool_window(self, "_maya_launch_dlg", _make)

    def _launch_maya(self):
        inst = self._maya_inst
        if not inst:
            QMessageBox.warning(self, tr("エラー", "Error"),
                                tr("Maya バージョンが選択されていません。",
                                   "No Maya version is selected."))
            return
        try:
            # 連携用 commandPort 付きで起動（スタンドアロンから開く/インポート/
            # リファレンスを送り込めるようにする）。複数Maya同時起動に備えて
            # レンジ内の空きポートを割り当て、起動したMayaへ接続先を切り替える。
            port = find_free_port()
            extra = self._maya_launch_args()
            launch_maya(inst, extra_args=extra or None, command_port=port)
            self._bridge.set_port(port)
            self._sm.set("maya_command_port", int(port), save=False)
            self.statusBar().showMessage(
                tr("Maya %s を起動しました（連携ポート :%d）%s",
                   "Launched Maya %s (bridge port :%d)%s")
                % (inst.version, port,
                   ("  " + " ".join(extra)) if extra else ""))
            # Maya起動には時間がかかるため、少し置いて接続リストを再スキャン
            for delay in (8000, 20000, 40000):
                QTimer.singleShot(delay, self._refresh_maya_connections)
        except Exception as e:
            QMessageBox.critical(self, tr("起動エラー", "Launch Error"), str(e))

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------

