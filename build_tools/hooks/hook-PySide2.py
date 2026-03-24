"""
hook-PySide2.py  — custom PyInstaller hook for PySide2
=======================================================
デフォルトフックで漏れる QtWidgets / QtGui のサブモジュールを
明示的に pull-in する。
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# サブモジュール全収集
hiddenimports = collect_submodules("PySide2")

# Qt プラグイン・翻訳ファイル
datas = collect_data_files("PySide2")
