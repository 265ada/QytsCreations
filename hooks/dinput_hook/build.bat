@echo off
REM Build dinput_hook.dll + injector.exe
REM
REM Prerequisites:
REM   1. Open a "x64 Native Tools Command Prompt for VS" (or x86 if your
REM      target game is 32-bit — bitness MUST match the target).
REM   2. Clone Microsoft Detours:
REM        git clone https://github.com/microsoft/Detours C:\src\Detours
REM   3. cd C:\src\Detours
REM   4. nmake                          (this produces lib\<arch>\detours.lib)
REM   5. set DETOURS=C:\src\Detours
REM
REM Then in this folder:   build.bat

setlocal

if "%DETOURS%"=="" (
    echo ERROR: Set DETOURS to your local Detours clone, e.g.:
    echo    set DETOURS=C:\src\Detours
    exit /b 1
)

REM Pick architecture from the active VS prompt
if "%VSCMD_ARG_TGT_ARCH%"=="" set VSCMD_ARG_TGT_ARCH=x64
set ARCH=%VSCMD_ARG_TGT_ARCH%
echo Building for %ARCH%
set DLIB=%DETOURS%\lib.%ARCH%\detours.lib
set DINC=%DETOURS%\include

if not exist "%DLIB%" (
    echo ERROR: %DLIB% not found.  Run `nmake` in %DETOURS% first.
    exit /b 2
)

REM ── dinput_hook.dll ───────────────────────────────────────────────────
cl /LD /EHsc /MT /O2 /nologo ^
   /I "%DINC%" ^
   dinput_hook.cpp ^
   "%DLIB%" dinput8.lib dxguid.lib user32.lib kernel32.lib ^
   /link /OUT:dinput_hook.dll
if errorlevel 1 (echo DLL build failed & exit /b 3)

REM ── injector.exe ──────────────────────────────────────────────────────
cl /EHsc /MT /O2 /nologo injector.cpp /link /OUT:injector.exe
if errorlevel 1 (echo injector build failed & exit /b 4)

echo.
echo === Build complete ===
echo   dinput_hook.dll  — inject this into the target process
echo   injector.exe     — usage: injector.exe ^<pid^> ^<full_path_to_dll^>
echo.
endlocal
