#!/usr/bin/env bash
# Maya File Manager — Build Script (macOS / Linux)
set -e
cd "$(dirname "$0")"

step() { echo -e "\n\033[1m$*\033[0m"; }
info() { echo "  [OK]   $*"; }
warn() { echo "  [WARN] $*"; }
err()  { echo "  [ERR]  $*"; exit 1; }

echo "================================================"
echo " Maya File Manager — EXE/App Build"
echo "================================================"

PYTHON=python3
command -v $PYTHON >/dev/null 2>&1 || err "python3 not found"
info "$($PYTHON --version)"

step "[1] Installing deps..."
$PYTHON -m pip install --upgrade pip -q
$PYTHON -m pip install PySide6 send2trash pyinstaller -q || warn "Some packages failed"

step "[2] Checking UPX..."
command -v upx >/dev/null 2>&1 && info "UPX found" || warn "No UPX (brew install upx)"

step "[3] Clean..."
rm -rf build/MayaFileManager dist/MayaFileManager dist/MayaFileManager.app

step "[4] Building..."
$PYTHON -m PyInstaller MayaFileManager.spec --clean --noconfirm --log-level WARN

echo ""
echo "================================================"
echo " BUILD COMPLETE"
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo " dist/MayaFileManager.app"
else
    echo " dist/MayaFileManager"
fi
echo "================================================"

read -rp "Launch now? (y/N): " L
if [[ "$L" =~ ^[Yy]$ ]]; then
    [[ "$OSTYPE" == "darwin"* ]] && open dist/MayaFileManager.app || ./dist/MayaFileManager &
fi
