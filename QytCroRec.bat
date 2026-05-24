@echo off
title QytCroRec
echo Checking dependencies...
pip install -q PyQt6 pynput
echo Starting QytCroRec...
python "%~dp0QytCroRec.py"
if %ERRORLEVEL% neq 0 (
    echo.
    echo Error starting app. Make sure Python is installed.
    pause
)
