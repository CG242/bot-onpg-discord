@echo off
cd /d "%~dp0"
if not exist .env copy .env.example .env
py -3 -m pip install -r requirements.txt -q
py -3 scripts\setup_db.py
pause
