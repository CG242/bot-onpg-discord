"""Système de rang (tiers) et ELO compétitif.

Ce module gère :
- Hiérarchie ELO avec 8 tiers (NR, C, B, B+, A, A+, S, S+)
- Formule ELO compétitive avec calcul d'expectancy
- Facteur K variable par rang
- Bonus multiplicateurs par type de FT
- Montée/descente automatique de rang
- Verrouillage admin (rank_manual)
"""

# Hiérarchie révisée (Phase 1 Tâche 4)
TIER_ELO: dict[str, int] = {
    "S+": 2400,  # Supérieur+
    "S": 2200,   # Supérieur
    "A+": 2000,  # Avancé+
    "A": 1800,   # Avancé
    "B+": 1600,  # Bon+
    "B": 1400,   # Bon
    "C": 1200,   # Confirmé (NOUVEAU)
    "NR": 1000,  # Non classé
}

TIER_ORDER = list(TIER_ELO.keys())

# Facteur K variable par rang (Phase 1 Tâche 1)
K_FACTORS: dict[str, int] = {
    "S+": 16,   # Très stable
    "S": 20,    # Stable
    "A+": 24,   # Équilibré
    "A": 28,
    "B+": 32,   # Volatilité croissante
    "B": 40,
    "C": 48,
    "NR": 50,   # Très volatilité (nouveau joueur)
}

# Bonus FT multiplicateurs (Phase 1 Tâche 1)
FT_BONUS_MULTIPLIERS: dict[int, float] = {
    2: 0.5,    # FT2 = 50%
    3: 0.75,   # FT3 = 75%
    5: 1.0,    # FT5 = 100% (référence)
    7: 1.25,   # FT7 = 125%
    10: 1.5,   # FT10 = 150%
}

# Seuils pour promotion/rétrogradation automatique (Phase 1 Tâche 1)
TIER_THRESHOLD = 200  # ±200 ELO du seuil du tier

# Base ELO constants (legacy, pour compatibilité)
ELO_WIN_BASE = 25
ELO_LOSS_BASE = 15


def elo_for_tier(tier: str) -> int:
    """Retourne l'ELO de base pour un tier donné."""
    key = (tier or "NR").upper()
    if key == "NR":
        return 1000
    for k, v in TIER_ELO.items():
        if k.upper() == key:
            return v
    return 1000


def format_tier(tier: str | None) -> str:
    """Formate le tier pour affichage (ex: 'NR' → 'Non classé')."""
    t = (tier or "NR").upper()
    tier_names = {
        "NR": "Non classé",
        "C": "Confirmé",
        "B": "Bon",
        "B+": "Bon+",
        "A": "Avancé",
        "A+": "Avancé+",
        "S": "Supérieur",
        "S+": "Supérieur+",
    }
    return tier_names.get(t, t)


def win_ratio(wins: int, losses: int) -> float:
    """Calcule le taux de victoire en pourcentage."""
    total = wins + losses
    return (wins / total * 100) if total else 0.0


# ============================================================================
# SYSTÈME ELO COMPÉTITIF (Phase 1 Tâche 1)
# ============================================================================

def expectancy(elo_a: int, elo_b: int) -> float:
    """
    Calcule la probabilité théorique de victoire pour le joueur A.
    Formule: E_A = 1 / (1 + 10^((ELO_B - ELO_A) / 400))
    
    Args:
        elo_a: ELO du joueur A
        elo_b: ELO du joueur B
    
    Returns:
        Probabilité de victoire pour A (0.0 à 1.0)
    """
    return 1.0 / (1.0 + pow(10, (elo_b - elo_a) / 400.0))


