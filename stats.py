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


# ============================================================================
# PHASE 3 TÂCHE 9: NOUVEAU SYSTÈME DE SCORING
# ============================================================================

def calculate_player_score(
    elo: int,
    wins: int,
    losses: int,
    last_match_at: datetime | None,
) -> int:
    """
    Calcule le score composite du joueur selon la formule Phase 3 Tâche 9.
    
    Formule: Score = (ELO × 0.5) + (taux % × 0.3) + (bonus_activité × 0.15) + (matchs × 0.05)
    
    Où:
    - ELO : Points ELO actuels (poids 50%)
    - taux % : Taux de victoire en % (poids 30%)
    - bonus_activité : Points bonus selon récence (poids 15%)
      * <7j : +50
      * <14j : +25
      * <30j : +10
      * ≥30j : +0
    - matchs : Nombre de matchs joués (poids 5%)
    
    Args:
        elo: Points ELO
        wins: Nombre de victoires
        losses: Nombre de défaites
        last_match_at: Date du dernier match
    
    Returns:
        Score composite arrondi
    """
    # Composante 1 : ELO (poids 50%)
    elo_component = elo * 0.5
    
    # Composante 2 : Taux de victoire (poids 30%)
    total_matches = wins + losses
    if total_matches > 0:
        win_percentage = (wins / total_matches) * 100
        win_component = win_percentage * 0.3
    else:
        win_component = 0.0
    
    # Composante 3 : Bonus activité (poids 15%)
    activity_bonus = 0.0
    if last_match_at:
        now = datetime.now(timezone.utc)
        if isinstance(last_match_at, str):
            try:
                last_match_at = datetime.fromisoformat(last_match_at)
                if last_match_at.tzinfo is None:
                    last_match_at = last_match_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                last_match_at = None
        
        if last_match_at:
            days_since = (now - last_match_at).days
            if days_since < 7:
                activity_bonus = 50.0
            elif days_since < 14:
                activity_bonus = 25.0
            elif days_since < 30:
                activity_bonus = 10.0
    
    activity_component = activity_bonus * 0.15
    
    # Composante 4 : Nombre de matchs (poids 5%)
    match_component = total_matches * 0.05
    
    # Score total
    total_score = elo_component + win_component + activity_component + match_component
    
    return int(round(total_score))


def format_leaderboard(
    db: Database,
    season_id: int,
    *,
    region: str | None = None,
    title: str = "CLASSEMENT OFFICIEL",
    date_debut=None,
    date_fin=None,
    use_new_scoring: bool = False,
) -> str:
    """
    Formate le classement avec tri intelligentoptionnel nouveau scoring.
    
    Args:
        use_new_scoring: Si True, utilise la formule composée Phase 3 Tâche 9
    """
    debut = date_debut if date_debut else config.START_DATE
    display_fin = date_fin if date_fin else datetime.now(timezone.utc)

    rows = db.get_leaderboard(
        season_id,
        region=region,
        active_only=False,
        date_debut=debut,
        date_fin=date_fin,
    )

    # Calcule les scores si nouveau système activé
    if use_new_scoring and rows:
        for row in rows:
            row["score"] = calculate_player_score(
                row.get("elo", 0),
                row.get("ft_wins", 0),
                row.get("ft_losses", 0),
                row.get("last_match_at"),
            )
        # Trie par score DESC
        rows.sort(key=lambda r: r.get("score", 0), reverse=True)

    lines = [
        f"**{title}**",
        "",
        f"Période : {_fmt_date(debut)} → {_fmt_date(display_fin)}",
    ]
    if region:
        lines.append(f"Région : {region}")
    if use_new_scoring:
        lines.append("Tri par score composite (ELO + activité + résultats)")
    else:
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
        
        if use_new_scoring:
            score = row.get("score", 0)
            lines.append(f"**TOP {pos} — {name}**")
            lines.append(f"Score : {score} | ELO : {points}")
            lines.append(f"Rang : {tier}")
            if not region:
                lines.append(f"Région : {reg}")
            lines.append(f"Ratio : {ratio:.0f}% ({wins}V-{losses}D)")
        else:
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
