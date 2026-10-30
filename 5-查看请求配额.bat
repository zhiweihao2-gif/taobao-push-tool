@echo off
chcp 65001 >nul
title Rate limit status
cd /d "%~dp0"
set "PY=C:\Users\hao\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set PYTHONIOENCODING=utf-8

echo.
echo ==================================================
echo   Request quota and cooldown status
echo ==================================================

"%PY%" scripts\rate_status.py

echo.
echo ==================================================
echo   If a platform shows COOLDOWN, just wait.
echo   Do NOT retry - it will only make it worse.
echo ==================================================
pause
