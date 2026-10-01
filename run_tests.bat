@echo off
REM ============================================================
REM Run the offscreen regression suite with mayapy (no display needed).
REM Result is printed here AND written to mfm_tests.log next to this file,
REM so Claude can read the log when its sandbox is unavailable.
REM
REM mayapy selection is the same as run_dev.bat
REM   (MFM_MAYA_VER, else newest Maya 2027 -> 2023, else python on PATH).
REM
REM NOTE: keep this file ASCII-only (CP932 console code page).
REM ============================================================
setlocal
cd /d "%~dp0"
set "MAYAPY="
set "QT_QPA_PLATFORM=offscreen"

if defined MFM_MAYA_VER (
    if exist "C:\Program Files\Autodesk\Maya%MFM_MAYA_VER%\bin\mayapy.exe" (
        set "MAYAPY=C:\Program Files\Autodesk\Maya%MFM_MAYA_VER%\bin\mayapy.exe"
    )
)

if not defined MAYAPY (
    for %%V in (2027 2026 2025 2024 2023) do (
        if not defined MAYAPY (
            if exist "C:\Program Files\Autodesk\Maya%%V\bin\mayapy.exe" (
                set "MAYAPY=C:\Program Files\Autodesk\Maya%%V\bin\mayapy.exe"
            )
        )
    )
)

if defined MAYAPY (
    echo [info] using: %MAYAPY%
    "%MAYAPY%" tests\run_offscreen.py
) else (
    echo [info] mayapy not found. Falling back to 'python' on PATH ^(requires PySide6^)...
    python tests\run_offscreen.py
)
echo.
echo ---- Finished. Result also in mfm_tests.log. Press any key to close. ----
pause >nul
