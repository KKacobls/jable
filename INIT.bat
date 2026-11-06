@echo off
REM Based on hcjohn463/JableTVDownload (Apache-2.0); modified setup for the Windows server variant.
chcp 65001 >nul
cd /d "%~dp0"
if not exist "jable\Scripts\python.exe" (
    where py >nul 2>&1
    if errorlevel 1 ( python -m venv jable ) else ( py -3 -m venv jable )
    if errorlevel 1 ( echo Failed to create virtual environment & pause & exit /b 1 )
)
"jable\Scripts\python.exe" -m pip install --upgrade pip
"jable\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 ( echo Python dependencies failed & pause & exit /b 1 )
where ffmpeg >nul 2>&1
if errorlevel 1 ( echo [WARNING] ffmpeg not found on PATH. Install FFmpeg and ffprobe before downloading. ) else ( echo FFmpeg detected. )
echo Setup complete. Run RUN_SERVER.bat
pause
