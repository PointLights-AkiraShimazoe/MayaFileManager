"""
Maya File Manager — Entry Point
================================

Standalone mode
---------------
    python main.py                  → マネージャーを直接開く

Inside Maya (shelf button or userSetup.py)
------------------------------------------
    import sys
    sys.path.insert(0, r"/path/to/MayaFileManager")
    import main
    main.show_in_maya()
"""
from core.diag import swallow as _swallow  # r112

import os
import sys

# Ensure the project root is on sys.path regardless of how this is invoked
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _bootstrap_pyside():
    """
    When running standalone outside Maya we need to ensure a Qt application
    exists before instantiating any widgets.
    Returns (app, created_new) where created_new=True when we created the app.
    """
    from core.compat import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        app.setApplicationName("MayaFileManager")
        app.setOrganizationName("PointLights")
        # 【重要】スタンドアロンでは OS ネイティブのファイル/フォルダダイアログを
        # 使わない（r64）。Windows のネイティブダイアログはシェル拡張
        # （TortoiseSVN/Git の オーバーレイ、Perforce P4EXP 等）をプロセス内へ
        # 読み込み、mayapy/EXE では QFileDialog.getExistingDirectory の中で
        # プロセスごとクラッシュした（mfm_freeze.log: _browse の中で 4 秒停止
        # → プロセス消滅、2026-09-11）。Qt 製ダイアログ（テーマ適用）で統一する。
        # Maya 内モードでは Maya の QApplication を触らないためここには来ない。
        try:
            from core.compat import Qt as _Qt
            app.setAttribute(_Qt.AA_DontUseNativeDialogs, True)
        except Exception as _e:
            _swallow(_e, "main.py:48 _bootstrap_pyside")
        _apply_theme_from_settings(app)
        return app, True
    return app, False


def _setup_error_logging():
    """
    未捕捉例外を ~/.maya_file_manager/error.log に記録し、
    可能ならダイアログでも表示する（console=False のEXEでは必須）。
    """
    import traceback
    from pathlib import Path
    from datetime import datetime

    log_dir = Path.home() / ".maya_file_manager"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "error.log"

    # ネイティブクラッシュ(C++層segfault等)もcrash.logに記録する
    # ファイルハンドルはGC防止のためグローバル保持
    global _crash_log_handle
    try:
        import faulthandler
        _crash_log_handle = open(log_dir / "crash.log", "a", encoding="utf-8")
        from datetime import datetime as _dt
        _crash_log_handle.write(f"\n===== session start {_dt.now():%Y-%m-%d %H:%M:%S} =====\n")
        _crash_log_handle.flush()
        faulthandler.enable(_crash_log_handle)
    except Exception as _e:
        _swallow(_e, "main.py:78 _setup_error_logging")

    def _hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n===== {stamp} =====\n{text}")
        except OSError:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        try:
            from core.compat import QApplication, QMessageBox
            if QApplication.instance():
                QMessageBox.critical(
                    None, "Maya File Manager — エラー",
                    f"予期しないエラーが発生しました。\n\n{exc_value}\n\n"
                    f"詳細ログ: {log_file}")
        except Exception as _e:
            _swallow(_e, "main.py:97 _hook")

    sys.excepthook = _hook
    return log_file


def _report_window_error(exc):
    """MainWindow 生成失敗をユーザーに見える形で報告する。"""
    import traceback
    from pathlib import Path
    from datetime import datetime
    log_file = Path.home() / ".maya_file_manager" / "error.log"
    text = traceback.format_exc()
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} (MainWindow) =====\n{text}")
    except OSError:
        pass
    from core.compat import QMessageBox
    QMessageBox.critical(
        None, "Maya File Manager — 起動エラー",
        f"マネージャーウィンドウの作成に失敗しました。\n\n{exc}\n\n"
        f"詳細ログ: {log_file}")


