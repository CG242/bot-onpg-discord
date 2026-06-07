from datetime import datetime, timezone
from typing import Any

import config
from database import Database
from ranking import format_tier, win_ratio

FT_LABELS = (2, 3, 5, 7, 10)


def _fmt_date(dt) -> str:
    if not dt:
        return "—"
    if isinstance(dt, datetime):
        return dt.strftime("%d/%m/%Y")
    return str(dt)


def _region_label(region: str | None) -> str:
    return region.upper() if region else "—"



def _match_result_label(won: bool) -> str:
    return "Gagné" if won else "Perdu"


def format_leaderboard(
    db: Database,
    season_id: int,
    *,
    region: str | None = None,
    title: str = "CLASSEMENT OFFICIEL",
    date_debut=None,
    date_fin=None,
) -> str:
    debut = date_debut if date_debut else config.START_DATE
    display_fin = date_fin if date_fin else datetime.now(timezone.utc)

    rows = db.get_leaderboard(
        season_id,
        region=region,
        active_only=False,
        date_debut=debut,
        date_fin=date_fin,
    )

    lines = [
        f"**{title}**",
        "",
        f"Période : {_fmt_date(debut)} → {_fmt_date(display_fin)}",
    ]
    if region:
        lines.append(f"Région : {region}")
    lines.append("Basé sur les confrontations enregistrées")
    lines.append("")

    if not rows:
        lines.append("Aucune confrontation sur cette période.")
        return "\n".join(lines)

    for pos, row in enumerate(rows, start=1):
        name = row.get("display_name") or row["name"]
        tier = format_tier(row.get("tier_rank"))
        reg = _region_label(row.get("region"))
        points = int(row.get("elo") or 0)
        wins = int(row["ft_wins"] or 0)
        losses = int(row["ft_losses"] or 0)
        ratio = win_ratio(wins, losses)

        lines.append(f"**TOP {pos} — {name}**")
        lines.append(f"Rang : {tier}")
        if not region:
            lines.append(f"Région : {reg}")
        lines.append(f"Points : {points}")
        lines.append(f"FT gagnés : {wins}")
        lines.append(f"Défaites : {losses}")
        lines.append(f"Ratio : {ratio:.0f}%")
        lines.append("")

    lines.append(f"{len(rows)} joueur(s) classés")
    return "\n".join(lines).strip()


def format_live_leaderboard_blocks(db: Database, season_id: int) -> list[str]:
    """Classements officiels par ville : BZ et PN uniquement."""
    return [
        format_leaderboard(
            db,
            season_id,
            region=region,
            title=config.official_region_title(region),
        )
        for region in config.VALID_REGIONS
    ]


def format_player_stats(stats: dict[str, Any] | None, player_name: str = "") -> str:
    if not stats:
        return f"Aucune statistique pour {player_name or 'ce joueur'}."

    name = stats.get("display_name") or stats.get("name") or player_name
    tier = format_tier(stats.get("tier_rank"))
    reg = _region_label(stats.get("region"))
    points = int(stats.get("elo") or 0)
    wins = int(stats["ft_wins"] or 0)
    losses = int(stats["ft_losses"] or 0)
    ratio = win_ratio(wins, losses)
    matches = stats.get("matches") or []

    lines = [
        f"**STATS — {name}**",
        "",
        f"Rang : {tier}",
        f"Région : {reg}",
        f"Points : {points}",
        "",
        "**GLOBAL**",
        f"FT gagnés : {wins}",
        f"Défaites : {losses}",
        f"Taux de victoire : {ratio:.0f}%",
        "",
        "**DETAIL PAR FT**",
    ]

    ft_details = []
    for ft in FT_LABELS:
        ft_matches = [m for m in matches if m["ft_type"] == ft]
        if not ft_matches:
            continue
        ft_w = sum(1 for m in ft_matches if m["won"])
        ft_l = sum(1 for m in ft_matches if not m["won"])
        ft_details.append(f"FT{ft} : {ft_w} FT gagnés / {ft_l} défaite")

    lines.extend(ft_details if ft_details else ["Aucun match enregistré."])
    lines.append("")
    lines.append("**MATCH HISTORY**")

    history_blocks = []
    for ft in FT_LABELS:
        ft_matches = [m for m in matches if m["ft_type"] == ft]
        if not ft_matches:
            continue
        block = [f"--- FT{ft} ---"]
        for m in ft_matches:
            result = _match_result_label(m["won"])
            opp = m.get("opponent_display", "?")
            date_str = _fmt_date(m.get("created_at"))
            block.append(
                f"{name} {m['my_score']}-{m['opp_score']} {opp} → {result} ({date_str})"
            )
        history_blocks.extend(block)
        history_blocks.append("")

    if history_blocks:
        lines.extend(history_blocks)
    else:
        lines.append("Aucun historique.")

    return "\n".join(lines).strip()


