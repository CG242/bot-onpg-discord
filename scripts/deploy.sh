#!/usr/bin/env bash
# Déploiement Linux / VPS — https://github.com/CG242/bot-onpg-discord
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Bot ONPG Discord — déploiement ==="

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Créez .env (DISCORD_TOKEN, GUILD_ID, MySQL…) puis relancez."
  exit 1
fi

python3 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "Attente MySQL…"
for i in $(seq 1 30); do
  if python3 scripts/setup_db.py; then
    break
  fi
  sleep 2
done

echo "Lancement du bot (systemd recommandé en prod — voir README)"
exec python3 bot.py