def _apply_theme_from_settings(app):
    """
    Apply the design-token driven theme (config/design_tokens.json)。
    モードは設定 "theme"（dark / light）に従う（r82。以前は dark 固定で、
    設定ダイアログのテーマ選択が効いていなかった）。
    Falls back to the legacy basic palette if the theme engine fails.
    """
    mode = "dark"
    try:
        from core.settings_manager import SettingsManager
        mode = SettingsManager().get("theme", "dark")
        if mode not in ("dark", "light"):
            mode = "dark"
    except Exception as _e:
        _swallow(_e, "main.py:137 _apply_theme_from_settings")
    try:
        from core.theme_engine import apply_theme
        apply_theme(app, mode=mode)
        return
    except Exception as e:
        print(f"[MayaFileManager] theme_engine failed, using fallback palette: {e}")
        _apply_fallback_palette(app)


def _apply_fallback_palette(app):
    """Legacy basic dark palette (kept as a safety net)."""
    from core.compat import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.Window,          QColor(45, 45, 45))
    palette.setColor(QPalette.WindowText,      QColor(210, 210, 210))
    palette.setColor(QPalette.Base,            QColor(30, 30, 30))
    palette.setColor(QPalette.AlternateBase,   QColor(50, 50, 50))
    palette.setColor(QPalette.ToolTipBase,     QColor(60, 60, 60))
    palette.setColor(QPalette.ToolTipText,     QColor(210, 210, 210))
    palette.setColor(QPalette.Text,            QColor(210, 210, 210))
    palette.setColor(QPalette.Button,          QColor(55, 55, 55))
    palette.setColor(QPalette.ButtonText,      QColor(210, 210, 210))
    palette.setColor(QPalette.BrightText,      QColor(255, 80, 80))
    palette.setColor(QPalette.Link,            QColor(80, 160, 230))
    palette.setColor(QPalette.Highlight,       QColor(42, 80, 128))
    palette.setColor(QPalette.HighlightedText, QColor(240, 240, 240))
    app.setPalette(palette)
    app.setStyle("Fusion")


# ---------------------------------------------------------------------------
# Standalone entry
# ---------------------------------------------------------------------------

def _log_env(tag: str):
    """実行環境（Python/Qt/PySide/Maya）を起動タイムラインへ記録する。
    Maya 2027 等、バージョン差の切り分けに使う。"""
    try:
        import platform
        from ui.browser_panel import _mfm_timeline
        from core.compat import QtCore
        pyside = ""
        try:
            import PySide6
            pyside = "PySide6 " + PySide6.__version__
        except Exception:
            try:
                import PySide2
                pyside = "PySide2 " + PySide2.__version__
            except Exception as _e:
                _swallow(_e, "main.py:188 _log_env")
        maya = ""
        try:
            import maya.cmds as cmds
            maya = " / Maya " + str(cmds.about(version=True))
        except Exception as _e:
            _swallow(_e, "main.py:194 _log_env")
        _mfm_timeline("env[%s]: python %s / qt %s / %s%s / %s / exe=%s"
                      % (tag, sys.version.split()[0], QtCore.qVersion(),
                         pyside, maya, platform.platform(), sys.executable))
    except Exception as _e:
        _swallow(_e, "main.py:199 _log_env")


def run_standalone(skip_launcher: bool = True):
    """スタンドアロン起動。**常にマネージャーを直接開く**（r111）。

    旧 Launcher ダイアログ（Maya バージョンを選んでから開く）は廃止した。
    Maya の起動はマネージャー側のヘッダから行えるため、起動のたびに
    1 枚挟む意味が無くなったため（2026-10 指示）。
    引数 skip_launcher は後方互換のために残しているが参照しない。
    """
    app, created = _bootstrap_pyside()
    _setup_error_logging()
    _log_env("standalone")

    from core.settings_manager import SettingsManager
    sm = SettingsManager()

    # UI表示言語を確定（auto=Mayaの言語モード追従 / ja / en。再起動で反映）
    from core import i18n
    i18n.init(sm)

    try:
        _open_main_window(sm, maya_installation=None)
    except Exception as e:
        _report_window_error(e)
        sys.exit(1)

    if created:
        from core.compat import exec_app
        sys.exit(exec_app(app))


def _open_main_window(settings_manager, maya_installation=None):
    from ui.main_window import MainWindow
    win = MainWindow(settings_manager, maya_installation=maya_installation)
    win.show()
    win.raise_()
    return win


