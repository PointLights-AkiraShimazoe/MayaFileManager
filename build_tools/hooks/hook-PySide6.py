"""
hook-PySide6.py  — custom PyInstaller hook for PySide6
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules("PySide6")
datas = collect_data_files("PySide6")
