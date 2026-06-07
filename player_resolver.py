from dataclasses import dataclass
from typing import Any
import logging

import discord

from database import Database

logger = logging.getLogger(__name__)


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


# ============================================================================
# DÉDUPLICATION DE JOUEURS (Phase 1 Tâche 3)
# ============================================================================

def normalize_name_strict(name: str) -> str:
    """
    Normalise strictement un nom pour détection de doublons.
    
    - Supprime espaces, tirets, underscores
    - Convertit en minuscules
    - Garde uniquement caractères alphanumériques
    
    Args:
        name: Nom du joueur brut
    
    Returns:
        Nom normalisé pour comparaison de doublons
    
    Exemples:
        "Leleo-242" → "leleo242"
        "Le Leo  242" → "leleo242"
        "LELEO_242" → "leleo242"
    """
    if not name:
        return ""
    
    # Supprime espaces, tirets, underscores
    cleaned = name.replace(" ", "").replace("-", "").replace("_", "")
    
    # Minuscules + garde alphanum
    normalized = "".join(c for c in cleaned.lower() if c.isalnum())
    
    return normalized


def similarity_ratio(str1: str, str2: str) -> float:
    """
    Calcule le ratio de similarité Levenshtein entre deux chaînes.
    
    Basé sur la distance de Levenshtein :
    - 1.0 = identique
    - 0.0 = complètement différent
    - >0.95 = doublons probables
    
    Args:
        str1: Première chaîne
        str2: Deuxième chaîne
    
    Returns:
        Ratio de similarité (0.0 à 1.0)
    """
    if not str1 or not str2:
        return 0.0
    
    # Utilise une approche simple : ratio de caractères communs
    # Pour une meilleure performance que Levenshtein
    shorter = min(str1, str2)
    longer = max(str1, str2)
    
    # Si très similaires en longueur, utiliser SequenceMatcher
    if len(longer) - len(shorter) <= 2:
        import difflib
        return difflib.SequenceMatcher(None, str1, str2).ratio()
    
    return 0.0


def find_duplicates(
    db: Database,
    threshold: float = 0.95,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """
    Trouve les paires de joueurs potentiellement dupliqués.
    
    Algorithme :
    1. Récupère tous les joueurs
    2. Normalise chaque nom
    3. Compare paires avec similarity_ratio()
    4. Retourne paires avec ratio > threshold
    
    Args:
        db: Instance Database
        threshold: Seuil de similarité (défaut 0.95 = 95%)
    
    Returns:
        Liste de tuples (joueur1, joueur2, ratio_similarité)
    """
    # Récupère tous les joueurs
    all_players = db.get_all_players()
    if not all_players:
        return []
    
    duplicates: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    checked: set[tuple[int, int]] = set()
    
    for i, player1 in enumerate(all_players):
        norm1 = normalize_name_strict(player1.get("name", ""))
        if not norm1:
            continue
        
        for player2 in all_players[i + 1:]:
            player_id1, player_id2 = player1["id"], player2["id"]
            
            # Évite les vérifications doubles
            if (player_id1, player_id2) in checked or (player_id2, player_id1) in checked:
                continue
            checked.add((player_id1, player_id2))
            
            norm2 = normalize_name_strict(player2.get("name", ""))
            if not norm2:
                continue
            
            # Calcule similarité
            ratio = similarity_ratio(norm1, norm2)
            
            if ratio >= threshold:
                duplicates.append((player1, player2, ratio))
                logger.debug(
                    "Doublon détecté: '%s' <→ '%s' (ratio=%.2f)",
                    player1.get("name"),
                    player2.get("name"),
                    ratio
                )
    
    return duplicates


def get_deduplication_suggestions(
    db: Database,
    min_matches: int = 1,
) -> dict[str, list]:
    """
    Fournit des suggestions de déduplication intelligentes.
    
    Groupes les doublons par normalized_name pour faciliter fusion en masse.
    
    Args:
        db: Instance Database
        min_matches: Nombre minimum de correspondances pour suggérer
    
    Returns:
        Dict avec clés = nom normalisé, valeurs = liste joueurs
    """
    all_players = db.get_all_players()
    if not all_players:
        return {}
    
    groups: dict[str, list[dict[str, Any]]] = {}
    
    for player in all_players:
        norm = normalize_name_strict(player.get("name", ""))
        if not norm:
            continue
        
        if norm not in groups:
            groups[norm] = []
        groups[norm].append(player)
    
    # Filtre : garder seulement les noms avec 2+ joueurs
    return {k: v for k, v in groups.items() if len(v) >= 2}

