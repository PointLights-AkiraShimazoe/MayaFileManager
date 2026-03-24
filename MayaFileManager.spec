# -*- mode: python ; coding: utf-8 -*-
"""
MayaFileManager.spec
====================
PyInstaller 5.x / 6.x 両対応。

    pyinstaller MayaFileManager.spec --clean --noconfirm

出力: dist/MayaFileManager.exe  (Windows onefile exe)
"""

from pathlib import Path
import PyInstaller  # version check

project_root = Path(SPECPATH)

# PyInstaller 6.x では cipher 引数が廃止
_pi_ver = tuple(int(x) for x in PyInstaller.__version__.split(".")[:2])
_cipher_kwargs = {} if _pi_ver >= (6, 0) else {"cipher": None}

# ---------------------------------------------------------------------------
# 同梱データ
# ---------------------------------------------------------------------------
_datas = [
    (str(project_root / 'config'),    'config'),
    (str(project_root / 'resources'), 'resources'),
]
# resources が空フォルダでも zip に入れるため dummy チェック
for src, dst in list(_datas):
    if not Path(src).exists():
        _datas.remove((src, dst))

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(project_root / 'main.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        # Qt バインディング
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
        'shiboken6',
        # PySide2 fallback
        'PySide2',
        'PySide2.QtCore',
        'PySide2.QtGui',
        'PySide2.QtWidgets',
        'shiboken2',
        # アプリ内モジュール（念のため明示）
        'core.compat',
        'core.maya_version',
        'core.settings_manager',
        'core.bookmark_manager',
        'core.file_operations',
        'core.thumbnail_generator',
        'ui.launcher_dialog',
        'ui.main_window',
        'ui.browser_panel',
        'ui.bookmark_panel',
        'ui.preset_editor',
        'ui.settings_dialog',
        'ui.batch_rename_dialog',
        'ui.quick_nav_editor',
        'ui.reference_editor',
        'ui.duplicate_folder_panel',
        # オプション依存
        'send2trash',
        'cv2',
        # stdlib
        'pathlib', 'json', 'struct', 'subprocess', 'platform',
        'uuid', 'collections', 'copy', 're', 'shutil', 'os', 'sys',
    ],
    hookspath=[str(project_root / '_pyinstaller_hooks')],
    runtime_hooks=[],
    excludes=[
        # Maya は実行時不要（起動パスとして参照するだけ）
        'maya', 'maya.cmds', 'maya.mel',
        # 巨大ライブラリ除外でサイズ削減
        'matplotlib', 'numpy', 'scipy', 'pandas',
        'PIL', 'Pillow',
        'tkinter', '_tkinter',
        'test', 'unittest',
        'email', 'html', 'http', 'xml', 'xmlrpc',
        'pydoc', 'doctest', 'difflib',
        'multiprocessing',  # QThreadPool で代替
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    **_cipher_kwargs,
)

# ---------------------------------------------------------------------------
# PYZ
# ---------------------------------------------------------------------------
pyz = PYZ(a.pure, a.zipped_data, **_cipher_kwargs)

# ---------------------------------------------------------------------------
# アイコン / バージョン情報（存在する場合のみ有効）
# ---------------------------------------------------------------------------
_icon = None
for _ico_name in ('mfm.ico', 'app.ico'):
    _p = project_root / 'resources' / 'icons' / _ico_name
    if _p.exists():
        _icon = str(_p)
        break

_ver = str(project_root / 'version_info.txt') \
    if (project_root / 'version_info.txt').exists() else None

# ---------------------------------------------------------------------------
# EXE  ── onefile モード
# ---------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MayaFileManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        'vcruntime*.dll',
        'ucrtbase.dll',
        'python*.dll',
        'Qt6*.dll',
        'Qt5*.dll',
        'PySide6*.pyd',
        'PySide2*.pyd',
    ],
    runtime_tmpdir=None,
    console=False,          # ウィンドウアプリ（コンソール非表示）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
    version=_ver,
)
