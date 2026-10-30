@echo off
chcp 65001 >nul
title Check login status
cd /d "%~dp0"
set "PY=C:\Users\hao\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set PYTHONIOENCODING=utf-8

echo.
echo ==================================================
echo   Check whether you are logged in
echo ==================================================
echo.

"%PY%" scripts\check_login.py

echo.
pause