# ---------------------------------------------------------------------------
# Inside-Maya entry
# ---------------------------------------------------------------------------

_maya_window_instance = None
_crash_log_handle = None


def show_in_maya():
    """
    Show (or raise) the manager window when called from inside Maya.
    Safe to call multiple times – will raise the existing window if open.
    """
    global _maya_window_instance

    app, _ = _bootstrap_pyside()
    _log_env("in-maya")

    from core.maya_version import get_current_maya_version
    from core.settings_manager import SettingsManager

    maya_ver = get_current_maya_version()
    sm = SettingsManager(maya_version=maya_ver)

    # UI表示言語を確定（auto=Mayaの言語モード追従 / ja / en）
    from core import i18n
    i18n.init(sm)

    # Maya 内では Maya 側の QApplication に stylesheet/palette を «当てない»
    # （Maya 全体の見た目を壊すため）。ただしウィジェット個別のスタイルが
    # 参照するテーマモードだけは設定に合わせておく（r82）。
    try:
        from core.theme_engine import set_mode
        set_mode(sm.get("theme", "dark"))
    except Exception as _e:
        _swallow(_e, "main.py:275 show_in_maya")

    if _maya_window_instance is not None:
        try:
            _maya_window_instance.raise_()
            _maya_window_instance.activateWindow()
            return _maya_window_instance
        except RuntimeError:
            # C++ object deleted
            _maya_window_instance = None

    from ui.main_window import MainWindow
    win = MainWindow(sm, maya_installation=None)

    # Attempt to parent to Maya's main window for proper docking behaviour
    try:
        from maya.OpenMayaUI import MQtUtil
        from core.compat import QWidget
        try:
            from PySide6.QtCore import Qt
            from shiboken6 import wrapInstance
        except ImportError:
            from PySide2.QtCore import Qt
            from shiboken2 import wrapInstance
        maya_main_ptr = MQtUtil.mainWindow()
        if maya_main_ptr:
            maya_main = wrapInstance(int(maya_main_ptr), QWidget)
            win.setParent(maya_main, win.windowFlags())
    except Exception as _e:
        _swallow(_e, "main.py:304 show_in_maya")

    win.show()
    win.raise_()
    _maya_window_instance = win
    return win


# ---------------------------------------------------------------------------
# Maya plugin stubs (optional: register as a Maya plugin)
# ---------------------------------------------------------------------------

def initializePlugin(plugin):  # noqa: N802
    """Maya plugin initialize – registers a menu item."""
    try:
        import maya.api.OpenMaya as om
        om.MFnPlugin(plugin, "PointLights", "1.0")
        try:
            import maya.cmds as cmds
            cmds.setParent("MayaWindow|mainMenuBar", menu=True)
            if not cmds.menu("MFMMenu", exists=True):
                cmds.menu("MFMMenu", label="File Manager", tearOff=True)
            cmds.setParent("MFMMenu", menu=True)
            cmds.menuItem(label="Open File Manager",
                          command="import main; main.show_in_maya()")
        except Exception as _e:
            _swallow(_e, "main.py:330 initializePlugin")
    except Exception as _e:
        _swallow(_e, "main.py:332 initializePlugin")


def uninitializePlugin(plugin):  # noqa: N802
    try:
        import maya.cmds as cmds
        if cmds.menu("MFMMenu", exists=True):
            cmds.deleteUI("MFMMenu")
    except Exception as _e:
        _swallow(_e, "main.py:341 uninitializePlugin")


# ---------------------------------------------------------------------------
# セルフチェック（r121）
# ---------------------------------------------------------------------------

