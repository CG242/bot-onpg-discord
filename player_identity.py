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


def find_existing_player(db, name: str, discord_id: int | None = None) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Priorité : Discord ID → clé exacte → similarité textuelle.
    Retourne un joueur unique, une liste de candidats si ambigü, ou None."""
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
    scored_candidates: list[tuple[float, dict[str, Any]]] = []
    best_score = 0.0

    for player in candidates:
        player_key = player.get("normalized_name") or normalize_key(
            player.get("name", "")
        )
        score = similarity(key, player_key)
        if score > 0:
            scored_candidates.append((score, player))
        if score > best_score:
            best_score = score

    # Sort by score descending
    scored_candidates.sort(key=lambda x: x[0], reverse=True)

    # If there's a clear best match (significantly higher score), return it
    if scored_candidates:
        best = scored_candidates[0]
        if best[0] >= config.SIMILARITY_THRESHOLD:
            # Check if there are other candidates with similar scores (ambiguous)
            similar_candidates = [
                c for s, c in scored_candidates 
                if s >= config.SIMILARITY_THRESHOLD and abs(s - best[0]) < 0.1
            ]
            if len(similar_candidates) > 1:
                # Return list for disambiguation
                return similar_candidates
            return best[1]

    if len(key) >= 4:
        substring_matches = []
        for player in candidates:
            player_key = player.get("normalized_name") or ""
            if key in player_key or player_key in key:
                sub_score = min(len(key), len(player_key)) / max(
                    len(key), len(player_key)
                )
                if sub_score >= 0.75:
                    substring_matches.append(player)
        if len(substring_matches) == 1:
            return substring_matches[0]
        elif len(substring_matches) > 1:
            return substring_matches

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
    
    # Handle ambiguous matches (list of candidates)
    if isinstance(existing, list):
        # If there are multiple candidates, we need user disambiguation
        # For now, pick the first one but log a warning
        # In a future enhancement, this should return the list for UI disambiguation
        logger.warning(
            "Ambiguous player match for '%s': %d candidates. Using first match.",
            clean_name,
            len(existing),
        )
        existing = existing[0]
    
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
