@echo off
chcp 65001 >nul
title Login Taobao
cd /d "%~dp0"
set "PY=C:\Users\hao\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set PYTHONIOENCODING=utf-8

echo.
echo ==================================================
echo   STEP 1 : login your Taobao account
echo ==================================================
echo.
echo A browser window will open.
echo Scan the QR code with your Taobao App.
echo The script saves your login automatically.
echo.

"%PY%" scripts\login_auto.py --platform taobao

echo.
echo ==================================================
echo   If it says LOGIN OK, close this window and run
echo   2-collect-item.bat
echo ==================================================
pause
