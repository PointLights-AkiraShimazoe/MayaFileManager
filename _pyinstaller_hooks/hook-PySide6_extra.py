"""
_pyinstaller_hooks/hook-PySide6_extra.py
=========================================
PySide6 の Qt プラグイン（画像形式・スタイル）を確実に同梱するための
カスタム PyInstaller フック。

PyInstaller の標準 PySide6 フックで抜け落ちるケースへの補完。
"""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Qt プラグインディレクトリをまるごと同梱
datas    = collect_data_files('PySide6', includes=['Qt/plugins/**/*'])
binaries = collect_dynamic_libs('PySide6')
