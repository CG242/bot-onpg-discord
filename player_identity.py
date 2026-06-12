"""Résolution intelligente d'identité joueur — déduplication automatique."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

import config
from parser import normalize_key, pick_display_name, sanitize_player_name


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def find_existing_player(db, name: str, discord_id: int | None = None) -> dict[str, Any] | None:
    """Priorité : Discord ID → clé exacte → similarité textuelle."""
    if discord_id:
        by_discord = db.get_player_by_discord_id(discord_id)
        if by_discord:
            return by_discord

    key = normalize_key(name)
    if not key:
        return None

    exact = db.get_player_by_normalized_name(key)
    if exact:
        return exact

    candidates = db.list_all_players()
    best: dict[str, Any] | None = None
    best_score = 0.0

    for player in candidates:
        player_key = player.get("normalized_name") or normalize_key(
            player.get("name", "")
        )
        score = similarity(key, player_key)
        if score > best_score:
            best_score = score
            best = player

    if best and best_score >= config.SIMILARITY_THRESHOLD:
        return best

    if len(key) >= 4:
        for player in candidates:
            player_key = player.get("normalized_name") or ""
            if key in player_key or player_key in key:
                sub_score = min(len(key), len(player_key)) / max(
                    len(key), len(player_key)
                )
                if sub_score >= 0.75:
                    return player

    return None


def resolve_or_create_player(
    db, name: str, discord_id: int | None = None
) -> dict[str, Any]:
    clean_name = sanitize_player_name(name) or str(name).strip()[:64]
    key = normalize_key(clean_name)

    # Check if this name was previously merged into another player (alias resolution)
    alias_target = db.resolve_player_alias(name)
    if alias_target:
        logger.info(
            "Nom '%s' correspond à un alias précédent, utilisation du joueur %s (id=%s)",
            name,
            alias_target.get("name", ""),
            alias_target["id"],
        )
        # Update the existing player with the new display name if needed
        display = pick_display_name(alias_target.get("name", ""), clean_name)
        updates: dict[str, Any] = {}
        if display != alias_target.get("name"):
            updates["name"] = display
        if discord_id and not alias_target.get("discord_id"):
            db.link_discord_id(alias_target["id"], discord_id)
            alias_target["discord_id"] = discord_id
        if updates:
            db.update_player_fields(alias_target["id"], **updates)
            alias_target.update(updates)
        return alias_target

    existing = find_existing_player(db, clean_name, discord_id)
    if existing:
        display = pick_display_name(existing.get("name", ""), clean_name)
        updates: dict[str, Any] = {}
        if display != existing.get("name"):
            updates["name"] = display
        if key and key != existing.get("normalized_name"):
            updates["normalized_name"] = key
        if discord_id and not existing.get("discord_id"):
            db.link_discord_id(existing["id"], discord_id)
            existing["discord_id"] = discord_id
        if updates:
            db.update_player_fields(existing["id"], **updates)
            existing.update(updates)
        return existing

    return db.create_player(clean_name, key, discord_id)


def search_players_by_query(
    db, query: str, season_id: int | None = None
) -> list[dict[str, Any]]:
    """Recherche partielle : leo, mk, david… si un seul résultat → match auto."""
    raw = (query or "").strip().lower()
    key = normalize_key(query)
    if len(raw) < 2 and len(key) < 2:
        return []

    if season_id:
        pool = db.list_season_players(season_id)
        if not pool:
            pool = db.list_all_players()
    else:
        pool = db.list_all_players()

    matches: list[dict[str, Any]] = []
    seen: set[int] = set()
    for player in pool:
        pid = player["id"]
        if pid in seen:
            continue
        name_lower = (player.get("name") or "").lower()
        norm = (
            player.get("normalized_name") or normalize_key(player.get("name", ""))
        ).lower()
        if raw in name_lower or (key and len(key) >= 2 and key in norm):
            matches.append(player)
            seen.add(pid)
    return matches
