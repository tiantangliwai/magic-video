@echo off
chcp 65001 >nul
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw.exe daemon.py
) else (
  start "" python daemon.py
)
