@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM For a larger internal drive, change the value below and move old downloads first:
REM set "JABLE_DOWNLOAD_DIR=D:\JableDownloads"
REM Default is password-free LAN access; for local-only use:
REM set "JABLE_HOST=127.0.0.1"
set "JABLE_HOST=0.0.0.0"
set "JABLE_PORT=6003"
if not exist "jable\Scripts\python.exe" (
  echo Please run INIT.bat first.
  pause
  exit /b 1
)
"jable\Scripts\python.exe" app.py
pause
