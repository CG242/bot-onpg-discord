import logging
from datetime import date, datetime
from pathlib import Path

import discord
from discord import app_commands

import config
from database import Database
from player_resolver import (
    format_ambiguous_players,
    resolve_player_input,
)
from stats import (
    format_comparison,
    format_inter_region_leaderboard,
    format_leaderboard,
    format_player_stats,
)
from views import (
    ArchivedSeasonView,
    FusionManageView,
    PlayerNotFoundView,
    RankTierPickView,
    RegionManageView,
    SeasonNewConfirmView,
)

logger = logging.getLogger(__name__)

MSG_LIMIT = 2000


def _split_message(text: str, limit: int = 1990) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return parts


async def send_reply(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool = False,
    view: discord.ui.View | None = None,
) -> None:
    """Répond à une interaction (messages longs + interaction déjà acquittée)."""
    parts = _split_message(content)

    try:
        if interaction.response.is_done():
            for part in parts:
                await interaction.followup.send(part, ephemeral=ephemeral)
            return

        if view is not None:
            await interaction.response.send_message(
                parts[0], ephemeral=ephemeral, view=view
            )
            for part in parts[1:]:
                await interaction.followup.send(part, ephemeral=ephemeral)
            return

        if len(parts) == 1:
            await interaction.response.send_message(parts[0], ephemeral=ephemeral)
            return

        await interaction.response.defer(ephemeral=ephemeral)
        for part in parts:
            await interaction.followup.send(part, ephemeral=ephemeral)
    except discord.HTTPException as exc:
        if exc.code == 40060:
            logger.warning("Interaction déjà acquittée — envoi via followup")
            for part in parts:
                await interaction.followup.send(part, ephemeral=ephemeral)
        else:
            raise


def is_admin(interaction: discord.Interaction) -> bool:
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


