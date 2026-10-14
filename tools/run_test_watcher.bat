@echo off
REM ============================================================
REM MayaFileManager - start the watcher (double-click me)
REM
REM Keeps running and waits for request files in the repo root:
REM   .run_tests_request  -> runs run_tests.bat, updates mfm_tests.log,
REM                          writes .run_tests_done
REM   .run_git_request    -> runs a whitelisted git command here, writes
REM                          .run_git_out.log and .run_git_done
REM                          (credentials never leave this PC)
REM
REM The watcher reloads itself when mfm_test_watcher.ps1 changes,
REM so it does not need to be restarted after an update.
REM
REM Close this window (or Ctrl+C) when you no longer want Claude
REM to be able to start tests or push.
REM
REM NOTE: keep this file ASCII-only (CP932 console code page).
REM ============================================================
title MayaFileManager watcher
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mfm_test_watcher.ps1"
echo.
echo watcher stopped.
pause
