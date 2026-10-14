@echo off
REM ============================================================
REM MayaFileManager - start the test watcher (double-click me)
REM
REM Keeps running and waits for a request file. Claude drops
REM ".run_tests_request" into the repo root and this window runs
REM run_tests.bat, updates mfm_tests.log and writes
REM ".run_tests_done".
REM
REM Close this window (or Ctrl+C) when you no longer want Claude
REM to be able to start tests.
REM
REM NOTE: keep this file ASCII-only (CP932 console code page).
REM ============================================================
title MayaFileManager test watcher
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mfm_test_watcher.ps1"
echo.
echo watcher stopped.
pause
