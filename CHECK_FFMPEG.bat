@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Checking FFmpeg...
where ffmpeg
where ffprobe
ffmpeg -version
ffprobe -version
if errorlevel 1 (
  echo.
  echo Install: winget install --id Gyan.FFmpeg --exact
  echo Then CLOSE and REOPEN PowerShell before running python app.py
  echo If installed manually, set FFMPEG_BIN_DIR to the directory containing ffmpeg.exe and ffprobe.exe
)
pause
