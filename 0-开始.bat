@echo off
chcp 65001 >nul
title Taobao Batch Workbench
cd /d "%~dp0"
set "PY=C:\Users\hao\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set PYTHONIOENCODING=utf-8

if not exist "%PY%" (
    echo.
    echo ERROR: python not found at
    echo   %PY%
    echo.
    pause
    exit /b 1
)

"%PY%" scripts\start_all.py

echo.
pause
