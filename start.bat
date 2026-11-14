@echo off
chcp 65001 >nul
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw.exe run.py
) else (
  start "" python run.py
)
