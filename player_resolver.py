from dataclasses import dataclass, field
from typing import Any

import discord

from database import Database
from player_identity import find_existing_player, resolve_or_create_player, search_players_by_query


@dataclass
class PlayerResolveResult:
    found: bool
    player: dict[str, Any] | None = None
    label: str = ""
    discord_id: int | None = None
    is_member: bool = False
    search_text: str = ""
    ambiguous: bool = False
    candidates: list[dict[str, Any]] = field(default_factory=list)


def format_ambiguous_players(db: Database, candidates: list[dict[str, Any]]) -> str:
    lines = ["Plusieurs joueurs correspondent. Utilisez le **pseudo exact** :"]
    for p in candidates[:15]:
        reg = (p.get("region") or "—").upper()
        lines.append(f"• **{db.player_display_name(p)}** ({reg})")
    if len(candidates) > 15:
        lines.append(f"… et {len(candidates) - 15} autre(s).")
    return "\n".join(lines)


def resolve_player_input(
    db: Database,
    target,
    season_id: int | None = None,
) -> PlayerResolveResult:
    if isinstance(target, (discord.Member, discord.User)):
        player = db.get_player_by_discord_id(target.id)
        if not player:
            player = find_existing_player(
                db, getattr(target, "display_name", str(target)), target.id
            )
        if player:
            return PlayerResolveResult(
                found=True,
                player=player,
                label=db.player_display_name(player),
                discord_id=target.id,
                is_member=True,
            )
        return PlayerResolveResult(
            found=False,
            label=getattr(target, "display_name", str(target)),
            discord_id=target.id,
            is_member=True,
            search_text=getattr(target, "display_name", ""),
        )

    text = str(target).strip()
    from parser import normalize_key

    exact = db.get_player_by_normalized_name(normalize_key(text))
    if exact:
        return PlayerResolveResult(
            found=True,
            player=exact,
            label=db.player_display_name(exact),
            discord_id=exact.get("discord_id"),
            is_member=False,
            search_text=text,
        )

    candidates = search_players_by_query(db, text, season_id)
    if len(candidates) == 1:
        p = candidates[0]
        return PlayerResolveResult(
            found=True,
            player=p,
            label=db.player_display_name(p),
            discord_id=p.get("discord_id"),
            is_member=False,
            search_text=text,
        )
    if len(candidates) > 1:
        return PlayerResolveResult(
            found=False,
            label=text,
            is_member=False,
            search_text=text,
            ambiguous=True,
            candidates=candidates,
        )

    player = find_existing_player(db, text)
    if player:
        return PlayerResolveResult(
            found=True,
            player=player,
            label=db.player_display_name(player),
            discord_id=player.get("discord_id"),
            is_member=False,
            search_text=text,
        )

    return PlayerResolveResult(
        found=False,
        label=text,
        is_member=False,
        search_text=text,
    )


def get_or_create_from_text(db: Database, text: str, discord_id: int | None = None):
    return resolve_or_create_player(db, text, discord_id)
