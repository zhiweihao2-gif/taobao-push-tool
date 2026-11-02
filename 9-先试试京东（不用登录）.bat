@echo off
chcp 65001 >nul
title Try JD (no login needed)
cd /d "%~dp0"
set "PY=C:\Users\hao\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set PYTHONIOENCODING=utf-8

echo.
echo ==================================================
echo   STEP 0 : try it on a JD product (no login)
echo ==================================================
echo.
echo Fetching one JD product, about 15 seconds...
echo.

"%PY%" run_demo.py --platform jd --id 100008348542 --with-detail

echo.
echo ==================================================
echo   Result files:
echo     data\items\100008348542.json
echo     data\media\100008348542\
echo ==================================================
echo.
echo If you see the files above, the tool works.
echo Next: run  1-login-taobao.bat
echo.
pause
