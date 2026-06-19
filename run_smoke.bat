@echo off
REM ============================================================
REM Run the headless smoke test (self-check before pushing).
REM ============================================================
setlocal
set "MAYAPY=C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe"
cd /d "%~dp0"
set QT_QPA_PLATFORM=offscreen
if exist "%MAYAPY%" (
    "%MAYAPY%" tests\smoke_test.py
) else (
    python tests\smoke_test.py
)
echo.
pause >nul