async def _refresh_leaderboard(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if interaction.guild and hasattr(bot, "update_leaderboard_message"):
        await bot.update_leaderboard_message(interaction.guild)


def _resolve(joueur, db: Database, season_id: int | None):
    result = resolve_player_input(db, joueur, season_id)
    if result.found and result.player:
        return result.player, db.player_display_name(result.player)
    return None, result.label or str(joueur)


def build_aide_embed() -> discord.Embed:
    embed = discord.Embed(
        title="FT Championship — Guide",
        description=(
            "Le bot lit les scores dans le salon **#scores**, calcule les points "
            "avec un **ELO compétitif** et met à jour les classements **automatiquement**."
        ),
        color=0x2B2D31,
    )
    embed.add_field(
        name="Enregistrer un score",
        value=(
            "Postez dans le salon scores :\n"
            "```\n"
            "Leleo242 5 - 1 David MK\n"
            "Saint-sir 3 - 0 Le David_Mk\n"
            "JoueurA 5 - 2 JoueurB\n"
            "```\n"
            "Plusieurs lignes = plusieurs matchs. Formats FT : 2, 3, 5, 7, 10."
        ),
        inline=False,
    )
    embed.add_field(
        name="Consulter ses statistiques",
        value=(
            "`/stats pseudo:Leleo242`\n"
            "`/stats pseudo:David MK`\n"
            "Les variantes de pseudo sont reconnues automatiquement."
        ),
        inline=False,
    )
    embed.add_field(
        name="Classements",
        value=(
            "`/classement` — tous les joueurs (saison active)\n"
            "`/classement-bz` — région BZ\n"
            "`/classement-pn` — région PN\n"
            "`/classement-bz-pn` — confrontations BZ vs PN\n"
            "`/classement-saison` — ancienne saison archivée"
        ),
        inline=False,
    )
    embed.add_field(
        name="Comparer deux joueurs",
        value="`/compare joueur_a:Leleo242 joueur_b:David MK`",
        inline=False,
    )
    embed.add_field(
        name="Système de points",
        value=(
            "Rangs : `S+` 2400 · `S` 2200 · `A+` 2000 · `A` 1800 · "
            "`B+` 1600 · `B` 1400 · `NR` 1000\n"
            "Battre un adversaire plus fort = gros gain.\n"
            "Perdre contre un faible = grosse perte."
        ),
        inline=False,
    )
    embed.add_field(
        name="Administration",
        value=(
            "`/rang-attribuer` · `/region` · `/fusion-joueur`\n"
            "`/export-donnees` · `/telechargement-donnees`\n"
            "`/recuperation-scores-2026` · `/classement-bz-pn`\n"
            "`/saison-nouvelle` · `/reset-saison` · `/backup` · `/restore`"
        ),
        inline=False,
    )
    embed.set_footer(text="Stats et compare : saison active uniquement.")
    return embed


def setup_commands(tree: app_commands.CommandTree, db: Database) -> None:
    @tree.command(name="aide", description="Guide complet du bot FT Championship")
    async def aide_cmd(interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=build_aide_embed(), ephemeral=True
        )

    @tree.command(
        name="classement",
        description="Classement de la saison active (tous les joueurs)",
    )
    async def classement(interaction: discord.Interaction):
        season = db.get_active_season()
        if not season:
            await send_reply(interaction, "Aucune saison active.", ephemeral=True)
            return
        await send_reply(
            interaction,
            format_leaderboard(
                db,
                season["id"],
                title="CLASSEMENT OFFICIEL",
                season_info=season,
            ),
        )

    @tree.command(
        name="classement-bz",
        description="Classement de la saison active — région BZ",
    )
    async def classement_bz(interaction: discord.Interaction):
        season = db.get_active_season()
        if not season:
            await send_reply(interaction, "Aucune saison active.", ephemeral=True)
            return
        await send_reply(
            interaction,
            format_leaderboard(
                db,
                season["id"],
                region="BZ",
                title=config.official_region_title("BZ"),
                season_info=season,
            ),
        )

    @tree.command(
        name="classement-pn",
        description="Classement de la saison active — région PN",
    )
    async def classement_pn(interaction: discord.Interaction):
        season = db.get_active_season()
        if not season:
            await send_reply(interaction, "Aucune saison active.", ephemeral=True)
            return
        await send_reply(
            interaction,
            format_leaderboard(
                db,
                season["id"],
                region="PN",
                title=config.official_region_title("PN"),
                season_info=season,
            ),
        )

    @tree.command(
        name="classement-saison",
        description="Classement final d'une saison archivée",
    )
    async def classement_saison(interaction: discord.Interaction):
        archived = db.list_archived_seasons()
        if not archived:
            await send_reply(interaction, "Aucune saison archivée.", ephemeral=True)
            return
        view = ArchivedSeasonView(db, archived, interaction.user.id)
        await send_reply(
            interaction,
            "Sélectionnez une saison archivée :",
            ephemeral=True,
            view=view,
        )

    @tree.command(name="stats", description="Statistiques d'un joueur (saison active)")
    @app_commands.describe(
        pseudo="Pseudo utilisé dans les scores",
        membre="Membre Discord (prioritaire si renseigné)",
    )
    async def stats_cmd(
        interaction: discord.Interaction,
        pseudo: str,
        membre: discord.Member | None = None,
    ):
        season = db.get_active_season()
        if not season:
            await send_reply(interaction, "Aucune saison active.", ephemeral=True)
            return

        target = membre if membre else pseudo
        result = resolve_player_input(db, target, season["id"])

        if result.ambiguous:
            await send_reply(
                interaction,
                format_ambiguous_players(db, result.candidates),
                ephemeral=True,
            )
            return

        if result.found and result.player:
            stats = db.get_player_stats(result.player["id"], season["id"])
            await send_reply(
                interaction, format_player_stats(stats, result.label)
            )
            return

        search = result.search_text or pseudo
        view = PlayerNotFoundView(db, season["id"], search, interaction.user.id)
        await send_reply(
            interaction,
            f"**{search}** introuvable sur la saison active.\n"
            "Souhaitez-vous créer ce joueur ?",
            ephemeral=True,
            view=view,
        )

    @tree.command(
        name="compare",
        description="Comparer deux joueurs (saison active)",
    )
    @app_commands.describe(joueur_a="Premier joueur", joueur_b="Second joueur")
    async def compare_cmd(
        interaction: discord.Interaction, joueur_a: str, joueur_b: str
    ):
        season = db.get_active_season()
        if not season:
            await send_reply(interaction, "Aucune saison active.", ephemeral=True)
            return

        res_a = resolve_player_input(db, joueur_a, season["id"])
        res_b = resolve_player_input(db, joueur_b, season["id"])
        if res_a.ambiguous:
            await send_reply(
                interaction,
                f"Joueur A — {format_ambiguous_players(db, res_a.candidates)}",
                ephemeral=True,
            )
            return
        if res_b.ambiguous:
            await send_reply(
                interaction,
                f"Joueur B — {format_ambiguous_players(db, res_b.candidates)}",
                ephemeral=True,
            )
            return
        if not res_a.found:
            await send_reply(
                interaction, f"Joueur A introuvable : **{joueur_a}**", ephemeral=True
            )
            return
        if not res_b.found:
            await send_reply(
                interaction, f"Joueur B introuvable : **{joueur_b}**", ephemeral=True
            )
            return
        if res_a.player["id"] == res_b.player["id"]:
            await send_reply(
                interaction, "Deux joueurs différents sont requis.", ephemeral=True
            )
            return

        matches = db.get_head_to_head(
            res_a.player["id"], res_b.player["id"], season["id"]
        )
        await send_reply(
            interaction,
            format_comparison(res_a.player, res_b.player, matches, db),
        )

    @tree.command(
        name="classement-bz-pn",
        description="Confrontations entre joueurs BZ et PN (saison active)",
    )
    async def classement_bz_pn(interaction: discord.Interaction):
        season = db.get_active_season()
        if not season:
            await send_reply(interaction, "Aucune saison active.", ephemeral=True)
            return
        await send_reply(
            interaction,
            format_inter_region_leaderboard(db, season["id"]),
        )

    @tree.command(
        name="rang-attribuer",
        description="[Admin] Attribuer un rang à un ou plusieurs joueurs",
    )
    async def rang_attribuer_cmd(interaction: discord.Interaction):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return

        players = db.list_all_players()
        if not players:
            await send_reply(
                interaction, "Aucun joueur enregistré.", ephemeral=True
            )
            return

        view = RankTierPickView(db, players, interaction.user.id)
        await send_reply(
            interaction,
            f"**Attribution des rangs** — {len(players)} joueur(s)\n"
            "**Étape 1 :** choisissez le rang (S+, S, A+, A, B+, B, NR)\n"
            "**Étape 2 :** sélectionnez les joueurs puis confirmez",
            ephemeral=True,
            view=view,
        )

    @tree.command(
        name="fusion-joueur",
        description="[Admin] Fusionner deux doublons en un seul joueur",
    )
    async def fusion_joueur_cmd(interaction: discord.Interaction):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return
        players = db.list_all_players()
        if len(players) < 2:
            await send_reply(
                interaction, "Pas assez de joueurs pour une fusion.", ephemeral=True
            )
            return
        view = FusionManageView(db, players, interaction.user.id)
        await send_reply(
            interaction,
            f"**Fusion de joueurs** — {len(players)} joueur(s)\n"
            "1. Menu **Joueur à GARDER** (profil conservé)\n"
            "2. Menu **Joueur à FUSIONNER** (supprimé, matchs transférés)\n"
            "3. **Confirmer fusion**\n"
            "Garder : **—** · Fusionner : **—**",
            ephemeral=True,
            view=view,
        )

    @tree.command(
        name="region",
        description="[Admin] Choisir un joueur et lui assigner BZ, PN ou retirer",
    )
    async def region_cmd(interaction: discord.Interaction):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return

        players = db.list_all_players()
        if not players:
            await send_reply(
                interaction, "Aucun joueur enregistré.", ephemeral=True
            )
            return

        view = RegionManageView(db, players, interaction.user.id)
        await send_reply(
            interaction,
            f"**Gestion des régions** — {len(players)} joueur(s)\n"
            "1. Choisissez un joueur dans le menu\n"
            "2. Cliquez **BZ**, **PN** ou **Retirer région**",
            ephemeral=True,
            view=view,
        )

    @tree.command(
        name="saison-nouvelle",
        description="[Admin] Archiver la saison active et en créer une nouvelle",
    )
    @app_commands.describe(
        nom="Nom de la nouvelle saison",
        date_debut="Date de début (AAAA-MM-JJ)",
    )
    async def saison_nouvelle(
        interaction: discord.Interaction,
        nom: str,
        date_debut: str | None = None,
    ):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return
        try:
            start = date.fromisoformat(date_debut) if date_debut else date.today()
        except ValueError:
            await send_reply(
                interaction, "Date invalide. Format : AAAA-MM-JJ.", ephemeral=True
            )
            return

        active = db.get_active_season()
        lines = [f"Créer la saison **{nom}** (début {start}) ?"]
        if active:
            lines.insert(
                0,
                f"Saison active : **{active.get('name')}** → sera **archivée**.",
            )
        lines.append(
            "Les scores Discord **antérieurs à cette date** ne seront **pas** importés."
        )
        view = SeasonNewConfirmView(db, nom, start, interaction.user.id, active)
        await send_reply(interaction, "\n".join(lines), ephemeral=True, view=view)

    @tree.command(
        name="reset-saison",
        description="[Admin] Effacer les matchs de la saison active (joueurs conservés)",
    )
    async def reset_saison_cmd(interaction: discord.Interaction):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return
        deleted = db.reset_active_season_data()
        await send_reply(
            interaction,
            f"Saison réinitialisée — **{deleted}** match(s) supprimé(s).\n"
            "Les anciens scores Discord ne seront **plus réimportés**.\n"
            "Joueurs, régions et rangs conservés. Points remis selon le rang.",
            ephemeral=True,
        )
        await _refresh_leaderboard(interaction)

    @tree.command(
        name="recalculer-points",
        description="[Admin] Recalculer les points ELO de la saison active",
    )
    async def recalculer_points(interaction: discord.Interaction):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return
        season = db.get_active_season()
        if not season:
            await send_reply(interaction, "Aucune saison active.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.client.loop.run_in_executor(
            None, db.recalculate_season_elo, season["id"]
        )
        await _refresh_leaderboard(interaction)
        await interaction.followup.send(
            f"Points recalculés pour **{season['name']}**.", ephemeral=True
        )

    @tree.command(
        name="export-donnees",
        description="[Admin] Export Excel + sauvegarde JSON (saison active)",
    )
    async def export_donnees_cmd(interaction: discord.Interaction):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return
        season = db.get_active_season()
        if not season:
            await send_reply(interaction, "Aucune saison active.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        from export_data import build_season_excel

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_path = Path(config.BACKUP_DIR) / f"backup_{ts}.json"
        excel_path = Path(config.BACKUP_DIR) / f"export_{ts}.xlsx"

        await interaction.client.loop.run_in_executor(
            None, db.export_backup, backup_path
        )
        await interaction.client.loop.run_in_executor(
            None, build_season_excel, db, season["id"], excel_path
        )

        await interaction.followup.send(
            f"Sauvegarde JSON : `{backup_path.name}`\n"
            f"Export Excel saison **{season['name']}** :",
            ephemeral=True,
            file=discord.File(excel_path, filename=excel_path.name),
        )

    @tree.command(
        name="telechargement-donnees",
        description="[Admin] Télécharger l'export Excel (nécessite une sauvegarde)",
    )
    async def telechargement_donnees_cmd(interaction: discord.Interaction):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return
        season = db.get_active_season()
        if not season:
            await send_reply(interaction, "Aucune saison active.", ephemeral=True)
            return

        from export_data import build_season_excel, list_backup_files

        backups = list_backup_files()
        if not backups:
            await send_reply(
                interaction,
                "Aucune sauvegarde trouvée.\n"
                "Lancez d'abord `/backup` ou `/export-donnees`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        excel_path = Path(config.BACKUP_DIR) / f"export_{ts}.xlsx"
        await interaction.client.loop.run_in_executor(
            None, build_season_excel, db, season["id"], excel_path
        )
        await interaction.followup.send(
            f"Dernière sauvegarde : `{backups[0].name}`\n"
            f"Export Excel saison **{season['name']}** :",
            ephemeral=True,
            file=discord.File(excel_path, filename=excel_path.name),
        )

    @tree.command(
        name="recuperation-scores-2026",
        description="[Admin] Réimporter les scores Discord depuis le 01/01/2026",
    )
    async def recuperation_scores_2026(interaction: discord.Interaction):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return
        if not interaction.guild:
            await send_reply(
                interaction, "Commande utilisable sur le serveur uniquement.",
                ephemeral=True,
            )
            return
        season = db.get_active_season()
        if not season:
            await send_reply(interaction, "Aucune saison active.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        bot = interaction.client
        count = await bot.recover_scores_from_date(
            interaction.guild, since=config.START_DATE
        )
        await _refresh_leaderboard(interaction)
        await interaction.followup.send(
            f"Récupération terminée depuis le **{config.START_DATE.date()}**.\n"
            f"**{count}** changement(s) — matchs importés dans la saison "
            f"**{season['name']}**.\n"
            "Les scores Discord antérieurs au reset sont de nouveau pris en compte.",
            ephemeral=True,
        )

    @tree.command(
        name="backup",
        description="[Admin] Sauvegarder toutes les données en JSON",
    )
    async def backup_cmd(interaction: discord.Interaction):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = Path(config.BACKUP_DIR) / f"backup_{ts}.json"
        out = await interaction.client.loop.run_in_executor(
            None, db.export_backup, path
        )
        await interaction.followup.send(
            f"Sauvegarde créée :\n`{out}`\n"
            "Contient : saisons, joueurs, matchs, paramètres.",
            ephemeral=True,
        )

    @tree.command(
        name="restore",
        description="[Admin] Vider la base et repartir à zéro (ou restaurer une sauvegarde)",
    )
    @app_commands.describe(
        fichier="Nom du fichier backup (optionnel). Sans fichier = tout effacer.",
    )
    async def restore_cmd(
        interaction: discord.Interaction, fichier: str | None = None
    ):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        if fichier:
            path = Path(config.BACKUP_DIR) / fichier
            if not path.exists():
                await interaction.followup.send(
                    f"Fichier introuvable : `{fichier}`", ephemeral=True
                )
                return
            await interaction.client.loop.run_in_executor(
                None, db.restore_backup, path
            )
            msg = (
                f"Base vidée puis restaurée depuis `{fichier}`.\n"
                "Toutes les tables ont été remplacées par la sauvegarde."
            )
        else:
            await interaction.client.loop.run_in_executor(
                None, db.wipe_all_and_reset
            )
            msg = (
                "Base **entièrement vidée** et réinitialisée.\n"
                "Nouvelle saison vierge créée. Aucun joueur, aucun match."
            )

        await _refresh_leaderboard(interaction)
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(name="sante", description="[Admin] État du bot et de la base de données")
    async def sante_cmd(interaction: discord.Interaction):
        if not is_admin(interaction):
            await send_reply(interaction, "Permission refusée.", ephemeral=True)
            return
        report = db.health_report()
        season = report.get("active_season")
        sname = season.get("name") if season else "—"
        await send_reply(
            interaction,
            f"**État FT Championship**\n"
            f"Base de données : OK\n"
            f"Saison active : **{sname}**\n"
            f"Joueurs : **{report['players']}**\n"
            f"Matchs : **{report['matches']}**\n"
            f"Doublons détectés : **{report['duplicate_keys']}**",
            ephemeral=True,
        )
