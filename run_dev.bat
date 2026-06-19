@echo off
REM ============================================================
REM Run MayaFileManager from source (no build needed).
REM If your Maya is installed elsewhere, edit the MAYAPY path below.
REM ============================================================
setlocal
set "MAYAPY=C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe"
cd /d "%~dp0"
if exist "%MAYAPY%" (
    "%MAYAPY%" main.py --no-launcher %*
) else (
    echo [info] mayapy not found at: %MAYAPY%
    echo [info] Falling back to 'python' on PATH ^(requires PySide6^)...
    python main.py --no-launcher %*
)
echo.
echo ---- Finished. Press any key to close. ----
pause >nul
