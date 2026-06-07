# Bot Discord — FT Championship ONPG

Bot Discord pour le championnat FT : scores automatiques, classements BZ/PN, stats, compare, rangs et régions.

Dépôt : [github.com/CG242/bot-onpg-discord](https://github.com/CG242/bot-onpg-discord)

## Prérequis

- Python 3.12+
- MySQL 8 (local WAMP ou Docker)
- Token bot Discord avec **Message Content Intent** et **Server Members Intent**

## Configuration

```bash
cp .env.example .env
```

Variables obligatoires :

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Token du bot |
| `GUILD_ID` | ID du serveur Discord |
| `MYSQL_*` | Connexion base de données |
| `SCORES_CHANNEL` | Salon des scores |
| `LEADERBOARD_CHANNEL` | Salon classement live |

## Installation locale (Windows)

```bat
setup.bat
run.bat
```

## Docker (recommandé prod)

```bash
cp .env.example .env
# Éditez .env (DISCORD_TOKEN, GUILD_ID, MYSQL_PASSWORD…)

docker compose up -d --build
docker compose logs -f bot
```

MySQL + bot démarrent ensemble. La base et les tables sont créées automatiquement.

## VPS Linux (sans Docker)

```bash
git clone https://github.com/CG242/bot-onpg-discord.git
cd bot-onpg-discord
cp .env.example .env
nano .env

chmod +x scripts/deploy.sh run.sh
./scripts/deploy.sh
```

Service systemd :

```bash
sudo cp scripts/bot-onpg.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bot-onpg
sudo journalctl -u bot-onpg -f
```

## Base de données

```bash
python scripts/setup_db.py
```

Crée la base `ft_championship` et toutes les tables (migrations incluses).

## Commandes principales

- `/classement` — classements officiels BZ + PN
- `/stats`, `/compare` — fiches joueurs
- `/region-definir`, `/set-rang` — admin
- `/aide` — guide complet

## Logs

Par défaut **console uniquement** (`LOG_TO_FILE=false`) — pas de fichier `bot.log` en prod.

Debug local optionnel : `LOG_TO_FILE=true` dans `.env`

## Sécurité

- Ne jamais committer `.env`
- Régénérez le token Discord si exposé
- `ADMIN_ROLE_ID` optionnel pour les commandes admin
