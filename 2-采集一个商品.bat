@echo off
chcp 65001 >nul
title Collect one item
cd /d "%~dp0"
set "PY=C:\Users\hao\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set PYTHONIOENCODING=utf-8

echo.
echo ==================================================
echo   STEP 2 : collect one Taobao item
echo ==================================================
echo.
echo Paste the product URL or the numeric item ID.
echo.
echo   OK : https://item.taobao.com/item.htm?id=123456789
echo   OK : 123456789
echo   NO : click.simba.taobao.com/...   (ad link, no item id)
echo.
set /p LINK=Paste here: 

if "%LINK%"=="" (
    echo No URL given, exit.
    pause
    exit /b 1
)

echo.
echo Collecting + rendering detail page (30-60 seconds)...
echo.

"%PY%" run_demo.py --platform taobao --id "%LINK%" --with-detail --render-desc

echo.
echo ==================================================
echo   Done. Files are in data\items and data\media
echo ==================================================
pause
