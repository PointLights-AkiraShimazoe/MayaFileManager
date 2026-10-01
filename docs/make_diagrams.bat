@echo off
rem Build code-structure diagrams with Archify (tt-a1i/archify).
rem Output: docs\architecture.html, docs\workflow.html
rem Log:    docs\diagrams\archify_report.txt  (Claude reads this)
setlocal
cd /d "%~dp0"
set LOG=diagrams\archify_report.txt
set ARCHIFY=%USERPROFILE%\.claude\skills\archify\bin\archify.mjs
if not exist "%ARCHIFY%" set ARCHIFY=%USERPROFILE%\.claude\skills\archify\archify\bin\archify.mjs
where node >nul 2>nul
if errorlevel 1 (
  echo node.exe not found in PATH. Install Node.js 18+ from https://nodejs.org/ > "%LOG%"
  echo node.exe not found in PATH. Install Node.js 18+ from https://nodejs.org/
  pause
  exit /b 1
)
if not exist "%ARCHIFY%" (
  echo archify not installed. Installing: npx skills add tt-a1i/archify -g
  call npx --yes skills add tt-a1i/archify -g -y
  set ARCHIFY=%USERPROFILE%\.claude\skills\archify\bin\archify.mjs
  if not exist "%ARCHIFY%" set ARCHIFY=%USERPROFILE%\.claude\skills\archify\archify\bin\archify.mjs
)
if not exist "%ARCHIFY%" (
  rem last resort: clone the repo next to this bat
  where git >nul 2>nul && git clone --depth 1 https://github.com/tt-a1i/archify "%~dp0_archify"
  set ARCHIFY=%~dp0_archify\archify\bin\archify.mjs
)
if not exist "%ARCHIFY%" (
  echo archify.mjs still not found. > "%LOG%"
  echo archify.mjs not found. Run manually: npx skills add tt-a1i/archify -g
  pause
  exit /b 1
)
echo === archify %DATE% %TIME% === > "%LOG%"
echo ARCHIFY=%ARCHIFY% >> "%LOG%"
node --version >> "%LOG%" 2>&1

echo --- validate architecture --- >> "%LOG%"
node "%ARCHIFY%" validate architecture diagrams\architecture.architecture.json --quality showcase --json >> "%LOG%" 2>&1
echo exit=%ERRORLEVEL% >> "%LOG%"
echo --- deliver architecture --- >> "%LOG%"
node "%ARCHIFY%" deliver architecture diagrams\architecture.architecture.json architecture.html --quality showcase --json >> "%LOG%" 2>&1
echo exit=%ERRORLEVEL% >> "%LOG%"

echo --- validate workflow --- >> "%LOG%"
node "%ARCHIFY%" validate workflow diagrams\workflow.workflow.json --quality showcase --json >> "%LOG%" 2>&1
echo exit=%ERRORLEVEL% >> "%LOG%"
echo --- deliver workflow --- >> "%LOG%"
node "%ARCHIFY%" deliver workflow diagrams\workflow.workflow.json workflow.html --quality showcase --json >> "%LOG%" 2>&1
echo exit=%ERRORLEVEL% >> "%LOG%"

echo === done === >> "%LOG%"
type "%LOG%"
pause