def _name_with_tier(name: str, tier: str) -> str:
    return f"{name}({tier})"


def _ft_head_to_head(
    matches: list[dict[str, Any]],
    player_a: dict[str, Any],
    player_b: dict[str, Any],
    label_a: str,
    label_b: str,
) -> list[str]:
    lines = []
    for ft in FT_LABELS:
        ft_matches = [m for m in matches if m["ft_type"] == ft]
        if not ft_matches:
            continue
        w_a = sum(1 for m in ft_matches if m["winner_id"] == player_a["id"])
        w_b = sum(1 for m in ft_matches if m["winner_id"] == player_b["id"])
        lines.append(f"FT{ft} : {label_a} {w_a} — {w_b} {label_b}")
    return lines


def format_comparison(
    player_a: dict[str, Any],
    player_b: dict[str, Any],
    matches: list[dict[str, Any]],
    db: Database,
) -> str:
    name_a = db.player_display_name(player_a)
    name_b = db.player_display_name(player_b)
    tier_a = format_tier(player_a.get("tier_rank"))
    tier_b = format_tier(player_b.get("tier_rank"))
    label_a = _name_with_tier(name_a, tier_a)
    label_b = _name_with_tier(name_b, tier_b)

    lines = [f"**COMPARAISON — {label_a} vs {label_b}**", ""]

    if not matches:
        lines.append("Aucune confrontation enregistrée.")
        return "\n".join(lines)

    wins_a = sum(1 for m in matches if m["winner_id"] == player_a["id"])
    wins_b = sum(1 for m in matches if m["winner_id"] == player_b["id"])

    lines.extend([
        f"Confrontations : {len(matches)}",
        f"FT gagnés : {label_a} {wins_a} — {wins_b} {label_b}",
        "",
        "**DETAIL PAR FT**",
    ])

    ft_lines = _ft_head_to_head(matches, player_a, player_b, label_a, label_b)
    lines.extend(ft_lines if ft_lines else ["Aucun détail par FT."])
    lines.append("")
    lines.append("**HISTORIQUE**")

    for m in matches:
        date_str = _fmt_date(m.get("created_at"))
        if m["player1_id"] == player_a["id"]:
            p1 = label_a
        elif m["player1_id"] == player_b["id"]:
            p1 = label_b
        else:
            p1 = m.get("player1_display") or m["player1_name"]

        if m["player2_id"] == player_a["id"]:
            p2 = label_a
        elif m["player2_id"] == player_b["id"]:
            p2 = label_b
        else:
            p2 = m.get("player2_display") or m["player2_name"]

        if m["winner_id"] == player_a["id"]:
            winner = label_a
        elif m["winner_id"] == player_b["id"]:
            winner = label_b
        else:
            winner = m.get("winner_display") or m["winner_name"]

        lines.append(
            f"{p1} {m['score1']}-{m['score2']} {p2} → {winner} a Gagné ({date_str})"
        )

    return "\n".join(lines).strip()


def resolve_player(db: Database, joueur, season_id: int | None = None):
    from player_resolver import resolve_player_input

    result = resolve_player_input(db, joueur, season_id)
    return result.player if result.found else None
