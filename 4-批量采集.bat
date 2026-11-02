@echo off
chcp 65001 >nul
title Batch collect
cd /d "%~dp0"
set "PY=C:\Users\hao\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set PYTHONIOENCODING=utf-8

echo.
echo ==================================================
echo   STEP 4 : batch collect many items
echo ==================================================
echo.
echo Paste product URLs or IDs, ONE per line.
echo When finished, type  END  and press Enter.
echo.
echo   Example:
echo     https://item.taobao.com/item.htm?id=123456789
echo     987654321
echo.

if exist links.txt del links.txt

:loop
set "LINE="
set /p LINE=Link or END: 
if /i "%LINE%"=="END" goto run
if "%LINE%"=="" goto loop
echo %LINE%>>links.txt
goto loop

:run
if not exist links.txt (
    echo Nothing to collect, exit.
    pause
    exit /b 1
)

echo.
echo Collected links saved to links.txt
echo Starting... (3 seconds between items to avoid risk control)
echo.

"%PY%" run_demo.py --platform taobao --batch links.txt --skip-existing --delay 3 --with-detail

echo.
echo Exporting summary CSV ...
"%PY%" scripts\export_csv.py

echo.
echo ==================================================
echo   Done. Summary CSV is in data folder.
echo   Images: data\media\^<item_id^>\
echo ==================================================
pause
