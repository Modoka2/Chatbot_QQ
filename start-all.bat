@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting chatbot services...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-all.ps1"
if errorlevel 1 pause
