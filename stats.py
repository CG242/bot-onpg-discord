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
    if hasattr(dt, "strftime"):
        return dt.strftime("%d/%m/%Y")
    return str(dt)[:10]


def _region_label(region: str | None) -> str:
    return region.upper() if region else "—"


def _match_result_label(won: bool) -> str:
    return "Gagné" if won else "Perdu"


def _leaderboard_table(rows: list[dict], *, show_region: bool = True) -> str:
    if not rows:
        return "Aucun joueur classé."

    lines = []
    for pos, row in enumerate(rows, start=1):
        name = row.get("display_name") or "?"
        lines.append(f"**{pos}.** {name}")
    return "\n".join(lines)


def format_leaderboard(
    db: Database,
    season_id: int,
    *,
    region: str | None = None,
    title: str = "CLASSEMENT OFFICIEL",
    season_info: dict | None = None,
) -> str:
    season = season_info or db.get_season(season_id) or db.get_active_season()
    debut = season.get("start_date") if season else config.START_DATE
    fin = season.get("end_date") if season and season.get("status") == "archived" else datetime.now(timezone.utc)

    rows = db.get_leaderboard(season_id, region=region, active_only=False)

    lines = [
        f"**{title}**",
        "",
        f"Période : {_fmt_date(debut)} → {_fmt_date(fin)}",
    ]
    if region:
        lines.append(f"Région : **{region}**")
    if season:
        lines.append(f"Saison : **{season.get('name', '?')}**")
    lines.append("")
    lines.append(_leaderboard_table(rows, show_region=not bool(region)))
    lines.append(f"**{len(rows)}** joueur(s) classés")
    return "\n".join(lines).strip()


def format_live_leaderboard_blocks(db: Database, season_id: int) -> list[str]:
    season = db.get_active_season()
    return [
        format_leaderboard(
            db,
            season_id,
            region=region,
            title=config.official_region_title(region),
            season_info=season,
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
        f"**STATISTIQUES — {name}**",
        "",
        f"Rang : `{tier}`",
        f"Région : {reg}",
        f"Points : {points}",
        "",
        "**TOTAL**",
        f"FT gagnés : {wins}",
        f"Défaites : {losses}",
        f"Winrate : {ratio:.0f} %",
        "",
        "**DÉTAIL PAR FT**",
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
    lines.append("**HISTORIQUE DES MATCHS**")

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
        "**DÉTAIL PAR FT**",
    ])

    ft_lines = _ft_head_to_head(matches, player_a, player_b, label_a, label_b)
    lines.extend(ft_lines if ft_lines else ["Aucun détail par format FT."])
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
            f"{p1} {m['score1']}-{m['score2']} {p2} → victoire {winner} ({date_str})"
        )

    return "\n".join(lines).strip()
