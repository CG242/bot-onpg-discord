@echo off
cd /d "%~dp0"
echo === Setup base MySQL locale (WAMP) ===
echo.

if not exist .env (
    copy .env.example .env
    echo Fichier .env cree depuis .env.example
    echo Remplissez DISCORD_TOKEN et GUILD_ID avant de lancer le bot.
    echo.
)

py -3 -m pip install -r requirements.txt -q
py -3 setup_local_db.py
echo.
pause
