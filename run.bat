@echo off
cd /d "%~dp0"
if not exist .env (
    copy .env.example .env
    echo Remplissez .env puis relancez.
    pause
    exit /b 1
)
py -3 -m pip install -r requirements.txt -q
set LOG_TO_FILE=false
py -3 bot.py
