import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "ft_championship")
START_DATE = datetime.strptime(
    os.getenv("START_DATE", "2026-01-01"), "%Y-%m-%d"
).replace(tzinfo=timezone.utc)
SCORES_CHANNEL = os.getenv("SCORES_CHANNEL", "Scores-ft-congo")
SCORES_CHANNEL_BZ = os.getenv("SCORES_CHANNEL_BZ", "").strip()
SCORES_CHANNEL_PN = os.getenv("SCORES_CHANNEL_PN", "").strip()
LEADERBOARD_CHANNEL = os.getenv("LEADERBOARD_CHANNEL", "classement")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0") or "0")

FT_TYPES = (2, 3, 5, 7, 10)
ACTIVE_DAYS = int(os.getenv("ACTIVE_DAYS", "14"))
AUTO_SYNC_SECONDS = int(os.getenv("AUTO_SYNC_SECONDS", "180"))

# Hiérarchie révisée (Phase 1 Tâche 4)
VALID_REGIONS = ("BZ", "PN")
VALID_TIERS = ("S+", "S", "A+", "A", "B+", "B", "C", "NR")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "false").lower() in ("1", "true", "yes")
LOG_FILE = os.getenv("LOG_FILE", "bot.log")


def score_channels() -> list[tuple[str | None, str]]:
    """
    Salons de scores : (région ou None, nom du salon).
    BZ/PN en priorité ; SCORES_CHANNEL = salon générique sans région auto.
    """
    channels: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    for region, name in (
        ("BZ", SCORES_CHANNEL_BZ),
        ("PN", SCORES_CHANNEL_PN),
        (None, SCORES_CHANNEL),
    ):
        if not name:
            continue
        key = name.lower().replace("#", "")
        if key in seen:
            continue
        seen.add(key)
        channels.append((region, name))
    return channels


def official_region_title(region: str) -> str:
    return f"CLASSEMENT OFFICIEL — {region.upper()}"
