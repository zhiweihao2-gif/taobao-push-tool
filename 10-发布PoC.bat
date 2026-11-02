@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY=C:\Users\hao\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "PYTHONIOENCODING=utf-8"

if not exist "%PY%" (
    echo [XX] Python not found:
    echo      %PY%
    pause
    exit /b 2
)

:MENU
cls
echo ==============================================================
echo   Taobao Publish PoC  --  CDP attaches to YOUR browser
echo ==============================================================
echo.
echo   1. Start browser with debug port 9222   ^(first step^)
echo   2. Check login status
echo   3. Probe publish page structure
echo   4. Fill form from collected item  ^(no submit^)
echo   0. Exit
echo.
echo   Prereq: browser must be running with --remote-debugging-port=9222
echo ==============================================================
set "CHOICE="
set /p "CHOICE=Select [0-4]: "

if "%CHOICE%"=="1" goto START
if "%CHOICE%"=="2" goto CHECK
if "%CHOICE%"=="3" goto PROBE
if "%CHOICE%"=="4" goto FILL
if "%CHOICE%"=="0" exit /b 0
goto MENU

:START
cls
"%PY%" scripts\start_chrome_debug.py
echo.
pause
goto MENU

:CHECK
cls
"%PY%" scripts\publish_poc.py check
echo.
pause
goto MENU

:PROBE
cls
echo Enter target category id ^(e.g. 50023905^), leave empty to probe bare page:
set "CID="
set /p "CID=catId: "
if "%CID%"=="" (
    "%PY%" scripts\publish_poc.py probe
) else (
    "%PY%" scripts\publish_poc.py probe --cid %CID%
)
echo.
pause
goto MENU

:FILL
cls
echo Enter collected item id ^(e.g. 1073025553052^):
set "ITEM="
set /p "ITEM=item: "
if "%ITEM%"=="" goto MENU
echo Enter target category id:
set "CID="
set /p "CID=catId: "
if "%CID%"=="" goto MENU
"%PY%" scripts\publish_poc.py fill --item %ITEM% --cid %CID%
echo.
pause
goto MENU