# 2026-10-05 実機障害: spec の excludes に 'xml' が入っていたため、EXE 版
# でだけ core.integrations の import が失敗し、Git / SVN / Perforce /
# クラウドの連携が丸ごと無効になっていた。開発版では標準ライブラリが普通に
# 入っているので誰も気付けず、そのまま配布してしまった。
#
# 以後、**ビルドした EXE そのもの** に対して CI がこれを実行する。
# 「開発版では動くのにリリース版だけ機能が欠ける」を機械で止めるための関所。
# 新しい機能を足したら、その要になるモジュールをここへ追加すること。
_REQUIRED_MODULES = [
    # 外部サービス連携（ここが落ちるとバッジも右クリックメニューも消える）
    "core.integrations",
    "core.integrations.manager",
    "core.integrations.git_provider",
    "core.integrations.svn_provider",
    "core.integrations.p4_provider",
    "core.integrations.cloud_provider",
    "xml.etree.ElementTree",
    # DCC 連携
    "core.maya_bridge",
    "core.blender_bridge",
    "core.dcc_save",
    "core.dcc_caps",
    # 画面
    "ui.main_window",
    "ui.browser_panel",
    "ui.browser_column_view",
    "ui.browser_delegates",
    "ui.flat_column",
    "ui.dialog_util",
    "ui.maya_bridge_dialog",
    "ui.maya_launch_dialog",
    "ui.settings_dialog",
    "ui.save_dialog",
    # 下回り
    "core.settings_manager",
    "core.theme_engine",
    "core.display_names",
    "core.thumbnail_generator",
    "core.file_operations",
    "core.undo_stack",
    "core.path_guard",
]


def selfcheck(out_path: str = "") -> int:
    """必要なモジュールが «この実行形態で» 全部読めるかを確かめる。
    0=すべて揃っている / 1=欠けがある（CI はここで落とす）。

    【重要】この EXE は **ウィンドウアプリ（console=False）** としてビルド
    される。その形態では sys.stdout / sys.stderr が無い（None）ので、
    print() はそれ自体が落ちる。結果は **必ずファイルへ** 書き、標準出力へは
    «書ければ書く» に留める。未処理例外もメッセージボックスで CI を
    止めてしまうので、ここで全部受け止める（2026-10-05 実機）。
    """
    import importlib
    import traceback
    lines = []

    def say(text):
        lines.append(text)
        try:
            if sys.stdout is not None:
                sys.stdout.write(text + "\n")
        except Exception:
            pass

    code = 1
    try:
        missing = []
        for name in _REQUIRED_MODULES:
            try:
                importlib.import_module(name)
            except Exception as e:
                missing.append((name, repr(e), traceback.format_exc()))
        # 連携プロバイダが 4 種そろっているか（import だけでなく実体で確認）
        try:
            from core.integrations import get_manager
            keys = sorted(p.key for p in get_manager().providers)
            if keys != ["cloud", "git", "p4", "svn"]:
                missing.append(("integrations.providers",
                                "プロバイダが揃っていない: %r" % (keys,), ""))
        except Exception as e:
            missing.append(("integrations.providers", repr(e),
                            traceback.format_exc()))

        if missing:
            say("[selfcheck] NG — 欠けている機能があります（%d 件）"
                % len(missing))
            for name, err, tb in missing:
                say("  - %s: %s" % (name, err))
                if tb:
                    say("    " + tb.replace("\n", "\n    ").rstrip())
            code = 1
        else:
            say("[selfcheck] OK — 必要なモジュール %d 件すべて読み込めました"
                % len(_REQUIRED_MODULES))
            code = 0
    except BaseException as e:            # noqa: BLE001 — CI を止めない
        say("[selfcheck] NG — セルフチェック自体が落ちました: %r" % (e,))
        try:
            say(traceback.format_exc())
        except Exception:
            pass
        code = 1

    if out_path:
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass
    return code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Maya File Manager")
    parser.add_argument("--no-launcher", action="store_true",
                        help="（廃止）常にマネージャーを直接開くため、指定しても動作は変わらない")
    parser.add_argument("--maya-ver", default="",
                        help="使用する Maya バージョン (例: 2027)")
    parser.add_argument("--selfcheck", action="store_true",
                        help="画面を出さずに «機能が揃っているか» だけ検査して終了"
                             "（CI がビルドした EXE に対して実行する）")
    parser.add_argument("--selfcheck-out", default="",
                        help="セルフチェックの結果を書き出すファイル。"
                             "ウィンドウアプリでは標準出力が無いので CI はこれを読む")
    args = parser.parse_args()

    if args.selfcheck:
        raise SystemExit(selfcheck(args.selfcheck_out))

    run_standalone(skip_launcher=args.no_launcher)
