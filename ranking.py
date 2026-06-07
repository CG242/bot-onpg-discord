"""Système de rang (tiers) et ELO."""

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

ELO_WIN_BASE = 25
ELO_LOSS_BASE = 15


def elo_for_tier(tier: str) -> int:
    key = (tier or "NR").upper()
    if key == "NR":
        return 1000
    for k, v in TIER_ELO.items():
        if k.upper() == key:
            return v
    return 1000


def format_tier(tier: str | None) -> str:
    t = (tier or "NR").upper()
    if t == "NR":
        return "Non classé"
    return t


def win_ratio(wins: int, losses: int) -> float:
    total = wins + losses
    return (wins / total * 100) if total else 0.0


def compute_match_elo_delta(ft_type: int, won: bool) -> int:
    bonus = {2: 0, 3: 2, 5: 5, 7: 8, 10: 12}.get(ft_type, 0)
    if won:
        return ELO_WIN_BASE + bonus
    return -(ELO_LOSS_BASE + bonus // 2)
