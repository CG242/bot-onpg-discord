import logging
from datetime import date, datetime, time, timezone

import discord
from discord import app_commands

import config
from database import Database
from player_resolver import resolve_player_input
from ranking import elo_for_tier, format_tier
from stats import format_comparison, format_leaderboard, format_live_leaderboard_blocks, format_player_stats
from views import PlayerNotFoundView, RankManageView, RegionManageView

logger = logging.getLogger(__name__)

TIER_CHOICES = [
    app_commands.Choice(name=t, value=t) for t in config.VALID_TIERS if t != "NR"
]


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


def parse_period(
    date_debut: str | None, date_fin: str | None
) -> tuple[datetime | None, datetime | None, str | None]:
    try:
        d1 = (
            datetime.strptime(date_debut, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if date_debut
            else None
        )
        d2 = (
            datetime.strptime(date_fin, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
            if date_fin
            else None
        )
        return d1, d2, None
    except ValueError:
        return None, None, "Date invalide. Format : **AAAA-MM-JJ**"


def build_aide_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Bot Manager FT",
        description=(
            "Le bot lit les scores postés dans **#scores-ft-congo**, "
            "calcule les **FT gagnés** et met à jour les classements **automatiquement**."
        ),
        color=0x2B2D31,
    )
    embed.add_field(
        name="Comment ça marche ?",
        value=(
            "1. Postez `JoueurA 5 - 1 JoueurB` dans #scores\n"
            "2. Le bot enregistre le match automatiquement\n"
            "3. Les **points** évoluent à chaque match (+/- selon FT)\n"
            "4. L'**admin** attribue le **rang** (S+→B) — les points de base suivent le rang"
        ),
        inline=False,
    )
    embed.add_field(
        name="Classements",
        value=(
            "`/classement` — officiel BZ + PN\n"
            "`/classement-bz` · `/classement-pn` — une ville\n"
            "Le salon `#classement` se met à jour **automatiquement** à chaque score"
        ),
        inline=True,
    )
    embed.add_field(
        name="Joueurs",
        value=(
            "`/stats pseudo:Leleo242` — fiche complète\n"
            "`/compare` — duel entre 2 pseudos\n"
            "`/link` — lier Discord ↔ pseudo"
        ),
        inline=True,
    )
    embed.add_field(
        name="Rangs & régions (admin)",
        value=(
            "`/set-rang` — rang manuel + points\n"
            "`/rang-attribuer` — menu multi-joueurs\n"
            "`/region-definir` — admin : pseudo → BZ/PN\n"
            "`/region-ajouter` · `/region-retirer` — menus\n"
            "`/set-region` — sa propre région"
        ),
        inline=True,
    )
    embed.set_footer(text="Pas besoin d'être sur Discord : le pseudo des scores suffit.")
    return embed


def _resolve(joueur, db: Database, season_id: int):
    result = resolve_player_input(db, joueur, season_id)
    if result.found and result.player:
        return result.player, db.player_display_name(result.player)
    label = result.label or str(joueur)
    return None, label


def setup_commands(tree: app_commands.CommandTree, db: Database) -> None:
    @tree.command(name="aide", description="Guide rapide du bot")
    async def aide_cmd(interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=build_aide_embed(), ephemeral=True
        )

    async def _send_classement(
        interaction: discord.Interaction,
        *,
        region: str | None = None,
        title: str = "CLASSEMENT OFFICIEL",
        date_debut: str | None = None,
        date_fin: str | None = None,
    ):
        season = db.get_active_season()
        if not season:
            await interaction.response.send_message(
                "Aucune saison active.", ephemeral=True
            )
            return

        d1, d2, err = parse_period(date_debut, date_fin)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        content = format_leaderboard(
            db,
            season["id"],
            region=region,
            title=title or (config.official_region_title(region) if region else "CLASSEMENT OFFICIEL"),
            date_debut=d1,
            date_fin=d2,
        )
        await interaction.response.send_message(content)

    async def _send_classement_all_regions(
        interaction: discord.Interaction,
        *,
        date_debut: str | None = None,
        date_fin: str | None = None,
    ):
        season = db.get_active_season()
        if not season:
            await interaction.response.send_message(
                "Aucune saison active.", ephemeral=True
            )
            return

        d1, d2, err = parse_period(date_debut, date_fin)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        blocks = [
            format_leaderboard(
                db,
                season["id"],
                region=region,
                title=config.official_region_title(region),
                date_debut=d1,
                date_fin=d2,
            )
            for region in config.VALID_REGIONS
        ]
        await interaction.response.send_message(blocks[0])
        for block in blocks[1:]:
            await interaction.followup.send(block)

    @tree.command(name="classement", description="Classements officiels BZ et PN")
    @app_commands.describe(
        date_debut="Début période AAAA-MM-JJ (optionnel)",
        date_fin="Fin période AAAA-MM-JJ (optionnel)",
    )
    async def classement(
        interaction: discord.Interaction,
        date_debut: str | None = None,
        date_fin: str | None = None,
    ):
        await _send_classement_all_regions(
            interaction, date_debut=date_debut, date_fin=date_fin
        )

    @tree.command(name="classement-bz", description="Classement officiel BZ")
    @app_commands.describe(
        date_debut="Début période AAAA-MM-JJ",
        date_fin="Fin période AAAA-MM-JJ",
    )
    async def classement_bz(
        interaction: discord.Interaction,
        date_debut: str | None = None,
        date_fin: str | None = None,
    ):
        await _send_classement(
            interaction,
            region="BZ",
            title=config.official_region_title("BZ"),
            date_debut=date_debut,
            date_fin=date_fin,
        )

    @tree.command(name="classement-pn", description="Classement officiel PN")
    @app_commands.describe(
        date_debut="Début période AAAA-MM-JJ",
        date_fin="Fin période AAAA-MM-JJ",
    )
    async def classement_pn(
        interaction: discord.Interaction,
        date_debut: str | None = None,
        date_fin: str | None = None,
    ):
        await _send_classement(
            interaction,
            region="PN",
            title=config.official_region_title("PN"),
            date_debut=date_debut,
            date_fin=date_fin,
        )

    @tree.command(name="stats", description="Fiche joueur (pseudo ou membre)")
    @app_commands.describe(
        pseudo="Pseudo des scores",
        membre="Membre Discord (prioritaire)",
    )
    async def stats_cmd(
        interaction: discord.Interaction,
        pseudo: str,
        membre: discord.Member | None = None,
    ):
        season = db.get_active_season()
        if not season:
            await interaction.response.send_message(
                "Aucune saison active.", ephemeral=True
            )
            return

        target = membre if membre else pseudo
        result = resolve_player_input(db, target, season["id"])

        if result.found and result.player:
            stats = db.get_player_stats(result.player["id"], season["id"])
            await interaction.response.send_message(
                format_player_stats(stats, result.label)
            )
            return

        search = result.search_text or pseudo
        view = PlayerNotFoundView(db, season["id"], search, interaction.user.id)
        await interaction.response.send_message(
            f"**{search}** introuvable.\nCréer ou lier à un Discord :",
            view=view,
            ephemeral=True,
        )

    @tree.command(name="compare", description="Duel entre 2 joueurs")
    @app_commands.describe(joueur_a="Pseudo A", joueur_b="Pseudo B")
    async def compare_cmd(
        interaction: discord.Interaction, joueur_a: str, joueur_b: str
    ):
        season = db.get_active_season()
        if not season:
            await interaction.response.send_message(
                "Aucune saison active.", ephemeral=True
            )
            return

        res_a = resolve_player_input(db, joueur_a, season["id"])
        res_b = resolve_player_input(db, joueur_b, season["id"])
        if not res_a.found:
            await interaction.response.send_message(
                f"Joueur A introuvable : **{joueur_a}**", ephemeral=True
            )
            return
        if not res_b.found:
            await interaction.response.send_message(
                f"Joueur B introuvable : **{joueur_b}**", ephemeral=True
            )
            return
        if res_a.player["id"] == res_b.player["id"]:
            await interaction.response.send_message(
                "Deux joueurs différents requis.", ephemeral=True
            )
            return

        matches = db.get_head_to_head(
            res_a.player["id"], res_b.player["id"], season["id"]
        )
        await interaction.response.send_message(
            format_comparison(res_a.player, res_b.player, matches, db)
        )

    @tree.command(name="set-rang", description="Admin : attribuer un rang manuel")
    @app_commands.describe(
        pseudo="Pseudo du joueur",
        rang="Rang tier (S+, S, A+, A, B+, B)",
        membre="Ou membre Discord",
    )
    @app_commands.choices(rang=TIER_CHOICES)
    async def set_rang_cmd(
        interaction: discord.Interaction,
        rang: app_commands.Choice[str],
        pseudo: str | None = None,
        membre: discord.Member | None = None,
    ):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée.", ephemeral=True
            )
            return

        season = db.get_active_season()
        target = membre if membre else pseudo
        if not target:
            await interaction.response.send_message(
                "Indiquez un pseudo ou membre.", ephemeral=True
            )
            return

        player, label = _resolve(target, db, season["id"] if season else None)
        if not player:
            await interaction.response.send_message(
                f"Joueur introuvable : **{label}**", ephemeral=True
            )
            return

        pts = db.set_player_rank(player["id"], rang.value, manual=True)
        await interaction.response.send_message(
            f"**{db.player_display_name(player)}** → rang **{rang.value}** "
            f"({format_tier(rang.value)}) · Points **{pts}**\n"
            f"_Les points évolueront ensuite avec les matchs._",
            ephemeral=True,
        )

    @tree.command(name="rang-attribuer", description="Admin : menu rang multi-joueurs")
    @app_commands.describe(rang="Rang à attribuer")
    @app_commands.choices(rang=TIER_CHOICES)
    async def rang_attribuer(
        interaction: discord.Interaction, rang: app_commands.Choice[str]
    ):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée.", ephemeral=True
            )
            return

        season = db.get_active_season()
        if not season:
            await interaction.response.send_message(
                "Aucune saison active.", ephemeral=True
            )
            return

        players = db.list_season_players(season["id"])
        if not players:
            await interaction.response.send_message(
                "Aucun joueur.", ephemeral=True
            )
            return

        view = RankManageView(
            db, rang.value, players, interaction.user.id
        )
        await interaction.response.send_message(
            f"Attribuer **{rang.value}** ({elo_for_tier(rang.value)} points) — "
            f"sélectionnez joueur(s) :",
            view=view,
            ephemeral=True,
        )

    @tree.command(name="set-region", description="Définir votre région (BZ ou PN)")
    @app_commands.describe(
        region="Région",
        pseudo="Pseudo score (admin : pour un autre joueur)",
        membre="Membre Discord (prioritaire)",
    )
    @app_commands.choices(
        region=[
            app_commands.Choice(name="BZ", value="BZ"),
            app_commands.Choice(name="PN", value="PN"),
        ]
    )
    async def set_region_cmd(
        interaction: discord.Interaction,
        region: app_commands.Choice[str],
        pseudo: str | None = None,
        membre: discord.Member | None = None,
    ):
        target = membre if membre else (pseudo if pseudo else interaction.user)
        if pseudo and not membre and not is_admin(interaction):
            await interaction.response.send_message(
                "Seul un admin peut définir la région d'un autre joueur via pseudo.",
                ephemeral=True,
            )
            return

        season = db.get_active_season()
        if isinstance(target, discord.Member):
            player = db.get_player_by_discord_id(target.id)
            if not player:
                player = db.get_or_create_player(target.display_name, target.id)
        else:
            result = resolve_player_input(
                db, target, season["id"] if season else None
            )
            if result.found and result.player:
                player = result.player
            elif isinstance(target, str):
                await interaction.response.send_message(
                    f"Joueur introuvable : **{target}**", ephemeral=True
                )
                return
            else:
                player = db.get_or_create_player(
                    interaction.user.display_name, interaction.user.id
                )

        db.set_player_region(player["id"], region.value)
        await interaction.response.send_message(
            f"Région **{region.value}** → **{db.player_display_name(player)}**.",
            ephemeral=True,
        )
        await _refresh_leaderboard(interaction)

    @tree.command(
        name="region-definir",
        description="Admin : assigner BZ/PN à un joueur (pseudo ou membre)",
    )
    @app_commands.describe(
        region="Région",
        pseudo="Pseudo des scores",
        membre="Membre Discord (prioritaire)",
    )
    @app_commands.choices(
        region=[
            app_commands.Choice(name="BZ", value="BZ"),
            app_commands.Choice(name="PN", value="PN"),
        ]
    )
    async def region_definir_cmd(
        interaction: discord.Interaction,
        region: app_commands.Choice[str],
        pseudo: str | None = None,
        membre: discord.Member | None = None,
    ):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée.", ephemeral=True
            )
            return
        if not pseudo and not membre:
            await interaction.response.send_message(
                "Indiquez un **pseudo** ou un **membre**.", ephemeral=True
            )
            return

        season = db.get_active_season()
        target = membre if membre else pseudo
        player, label = _resolve(target, db, season["id"] if season else None)
        if not player:
            await interaction.response.send_message(
                f"Joueur introuvable : **{label}**", ephemeral=True
            )
            return

        db.set_player_region(player["id"], region.value)
        await interaction.response.send_message(
            f"**{db.player_display_name(player)}** → région **{region.value}**.",
            ephemeral=True,
        )
        await _refresh_leaderboard(interaction)

    @tree.command(name="region-ajouter", description="Admin : assigner région (menu)")
    @app_commands.choices(
        region=[
            app_commands.Choice(name="BZ", value="BZ"),
            app_commands.Choice(name="PN", value="PN"),
        ]
    )
    async def region_ajouter(
        interaction: discord.Interaction, region: app_commands.Choice[str]
    ):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée.", ephemeral=True
            )
            return
        season = db.get_active_season()
        if not season:
            await interaction.response.send_message(
                "Aucune saison active.", ephemeral=True
            )
            return
        players = db.list_all_players()
        if not players:
            await interaction.response.send_message(
                "Aucun joueur en base. Postez des scores d'abord.", ephemeral=True
            )
            return
        view = RegionManageView(
            db, region.value, "ajouter", players, interaction.user.id
        )
        await interaction.response.send_message(
            f"Région **{region.value}** — choisir joueur(s) :",
            view=view,
            ephemeral=True,
        )

    @tree.command(name="region-retirer", description="Admin : retirer région (menu)")
    @app_commands.choices(
        region=[
            app_commands.Choice(name="BZ", value="BZ"),
            app_commands.Choice(name="PN", value="PN"),
            app_commands.Choice(name="Toutes", value="ALL"),
        ]
    )
    async def region_retirer(
        interaction: discord.Interaction, region: app_commands.Choice[str]
    ):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée.", ephemeral=True
            )
            return
        season = db.get_active_season()
        if not season:
            await interaction.response.send_message(
                "Aucune saison active.", ephemeral=True
            )
            return
        if region.value == "ALL":
            players = [
                p for p in db.list_season_players(season["id"]) if p.get("region")
            ]
            label = "toutes régions"
        else:
            players = db.list_players_by_region(season["id"], region.value)
            label = region.value
        if not players:
            await interaction.response.send_message(
                f"Aucun joueur ({label}).", ephemeral=True
            )
            return
        view = RegionManageView(
            db, None, "retirer", players, interaction.user.id
        )
        await interaction.response.send_message(
            f"Retirer région **{label}** :", view=view, ephemeral=True
        )

    @tree.command(name="leaderboard-live", description="Classement temps réel")
    async def leaderboard_live(interaction: discord.Interaction):
        season = db.get_active_season()
        if not season:
            await interaction.response.send_message(
                "Aucune saison active.", ephemeral=True
            )
            return
        blocks = format_live_leaderboard_blocks(db, season["id"])
        await interaction.response.send_message(blocks[0])
        for block in blocks[1:]:
            await interaction.followup.send(block)

    @tree.command(name="reset-saison", description="Admin : nouvelle saison")
    async def reset_saison(
        interaction: discord.Interaction,
        nom: str,
        date_debut: str | None = None,
    ):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée.", ephemeral=True
            )
            return
        try:
            start = date.fromisoformat(date_debut) if date_debut else date.today()
        except ValueError:
            await interaction.response.send_message(
                "Date invalide.", ephemeral=True
            )
            return
        sid = db.reset_season(nom, start)
        await interaction.response.send_message(f"Saison **{nom}** créée (id={sid}).")

    @tree.command(name="link", description="Lier Discord à un pseudo")
    async def link_player(interaction: discord.Interaction, pseudo: str):
        season = db.get_active_season()
        if db.get_player_by_discord_id(interaction.user.id):
            await interaction.response.send_message(
                "Compte déjà lié.", ephemeral=True
            )
            return
        result = resolve_player_input(
            db, pseudo, season["id"] if season else None
        )
        if result.found and result.player:
            p = result.player
            if p.get("discord_id") and p["discord_id"] != interaction.user.id:
                await interaction.response.send_message(
                    "Pseudo déjà lié.", ephemeral=True
                )
                return
            db.link_discord_id_force(p["id"], interaction.user.id)
            player = p
        else:
            player = db.get_or_create_player(pseudo, interaction.user.id)
        await interaction.response.send_message(
            f"Lié à **{db.player_display_name(player)}**.", ephemeral=True
        )

    @tree.command(name="resync-scores", description="Admin : relire les scores")
    async def resync_scores(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Permission refusée.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        bot = interaction.client
        if hasattr(bot, "scan_history"):
            await bot.scan_history()
            await bot.ensure_leaderboard_message()
            season = db.get_active_season()
            if season:
                await interaction.followup.send(
                    f"OK — **{db.count_season_matches(season['id'])}** matchs, "
                    f"**{db.count_season_players(season['id'])}** joueurs.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send("Resync OK.", ephemeral=True)
        else:
            await interaction.followup.send("Indisponible.", ephemeral=True)
