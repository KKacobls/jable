@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "jable\Scripts\python.exe" ( echo Please run INIT.bat first. & pause & exit /b 1 )
"jable\Scripts\python.exe" -m unittest -v tests.test_server
pause
