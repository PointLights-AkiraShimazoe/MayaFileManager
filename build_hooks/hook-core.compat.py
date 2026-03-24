"""
hook-PySide6.py / hook-PySide2.py に相当するカスタムフック。
PySide6 の Qt プラグイン（platforms, styles, imageformats）を
確実に含めるための補完フック。
"""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# PySide6 が存在する場合のみ有効
try:
    import PySide6 as _pkg
    PKG = "PySide6"
except ImportError:
    try:
        import PySide2 as _pkg
        PKG = "PySide2"
    except ImportError:
        PKG = None

if PKG:
    # Qt プラグインバイナリを収集
    datas = collect_data_files(PKG, includes=["Qt*/plugins/**/*"])
    binaries = collect_dynamic_libs(PKG)
