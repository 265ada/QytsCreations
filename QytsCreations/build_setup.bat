@echo off
setlocal enabledelayedexpansion
title QytCroRec — Build Setup

echo ============================================================
echo  QytCroRec Build Setup
echo  Installs all dependencies then builds QytCroRec.exe
echo ============================================================
echo.

REM ── Python packages ─────────────────────────────────────────────────────────
echo [1/3] Installing Python dependencies...
pip install --upgrade Pillow pytesseract pyinstaller
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. Make sure Python is on PATH.
    pause & exit /b 1
)

REM ── Tesseract-OCR ───────────────────────────────────────────────────────────
echo.
echo [2/3] Checking for Tesseract-OCR...

set "TESS_DIR="
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe"       set "TESS_DIR=C:\Program Files\Tesseract-OCR"
if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" set "TESS_DIR=C:\Program Files (x86)\Tesseract-OCR"
if exist "vendor\tesseract\tesseract.exe"                      set "TESS_DIR=%~dp0vendor\tesseract"

where tesseract >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('where tesseract') do (
        if "!TESS_DIR!"=="" set "TESS_DIR=%%~dpi"
    )
)

if not "!TESS_DIR!"=="" (
    echo     Found: !TESS_DIR!
    goto :build
)

REM Tesseract not found — download installer silently to a local vendor folder
echo     Not found. Downloading Tesseract 5.x portable installer...
set "TESS_INSTALLER=%TEMP%\tesseract_setup.exe"
set "TESS_INSTALL_DIR=C:\Program Files\Tesseract-OCR"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Invoke-WebRequest -Uri 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.3.3.20231005/tesseract-ocr-w64-setup-5.3.3.20231005.exe' -OutFile '%TESS_INSTALLER%' -UseBasicParsing"

if not exist "%TESS_INSTALLER%" (
    echo ERROR: Download failed. Install Tesseract manually from:
    echo   https://github.com/UB-Mannheim/tesseract/wiki
    echo Then re-run this script.
    pause & exit /b 1
)

echo     Installing Tesseract (may prompt for admin elevation)...
"%TESS_INSTALLER%" /S
del /q "%TESS_INSTALLER%" 2>nul

REM Wait for install to finish
timeout /t 5 /nobreak >nul

if exist "%TESS_INSTALL_DIR%\tesseract.exe" (
    echo     Installed to: %TESS_INSTALL_DIR%
    set "TESS_DIR=%TESS_INSTALL_DIR%"
) else (
    echo ERROR: Tesseract install did not complete as expected.
    echo Install manually: https://github.com/UB-Mannheim/tesseract/wiki
    pause & exit /b 1
)

REM ── Build ────────────────────────────────────────────────────────────────────
:build
echo.
echo [3/3] Building QytCroRec.exe with PyInstaller...
echo     Tesseract will be bundled from: !TESS_DIR!
echo.

cd /d "%~dp0"
pyinstaller QytCroRec.spec --noconfirm

if %errorlevel% neq 0 (
    echo.
    echo ERROR: PyInstaller build failed. Check output above.
    pause & exit /b 1
)

echo.
echo ============================================================
echo  BUILD COMPLETE
echo  Output: %~dp0dist\QytCroRec.exe
echo ============================================================
pause