def compute_elo_delta(
    elo_a: int,
    elo_b: int,
    won: bool,
    tier_a: str,
    ft_type: int,
) -> int:
    """
    Calcule le delta ELO pour le joueur A avec le système compétitif.
    
    Formule: Δ = K × (Résultat - E_A)
    où:
    - K = facteur variable par rang du joueur A
    - Résultat = 1.0 si victoire, 0.0 si défaite
    - E_A = expectancy(elo_a, elo_b)
    
    Args:
        elo_a: ELO du joueur A
        elo_b: ELO du joueur B
        won: True si le joueur A gagne
        tier_a: Tier du joueur A (pour déterminer le facteur K)
        ft_type: Type de FT (2, 3, 5, 7, 10) pour le bonus multiplicateur
    
    Returns:
        Delta ELO à appliquer (peut être négatif)
    """
    # Facteur K selon le tier
    k = K_FACTORS.get((tier_a or "NR").upper(), K_FACTORS["NR"])
    
    # Expectancy théorique
    exp = expectancy(elo_a, elo_b)
    
    # Résultat (1 si victoire, 0 si défaite)
    result = 1.0 if won else 0.0
    
    # Bonus multiplicateur FT
    multiplier = FT_BONUS_MULTIPLIERS.get(ft_type, 1.0)
    
    # Δ = K × bonus × (Résultat - E_A)
    delta = int(round(k * multiplier * (result - exp)))
    
    return delta


def determine_tier_by_elo(elo: int) -> str:
    """
    Détermine le tier basé sur l'ELO selon la hiérarchie révisée.
    
    Args:
        elo: Valeur ELO
    
    Returns:
        Tier correspondant (S+, S, A+, A, B+, B, C, NR)
    """
    if elo >= TIER_ELO["S+"]:
        return "S+"
    elif elo >= TIER_ELO["S"]:
        return "S"
    elif elo >= TIER_ELO["A+"]:
        return "A+"
    elif elo >= TIER_ELO["A"]:
        return "A"
    elif elo >= TIER_ELO["B+"]:
        return "B+"
    elif elo >= TIER_ELO["B"]:
        return "B"
    elif elo >= TIER_ELO["C"]:
        return "C"
    else:
        return "NR"


def should_promote(elo: int, current_tier: str) -> bool:
    """
    Vérifie si le joueur devrait être promu automatiquement.
    Promotion quand ELO > base_tier + TIER_THRESHOLD
    
    Args:
        elo: ELO actuel du joueur
        current_tier: Tier actuel du joueur
    
    Returns:
        True si promotion recommandée
    """
    if current_tier == "S+":
        return False  # Tier max
    
    current_base = TIER_ELO.get(current_tier, 1000)
    return elo > current_base + TIER_THRESHOLD


def should_demote(elo: int, current_tier: str) -> bool:
    """
    Vérifie si le joueur devrait être rétrogradé automatiquement.
    Rétrogradation quand ELO < base_tier - TIER_THRESHOLD
    
    Args:
        elo: ELO actuel du joueur
        current_tier: Tier actuel du joueur
    
    Returns:
        True si rétrogradation recommandée
    """
    if current_tier == "NR":
        return False  # Tier min
    
    current_base = TIER_ELO.get(current_tier, 1000)
    return elo < current_base - TIER_THRESHOLD


def auto_update_tier(elo: int, current_tier: str, rank_manual: bool) -> str:
    """
    Détermine le tier automatiquement selon l'ELO.
    Si rank_manual=1, le tier reste inchangé (verrouillage admin).
    
    Args:
        elo: ELO actuel du joueur
        current_tier: Tier actuel du joueur
        rank_manual: True si le tier est verrouillé par admin
    
    Returns:
        Tier mis à jour (ou inchangé si verrouillage)
    """
    if rank_manual:
        return current_tier  # Tier verrouillé par admin
    
    return determine_tier_by_elo(elo)


def compute_match_elo_delta(ft_type: int, won: bool) -> int:
    """
    LEGACY: Calcule le delta ELO simple (ancien système).
    Conservé pour rétrocompatibilité.
    
    Args:
        ft_type: Type de FT
        won: True si victoire
    
    Returns:
        Delta ELO simple (sans expectancy)
    """
    bonus = {2: 0, 3: 2, 5: 5, 7: 8, 10: 12}.get(ft_type, 0)
    if won:
        return ELO_WIN_BASE + bonus
    return -(ELO_LOSS_BASE + bonus // 2)
