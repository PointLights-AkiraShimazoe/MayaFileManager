"""
Maya File Manager — Entry Point
================================

Standalone mode
---------------
    python main.py                  → Launcher dialog → pick Maya → open manager
    python main.py --no-launcher    → Skip launcher, open manager directly

Inside Maya (shelf button or userSetup.py)
------------------------------------------
    import sys
    sys.path.insert(0, r"/path/to/MayaFileManager")
    import main
    main.show_in_maya()
"""

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
        _apply_dark_theme(app)
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
        except Exception:
            pass

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


def _apply_dark_theme(app):
    """
    Apply the design-token driven M3 theme (config/design_tokens.json).
    Falls back to the legacy basic palette if the theme engine fails.
    """
    try:
        from core.theme_engine import apply_theme
        apply_theme(app, mode="dark")
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

def run_standalone(skip_launcher: bool = False):
    """
    Start as a standalone application.
    """
    app, created = _bootstrap_pyside()
    _setup_error_logging()

    from core.settings_manager import SettingsManager
    sm = SettingsManager()

    if skip_launcher:
        # Open manager immediately without choosing Maya version
        _open_main_window(sm, maya_installation=None)
    else:
        from ui.launcher_dialog import LauncherDialog
        launcher = LauncherDialog()

        _pending_window = []  # hold reference to prevent GC

        def on_launch(installation, file_path):
            try:
                win = _open_main_window(sm, maya_installation=installation)
                _pending_window.append(win)
                if file_path:
                    win._browser.navigate_to(
                        os.path.dirname(file_path) if os.path.isfile(file_path) else file_path
                    )
            except Exception as e:
                _report_window_error(e)

        def on_manager_only():
            try:
                win = _open_main_window(sm, maya_installation=None)
                _pending_window.append(win)
            except Exception as e:
                _report_window_error(e)

        launcher.launch_requested.connect(on_launch)
        launcher.open_manager_only.connect(on_manager_only)

        result = launcher.exec_() if hasattr(launcher, "exec_") else launcher.exec()

        # ウィンドウが1つも開けなかった場合は終了
        # （キャンセル時だけでなく、生成失敗時も透明なプロセスを残さない）
        if not _pending_window:
            sys.exit(0)

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


def show_in_maya():
    """
    Show (or raise) the manager window when called from inside Maya.
    Safe to call multiple times – will raise the existing window if open.
    """
    global _maya_window_instance

    app, _ = _bootstrap_pyside()

    from core.maya_version import get_current_maya_version
    from core.settings_manager import SettingsManager

    maya_ver = get_current_maya_version()
    sm = SettingsManager(maya_version=maya_ver)

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
    except Exception:
        pass

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
        except Exception:
            pass
    except Exception:
        pass


def uninitializePlugin(plugin):  # noqa: N802
    try:
        import maya.cmds as cmds
        if cmds.menu("MFMMenu", exists=True):
            cmds.deleteUI("MFMMenu")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Maya File Manager")
    parser.add_argument("--no-launcher", action="store_true",
                        help="Launcher ダイアログをスキップしてマネージャーを直接開く")
    parser.add_argument("--maya-ver", default="",
                        help="使用する Maya バージョン (例: 2027)")
    args = parser.parse_args()

    run_standalone(skip_launcher=args.no_launcher)
