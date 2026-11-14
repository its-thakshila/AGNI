@echo off
rem Double-click this file, or run it from PowerShell (.\train_all.bat) or Command Prompt (train_all.bat).
rem Options are passed on, e.g.:  train_all.bat --skip-test
cd /d "%~dp0"
title MODIS to VIIRS training
where python >nul 2>nul
if %errorlevel%==0 (set "PY=python") else (set "PY=py")
%PY% train_all.py %*
echo.
echo Press any key to close this window.
pause >nul
