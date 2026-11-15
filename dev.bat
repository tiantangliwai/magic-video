@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

rem Local debug launcher: run from SOURCE, never from the installed exe.
rem Usage: dev.bat [--real] [--nodevtools]
rem   default      -> safe copy of the real database, DevTools enabled
rem   --real       -> use %APPDATA%\Magic Video directly (writes real history)
rem   --nodevtools -> do not open the DevTools window

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [dev] .venv missing. Run these once:
  echo       python -m venv .venv
  echo       .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

set "USE_REAL=0"
set "DEBUG_FLAG=--debug"
if /I "%~1"=="--real" set "USE_REAL=1"
if /I "%~2"=="--real" set "USE_REAL=1"
if /I "%~1"=="--nodevtools" set "DEBUG_FLAG="
if /I "%~2"=="--nodevtools" set "DEBUG_FLAG="

if "%USE_REAL%"=="1" (
  set "MAGIC_VIDEO_DATA_DIR=%APPDATA%\Magic Video"
  echo [dev] DATA = real db: %APPDATA%\Magic Video\library.db
  echo [dev] WARNING: debug actions WILL write into your real watch history
) else (
  "%PY%" dev.py
  set "MAGIC_VIDEO_DATA_DIR=%CD%\data\dev"
  echo [dev] DATA = safe copy: data\dev\library.db
)

echo [dev] Frontend change  -^> press Ctrl+R in the window.
echo [dev] Python change    -^> close window and run dev.bat again.
echo [dev] --debug opens DevTools ^(F12 / right-click Inspect^).
echo.
"%PY%" run.py %DEBUG_FLAG%
if errorlevel 1 pause
