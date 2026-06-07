@echo off
cd /d "%~dp0"
if not exist .env (
    echo Copiez .env.example vers .env et remplissez vos identifiants.
    pause
    exit /b 1
)
python -m pip install -r requirements.txt
py -3 bot.py
pause
