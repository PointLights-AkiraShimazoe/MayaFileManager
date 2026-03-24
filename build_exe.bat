@echo off
setlocal EnableDelayedExpansion
chcp 65001 > nul

echo ================================================
echo  Maya File Manager ^-- EXE Build
echo  Windows / PyInstaller
echo ================================================
echo.

:: ---- Python チェック ------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python が見つかりません。PATH を確認してください。
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do (
    echo [OK] %%v
    set PYVER=%%v
)

:: ---- PyInstaller チェック -------------------------------------------
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [INFO] PyInstaller が見つかりません。インストールします...
    python -m pip install pyinstaller --quiet
    if errorlevel 1 (
        echo [ERROR] PyInstaller のインストールに失敗しました。
        pause & exit /b 1
    )
)
for /f "tokens=*" %%v in ('python -m PyInstaller --version 2^>^&1') do echo [OK] PyInstaller %%v

:: ---- PySide チェック ------------------------------------------------
python -c "import PySide6" >nul 2>&1
if not errorlevel 1 (
    echo [OK] PySide6 が見つかりました
    goto :pyside_ok
)
python -c "import PySide2" >nul 2>&1
if not errorlevel 1 (
    echo [OK] PySide2 が見つかりました
    goto :pyside_ok
)
echo [INFO] PySide6 が見つかりません。インストールします...
python -m pip install PySide6 --quiet
if errorlevel 1 (
    echo [ERROR] PySide6 のインストールに失敗しました。
    pause & exit /b 1
)
echo [OK] PySide6 をインストールしました
:pyside_ok

:: ---- send2trash チェック -------------------------------------------
python -c "import send2trash" >nul 2>&1
if errorlevel 1 (
    echo [INFO] send2trash をインストールします...
    python -m pip install send2trash --quiet
)
echo [OK] send2trash

:: ---- クリーン -------------------------------------------------------
echo.
echo [Clean] 前回ビルドを削除...
if exist build\MayaFileManager  rmdir /s /q build\MayaFileManager
if exist dist\MayaFileManager.exe del /f /q dist\MayaFileManager.exe
if exist __pycache__             rmdir /s /q __pycache__

:: ---- PyInstaller 実行 -----------------------------------------------
echo.
echo [Build] PyInstaller を実行中...
echo         （初回は2〜5分かかります）
echo.

python -m PyInstaller MayaFileManager.spec --clean --noconfirm --log-level WARN

if errorlevel 1 (
    echo.
    echo [ERROR] ビルドに失敗しました。
    echo.
    echo よくある解決策:
    echo   1. pip install --upgrade pyinstaller
    echo   2. アンチウイルスを一時無効化
    echo   3. python -m PyInstaller MayaFileManager.spec --log-level DEBUG
    echo      でエラー詳細を確認
    pause & exit /b 1
)

:: ---- 完了確認 -------------------------------------------------------
if not exist dist\MayaFileManager.exe (
    echo [ERROR] dist\MayaFileManager.exe が生成されませんでした。
    pause & exit /b 1
)

echo.
for %%f in (dist\MayaFileManager.exe) do (
    set /a SIZEMB=%%~zf / 1048576
    echo ================================================
    echo  BUILD COMPLETE
    echo.
    echo  dist\MayaFileManager.exe
    echo  サイズ: !SIZEMB! MB
    echo ================================================
)

echo.
set /p LAUNCH="今すぐ起動しますか？ (y/N): "
if /i "!LAUNCH!"=="y" (
    start "" "dist\MayaFileManager.exe"
)

pause
endlocal
