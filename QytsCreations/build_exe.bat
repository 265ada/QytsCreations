@echo off
REM ─────────────────────────────────────────────────────────────────────
REM  Build QytCroRec.exe — single-file Windows executable
REM
REM  Output:  dist\QytCroRec.exe   (~40-50 MB, includes Python + PyQt6)
REM
REM  First time:  pip install pyinstaller
REM  Then:        build_exe.bat
REM ─────────────────────────────────────────────────────────────────────

setlocal

pushd "%~dp0"

REM Ensure PyInstaller is installed
where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo PyInstaller not found, installing...
    pip install pyinstaller
    if errorlevel 1 (
        echo ERROR: pip install pyinstaller failed
        popd
        exit /b 1
    )
)

REM Clean previous build artifacts
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist QytCroRec.spec del QytCroRec.spec

echo === Building QytCroRec.exe ===
pyinstaller ^
    --onefile ^
    --windowed ^
    --name QytCroRec ^
    --add-data "hooks\dinput_hook\dinput_hook_x64.dll;hooks\dinput_hook" ^
    --add-data "hooks\dinput_hook\dinput_hook_x86.dll;hooks\dinput_hook" ^
    --add-data "hooks\dinput_hook\injector_x64.exe;hooks\dinput_hook" ^
    --add-data "hooks\dinput_hook\injector_x86.exe;hooks\dinput_hook" ^
    --add-data "hooks\dinput_hook\README.md;hooks\dinput_hook" ^
    --hidden-import pynput ^
    --hidden-import PyQt6 ^
    QytCroRec.py

if errorlevel 1 (
    echo BUILD FAILED
    popd
    exit /b 2
)

echo.
echo === Build complete ===
dir dist\QytCroRec.exe
echo.
echo Distribute the single dist\QytCroRec.exe — it bundles Python, PyQt6,
echo pynput, and the hook DLLs.  No prerequisites needed on target machine.
echo.
popd
endlocal
