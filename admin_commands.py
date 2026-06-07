"""Commandes administrateur pour Phase 2 - Gestion saisons et déduplication."""

import logging
from datetime import datetime

import discord
from discord import app_commands

import config
from database import Database
from player_resolver import (
    find_duplicates,
    get_deduplication_suggestions,
    normalize_name_strict,
)

logger = logging.getLogger(__name__)


def is_admin(interaction: discord.Interaction) -> bool:
    """Vérifie si l'utilisateur est admin."""
    if not interaction.guild:
        return False
    perms = interaction.user.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True
    if config.ADMIN_ROLE_ID:
        role = interaction.guild.get_role(config.ADMIN_ROLE_ID)
        if role and role in interaction.user.roles:
            return True
    return False


def setup_admin_commands(tree: app_commands.CommandTree, db: Database) -> None:
    """Configure les commandes administrateur de Phase 2."""

    # ========================================================================
    # TÂCHE 5: COMMANDE /nouvelle-saison
    # ========================================================================

    @tree.command(
        name="nouvelle-saison",
        description="Admin : créer une nouvelle saison"
    )
    @app_commands.describe(
        nom="Nom de la nouvelle saison (ex: 'Saison 2 - Février')",
        date_debut="Date de début (optionnel, format AAAA-MM-JJ)",
        description="Description ou notes (optionnel)",
    )
    async def nouvelle_saison_cmd(
        interaction: discord.Interaction,
        nom: str,
        date_debut: str | None = None,
        description: str | None = None,
    ):
        """
        Phase 1 Tâche 2: GESTION DES SAISONS - Création
        
        Crée une nouvelle saison :
        1. Archive la saison active actuelle
        2. Crée la nouvelle saison
        3. Réinitialise les ELO des joueurs
        """
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée (admin requis).", ephemeral=True
            )
            return

        try:
            await interaction.response.defer()
            
            # Parse la date de début
            if date_debut:
                try:
                    start_date = datetime.strptime(date_debut, "%Y-%m-%d").date()
                except ValueError:
                    await interaction.followup.send(
                        "Format date invalide. Utilisez AAAA-MM-JJ",
                        ephemeral=True,
                    )
                    return
            else:
                start_date = datetime.now().date()
            
            # Récupère la saison active
            old_season = db.get_active_season()
            old_season_id = old_season["id"] if old_season else None
            old_name = old_season.get("name", "Saison précédente") if old_season else "Aucune"
            
            # Crée la nouvelle saison
            new_season_id = db.create_season(nom, start_date)
            
            # Recalcule les ELO (réinitialise)
            if new_season_id:
                db.recalculate_season_elo(new_season_id)
            
            # Prépare le résumé
            summary = f"""
**✅ Nouvelle saison créée !**

📌 **Saison précédente**: {old_name}
📌 **Nouvelle saison**: {nom}
📅 **Date de début**: {start_date.strftime('%d/%m/%Y')}

**Actions effectuées:**
1. ✓ Saison '{old_name}' archivée
2. ✓ Saison '{nom}' créée (ID: {new_season_id})
3. ✓ ELO réinitialisés pour tous les joueurs
4. ✓ Classements réinitialisés

_La saison est maintenant active. Les scores enregistrés s'appliqueront à cette nouvelle saison._
            """
            
            await interaction.followup.send(summary, ephemeral=False)
            logger.info(
                "Nouvelle saison créée: '%s' (id=%s), ancienne '%s' archivée (id=%s)",
                nom,
                new_season_id,
                old_name,
                old_season_id,
            )
        except Exception as e:
            logger.exception("Erreur création saison: %s", e)
            await interaction.followup.send(
                f"Erreur : {str(e)}",
                ephemeral=True,
            )

    # ========================================================================
    # TÂCHE 6: COMMANDE /fusion-joueurs
    # ========================================================================

    @tree.command(
        name="fusion-joueurs",
        description="Admin : fusionner deux profils joueurs"
    )
    @app_commands.describe(
        joueur_source="Pseudo du joueur à fusionner (sera supprimé)",
        joueur_cible="Pseudo du joueur cible (conservé)",
        raison="Raison de la fusion (optionnel)",
    )
    async def fusion_joueurs_cmd(
        interaction: discord.Interaction,
        joueur_source: str,
        joueur_cible: str,
        raison: str | None = None,
    ):
        """
        Phase 1 Tâche 3: DÉDUPLICATION - Fusion manuelle
        
        Fusionne deux profils :
        1. Transfère tous les matches du source au cible
        2. Enregistre l'opération dans deduplication_history
        3. Supprime le profil source
        """
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée (admin requis).", ephemeral=True
            )
            return

        try:
            await interaction.response.defer()
            
            # Récupère les joueurs
            source = db.get_or_create_player(joueur_source)
            cible = db.get_or_create_player(joueur_cible)
            
            if source["id"] == cible["id"]:
                await interaction.followup.send(
                    "Impossible de fusionner un joueur avec lui-même.",
                    ephemeral=True,
                )
                return
            
            # Effectue la fusion
            success = db.merge_players(
                source["id"],
                cible["id"],
                merged_by=f"{interaction.user.name}#{interaction.user.discriminator}",
            )
            
            if not success:
                await interaction.followup.send(
                    "Erreur lors de la fusion.",
                    ephemeral=True,
                )
                return
            
            summary = f"""
**✅ Fusion réussie !**

👤 **Source (supprimé)**: {source['name']}
👤 **Cible (conservé)**: {cible['name']}

{"📝 Raison: " + raison if raison else ""}

_Les matchs du joueur source ont été transférés à la cible._
_Le profil source a été supprimé._
            """
            
            await interaction.followup.send(summary, ephemeral=False)
            logger.info(
                "Fusion réussie: %s (id=%s) → %s (id=%s)",
                source['name'],
                source['id'],
                cible['name'],
                cible['id'],
            )
        except Exception as e:
            logger.exception("Erreur fusion joueurs: %s", e)
            await interaction.followup.send(
                f"Erreur : {str(e)}",
                ephemeral=True,
            )

    # ========================================================================
    # TÂCHE 7: COMMANDE /deduplication-auto
    # ========================================================================

    @tree.command(
        name="deduplication-auto",
        description="Admin : détecter les doublons automatiquement"
    )
    @app_commands.describe(
        seuil="Seuil de similarité % (défaut: 95)",
    )
    async def deduplication_auto_cmd(
        interaction: discord.Interaction,
        seuil: int | None = None,
    ):
        """
        Phase 1 Tâche 3: DÉDUPLICATION - Détection automatique
        
        Scanne tous les joueurs et suggère les fusions basées sur similarité.
        """
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée (admin requis).", ephemeral=True
            )
            return

        try:
            await interaction.response.defer()
            
            threshold = (seuil or 95) / 100.0  # Convertir % en ratio
            if threshold < 0.0 or threshold > 1.0:
                await interaction.followup.send(
                    "Seuil invalide (0-100%).",
                    ephemeral=True,
                )
                return
            
            # Détecte les doublons
            suggestions = get_deduplication_suggestions(db, min_matches=1)
            
            if not suggestions:
                await interaction.followup.send(
                    "Aucun doublon détecté.",
                    ephemeral=False,
                )
                return
            
            # Formate les suggestions
            lines = [
                "**🔍 Doublons détectés :**",
                "",
            ]
            
            for normalized_name, players in list(suggestions.items())[:10]:  # Limité à 10
                if len(players) >= 2:
                    names = ", ".join([f"'{p['name']}' (ID:{p['id']})" for p in players])
                    lines.append(f"→ {names}")
            
            if len(suggestions) > 10:
                lines.append(f"... et {len(suggestions) - 10} autre(s)")
            
            lines.extend([
                "",
                "**Pour fusionner manuellement :**",
                "`/fusion-joueurs joueur_source:X joueur_cible:Y`",
                "",
                "_Suggestion: Vérifier manuellement avant de fusionner._",
            ])
            
            response = "\n".join(lines)
            await interaction.followup.send(response, ephemeral=False)
            
            logger.info(
                "Déduplication auto: %d groupes douteux trouvés",
                len(suggestions),
            )
        except Exception as e:
            logger.exception("Erreur déduplication auto: %s", e)
            await interaction.followup.send(
                f"Erreur : {str(e)}",
                ephemeral=True,
            )

    # ========================================================================
    # TÂCHE 8: COMMANDES /saisons et /terminer-saison
    # ========================================================================

    @tree.command(
        name="saisons",
        description="Admin : lister toutes les saisons"
    )
    async def saisons_cmd(interaction: discord.Interaction):
        """
        Liste toutes les saisons (actives et archivées) avec statistiques.
        """
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée (admin requis).", ephemeral=True
            )
            return

        try:
            await interaction.response.defer()
            
            seasons = db.get_all_seasons()
            if not seasons:
                await interaction.followup.send(
                    "Aucune saison trouvée.",
                    ephemeral=True,
                )
                return
            
            lines = ["**📋 Toutes les saisons :**", ""]
            
            for season in seasons:
                status = "🟢 ACTIVE" if season["is_active"] else "🔴 Archivée"
                season_id = season["id"]
                name = season["name"]
                start = season["start_date"]
                end = season.get("end_date") or "—"
                
                # Stats de la saison
                match_count = db.count_season_matches(season_id)
                player_count = db.count_season_players(season_id)
                
                champion_name = "—"
                if season.get("champion_id"):
                    champion = db.get_player_by_discord_id(season["champion_id"])
                    if champion:
                        champion_name = champion.get("name", "—")
                
                lines.append(f"**#{season_id}** — {name}")
                lines.append(f"  Status: {status}")
                lines.append(f"  Période: {start} → {end}")
                lines.append(f"  Matchs: {match_count} | Joueurs: {player_count}")
                lines.append(f"  Champion: {champion_name}")
                lines.append("")
            
            response = "\n".join(lines[:50])  # Limité pour Discord
            await interaction.followup.send(response, ephemeral=False)
        except Exception as e:
            logger.exception("Erreur lister saisons: %s", e)
            await interaction.followup.send(
                f"Erreur : {str(e)}",
                ephemeral=True,
            )

    @tree.command(
        name="terminer-saison",
        description="Admin : archiver et couronner le champion de la saison active"
    )
    @app_commands.describe(
        champion="Champion (optionnel, sinon automatique = leader)"
    )
    async def terminer_saison_cmd(
        interaction: discord.Interaction,
        champion: str | None = None,
    ):
        """
        Termine la saison active :
        1. Détermine le champion (leader du classement ou spécifié)
        2. Archive la saison
        """
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée (admin requis).", ephemeral=True
            )
            return

        try:
            await interaction.response.defer()
            
            season = db.get_active_season()
            if not season:
                await interaction.followup.send(
                    "Aucune saison active.",
                    ephemeral=True,
                )
                return
            
            champion_id = None
            if champion:
                champ_player = db.get_or_create_player(champion)
                champion_id = champ_player["id"]
            
            # Archive la saison
            db.close_season(season["id"], champion_id)
            
            # Récupère le champion assigné
            champion_name = "—"
            if champion_id:
                champ = db.get_player_by_discord_id(champion_id)
                if champ:
                    champion_name = champ.get("name", "—")
            else:
                leaderboard = db.get_leaderboard(season["id"], active_only=False)
                if leaderboard:
                    champion_name = leaderboard[0].get("name", "—")
            
            summary = f"""
**✅ Saison terminée !**

📌 **Saison**: {season['name']}
👑 **Champion**: {champion_name}
📅 **Fin**: {datetime.now().date().strftime('%d/%m/%Y')}

_La saison a été archivée. Une nouvelle saison peut être créée avec `/nouvelle-saison`._
            """
            
            await interaction.followup.send(summary, ephemeral=False)
            logger.info(
                "Saison %s terminée (champion_id=%s)",
                season["id"],
                champion_id,
            )
        except Exception as e:
            logger.exception("Erreur terminer saison: %s", e)
            await interaction.followup.send(
                f"Erreur : {str(e)}",
                ephemeral=True,
            )
