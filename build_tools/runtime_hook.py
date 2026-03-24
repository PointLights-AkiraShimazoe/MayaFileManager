"""
PyInstaller runtime hook — MayaFileManager
==========================================
実行時に sys.path とQt プラグインパスを正しく設定する。
_MEIPASS は PyInstaller が展開する一時ディレクトリ。
"""

import os
import sys

# ── Project root (inside the bundle) ──────────────────────────────────────
_base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if _base not in sys.path:
    sys.path.insert(0, _base)

# ── Qt platform plugin path ───────────────────────────────────────────────
# Without this, Qt cannot find qwindows.dll (Windows) / libqxcb.so (Linux)
def _setup_qt_plugins():
    for binding in ("PySide6", "PySide2"):
        plugin_dir = os.path.join(_base, binding, "plugins")
        if os.path.isdir(plugin_dir):
            os.environ.setdefault("QT_PLUGIN_PATH", plugin_dir)
            # Also try QtCore.QCoreApplication.addLibraryPath after Qt loads
            try:
                if binding == "PySide6":
                    from PySide6.QtCore import QCoreApplication
                else:
                    from PySide2.QtCore import QCoreApplication
                QCoreApplication.addLibraryPath(plugin_dir)
            except Exception:
                pass
            break

_setup_qt_plugins()

# ── Suppress Maya import errors when running standalone ───────────────────
# Maya's Python modules are never bundled; silence the ImportError so
# is_running_inside_maya() returns False cleanly.
class _MayaBlocker:
    """Fake module that raises ImportError on attribute access."""
    def __getattr__(self, name):
        raise ImportError("maya not available in standalone mode")

_MAYA_STUBS = ["maya", "maya.cmds", "maya.mel", "maya.OpenMaya",
               "maya.OpenMayaUI", "maya.api.OpenMaya",
               "shiboken2", "shiboken6"]
for _mod in _MAYA_STUBS:
    if _mod not in sys.modules:
        sys.modules[_mod] = _MayaBlocker()  # type: ignore
