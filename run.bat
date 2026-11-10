@echo off
chcp 65001 >nul
cd /d "%~dp0"
set CREATED=0
if not exist ".env" set CREATED=1
if not exist ".env" copy ".env.example" ".env" >nul
if "%CREATED%"=="1" echo [i] .env created. Please fill in your API key, then run again.
if "%CREATED%"=="1" echo [i] Try without a key: python main.py --mock
python main.py %*
if errorlevel 1 pause
