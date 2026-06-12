"""Système de rangs et ELO compétitif v3."""

TIER_ELO: dict[str, int] = {
    "S+": 2400,
    "S": 2200,
    "A+": 2000,
    "A": 1800,
    "B+": 1600,
    "B": 1400,
    "NR": 1000,
}

TIER_ORDER = list(TIER_ELO.keys())

K_FACTOR = 40

FT_COEFFICIENTS: dict[int, float] = {
    2: 1.00,
    3: 1.05,
    5: 1.10,
    7: 1.15,
    10: 1.20,
}


def elo_for_tier(tier: str) -> int:
    key = (tier or "NR").upper()
    if key == "NR":
        return 1000
    for k, v in TIER_ELO.items():
        if k.upper() == key:
            return v
    return 1000


def format_tier(tier: str | None) -> str:
    """Affiche le rang tel quel : S+, A+, B+, NR…"""
    raw = (tier or "NR").strip()
    upper = raw.upper()
    for key in TIER_ELO:
        if key.upper() == upper:
            return key
    return "NR"


def win_ratio(wins: int, losses: int) -> float:
    total = wins + losses
    return (wins / total * 100) if total else 0.0


def expected_score(player_elo: float, opponent_elo: float) -> float:
    return 1.0 / (1.0 + 10 ** ((opponent_elo - player_elo) / 400.0))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_match_elo_changes(
    winner_elo: int, loser_elo: int, ft_type: int
) -> tuple[int, int]:
    """
    ELO compétitif : gain/perte selon niveau adverse et écart.
    FT = léger coefficient multiplicateur.
    """
    ft_mult = FT_COEFFICIENTS.get(ft_type, 1.0)
    e_winner = expected_score(winner_elo, loser_elo)

    win_delta = K_FACTOR * (1.0 - e_winner) * ft_mult
    loss_delta = -K_FACTOR * e_winner * ft_mult

    diff = winner_elo - loser_elo

    if diff >= 800:
        win_delta = _clamp(win_delta, 2, 5)
    elif diff <= -800:
        win_delta = _clamp(win_delta, 35, 50)
        loss_delta = _clamp(loss_delta, -5, -2)
    elif diff <= -400:
        win_delta = _clamp(win_delta, 20, 50)
        loss_delta = _clamp(loss_delta, -8, -2)
    elif diff >= 400:
        win_delta = _clamp(win_delta, 2, 8)
        loss_delta = _clamp(loss_delta, -50, -30)

    return int(round(win_delta)), int(round(loss_delta))


def base_elo_for_player(tier_rank: str | None, rank_manual: bool) -> int:
    tier = (tier_rank or "NR").upper()
    if rank_manual:
        return elo_for_tier(tier)
    return 1000 if tier == "NR" else elo_for_tier(tier)


def rank_from_elo(elo: int) -> str:
    """Calculate rank based on ELO score."""
    if elo >= 2400:
        return "S+"
    elif elo >= 2200:
        return "S"
    elif elo >= 2000:
        return "A+"
    elif elo >= 1800:
        return "A"
    elif elo >= 1600:
        return "B+"
    elif elo >= 1400:
        return "B"
    else:
        return "NR"
