from dataclasses import dataclass
from typing import Any

import discord

from database import Database


@dataclass
class PlayerResolveResult:
    found: bool
    player: dict[str, Any] | None = None
    label: str = ""
    discord_id: int | None = None
    is_member: bool = False
    search_text: str = ""


def resolve_player_input(
    db: Database,
    target,
    season_id: int | None = None,
) -> PlayerResolveResult:
    """
    CAS 1 : Member Discord → discord_id
    CAS 2 : Texte → recherche en base (pseudo des scores)
    CAS 3 : Introuvable → found=False (proposer création ou liaison)
    """
    if isinstance(target, (discord.Member, discord.User)):
        player = db.get_player_by_discord_id(target.id)
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
    player = db.find_player_by_name(text, season_id)
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
    return db.get_or_create_player(text, discord_id)


def link_player_to_discord(
    db: Database, player_id: int, discord_id: int, force: bool = False
) -> bool:
    if force:
        return db.link_discord_id_force(player_id, discord_id)
    db.link_discord_id(player_id, discord_id)
    return True
