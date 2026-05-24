@echo off
title Macro Recorder
echo Checking dependencies...
pip install -q PyQt6 pynput
echo Starting Macro Recorder...
python "%~dp0macro_recorder.py"
if %ERRORLEVEL% neq 0 (
    echo.
    echo Error starting app. Make sure Python is installed.
    pause
)
