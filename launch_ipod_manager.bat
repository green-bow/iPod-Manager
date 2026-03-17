@echo off
title iPod Shuffle Manager
cd /d "%~dp0"
echo Starting iPod Shuffle Manager...
echo.
python ipod_manager.py E:\
if errorlevel 1 (
    echo.
    echo Error: Could not start the manager.
    echo Make sure Python is installed and the iPod is connected as drive E:\
    pause
)
