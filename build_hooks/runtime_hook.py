"""
runtime_hook.py
===============
PyInstaller の frozen 実行環境向けパッチ。
exe 起動時に自動的に呼ばれる。

対応する問題
------------
1. sys.path にアプリルートが含まれない
2. QFileSystemModel が frozen 環境でアイコンを見失う
3. Qt プラグインパスが通っていない
"""

import sys
import os


# ---------------------------------------------------------------------------
# 1. sys.path にアプリルートを追加
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    # _MEIPASS = PyInstaller が展開したテンポラリディレクトリ
    app_root = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    if app_root not in sys.path:
        sys.path.insert(0, app_root)

    # exe と同じディレクトリも追加（onedir モードでは config/ など隣にある）
    exe_dir = os.path.dirname(sys.executable)
    if exe_dir not in sys.path:
        sys.path.insert(0, exe_dir)


# ---------------------------------------------------------------------------
# 2. Qt プラグインパス（PySide2/6 共通）
# ---------------------------------------------------------------------------
def _fix_qt_plugin_path():
    try:
        # PySide6
        try:
            from PySide6.QtCore import QCoreApplication
            import PySide6
            plugin_path = os.path.join(os.path.dirname(PySide6.__file__), "Qt6", "plugins")
        except ImportError:
            from PySide2.QtCore import QCoreApplication
            import PySide2
            plugin_path = os.path.join(os.path.dirname(PySide2.__file__), "Qt", "plugins")

        if os.path.isdir(plugin_path):
            QCoreApplication.addLibraryPath(plugin_path)

        # Also set env var for fallback
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                               os.path.join(plugin_path, "platforms"))
    except Exception:
        pass


_fix_qt_plugin_path()


# ---------------------------------------------------------------------------
# 3. Windows: DPI awareness（ぼやけ防止）
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Per-monitor V2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
