import asyncio
import logging
from datetime import timezone
from logging.handlers import RotatingFileHandler

import discord
from discord.ext import commands

import config
from commands import setup_commands
from admin_commands import setup_admin_commands
from database import Database
from parser import normalize_name, parse_all_matches
from stats import format_live_leaderboard_blocks


def setup_logging() -> None:
    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)

    handlers = [console]
    if config.LOG_TO_FILE:
        file_handler = RotatingFileHandler(
            config.LOG_FILE,
            maxBytes=512_000,
            backupCount=1,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    for name in (
        "__main__", "database", "commands", "parser", "stats",
        "views", "player_resolver", "ranking",
    ):
        app_logger = logging.getLogger(name)
        app_logger.setLevel(level)
        app_logger.handlers.clear()
        for handler in handlers:
            app_logger.addHandler(handler)
        app_logger.propagate = False


setup_logging()
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.members = True
intents.guilds = True

db = Database()
DISCORD_MSG_LIMIT = 2000


def _to_mysql_datetime(dt):
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class FTBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)
        self.db = db
        self._startup_done = False
        self._leaderboard_lock = asyncio.Lock()
        self._auto_sync_task: asyncio.Task | None = None

    async def setup_hook(self) -> None:
        setup_commands(self.tree, self.db)
        setup_admin_commands(self.tree, self.db)  # Phase 2: Commandes admin
        if config.GUILD_ID:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Commandes synchronisées pour le serveur %s", config.GUILD_ID)
        else:
            await self.tree.sync()
            logger.warning(
                "Commandes synchronisées globalement (peut prendre ~1h). "
                "Renseignez GUILD_ID dans .env pour une sync instantanée."
            )

    async def on_ready(self) -> None:
        logger.info("Connecté en tant que %s (id=%s)", self.user, self.user.id)
        if not self._startup_done:
            self._startup_done = True
            asyncio.create_task(self._startup_work())
        else:
            asyncio.create_task(self._resync_after_reconnect())

    async def _startup_work(self) -> None:
        try:
            await asyncio.to_thread(self.db.init_schema)
            await self.sync_all_guilds()
            await self.ensure_leaderboard_message()
            if self._auto_sync_task is None or self._auto_sync_task.done():
                self._auto_sync_task = asyncio.create_task(self._auto_sync_loop())
            logger.info(
                "Démarrage terminé — sync auto toutes les %ds",
                config.AUTO_SYNC_SECONDS,
            )
        except Exception:
            logger.exception("Erreur durant le démarrage (le bot reste connecté)")

    async def _resync_after_reconnect(self) -> None:
        try:
            logger.info("Reconnexion — resynchronisation Discord…")
            await self.sync_all_guilds()
            await self.ensure_leaderboard_message()
        except Exception:
            logger.exception("Erreur resync après reconnexion")

    async def _auto_sync_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(config.AUTO_SYNC_SECONDS)
            try:
                await self.sync_all_guilds(quiet=True)
            except Exception:
                logger.exception("Erreur sync automatique")

    def _iter_target_guilds(self) -> list[discord.Guild]:
        if config.GUILD_ID:
            guild = self.get_guild(config.GUILD_ID)
            return [guild] if guild else []
        return list(self.guilds)

    async def sync_all_guilds(self, *, quiet: bool = False) -> None:
        for guild in self._iter_target_guilds():
            await self.sync_guild_scores(guild, quiet=quiet)

    def get_channel_by_name(self, guild: discord.Guild, name: str) -> discord.TextChannel | None:
        target = name.lower().replace("#", "")
        for channel in guild.text_channels:
            if channel.name.lower() == target:
                return channel
        return None

    def get_score_region(self, message: discord.Message) -> str | None:
        """
        Retourne BZ/PN si le message est dans un salon scores régional,
        '' si salon scores générique, None si ce n'est pas un salon scores.
        """
        if not message.guild:
            return None

        channel = message.channel
        channel_id = channel.id
        parent_id = channel.parent_id if isinstance(channel, discord.Thread) else None

        for region, channel_name in config.score_channels():
            scores_channel = self.get_channel_by_name(message.guild, channel_name)
            if not scores_channel:
                continue
            if channel_id == scores_channel.id or parent_id == scores_channel.id:
                return region or ""
        return None

    def is_scores_channel(self, message: discord.Message) -> bool:
        return self.get_score_region(message) is not None

    def channel_is_scores(self, guild: discord.Guild, channel: discord.abc.GuildChannel) -> bool:
        channel_id = channel.id
        parent_id = channel.parent_id if isinstance(channel, discord.Thread) else None
        for _, channel_name in config.score_channels():
            scores_channel = self.get_channel_by_name(guild, channel_name)
            if not scores_channel:
                continue
            if channel_id == scores_channel.id or parent_id == scores_channel.id:
                return True
        return False

    def _resolve_discord_id_sync(
        self, guild: discord.Guild, player_name: str
    ) -> int | None:
        normalized = player_name.strip().lower()
        for member in guild.members:
            if member.display_name.lower() == normalized:
                return member.id
            if member.name.lower() == normalized:
                return member.id
            if member.global_name and member.global_name.lower() == normalized:
                return member.id
        return None

    def _load_leaderboard_message_ids(self, guild_id: int) -> list[int]:
        key = f"leaderboard_message_ids_{guild_id}"
        raw = self.db.get_setting(key)
        if raw:
            ids = []
            for part in raw.split(","):
                part = part.strip()
                if part.isdigit():
                    ids.append(int(part))
            if ids:
                return ids

        legacy_key = f"leaderboard_message_id_{guild_id}"
        legacy = self.db.get_setting(legacy_key)
        if legacy and legacy.isdigit():
            return [int(legacy)]
        return []

    def _save_leaderboard_message_ids(self, guild_id: int, message_ids: list[int]) -> None:
        key = f"leaderboard_message_ids_{guild_id}"
        self.db.set_setting(key, ",".join(str(mid) for mid in message_ids))

    def _parsed_signature(self, parsed_list) -> list[tuple]:
        return [
            (
                index,
                normalize_name(parsed.player1),
                normalize_name(parsed.player2),
                parsed.score1,
                parsed.score2,
                parsed.ft_type,
                parsed.winner_side,
            )
            for index, parsed in enumerate(parsed_list)
        ]

    def _sync_message_scores_sync(
        self,
        message: discord.Message,
        *,
        score_region: str | None = None,
    ) -> bool:
        """Synchronise les matchs d'un message Discord. Retourne True si modifié."""
        if message.author.bot:
            return False

        if message.created_at < config.START_DATE:
            return False

        parsed_list = parse_all_matches(message.content or "")
        new_signature = self._parsed_signature(parsed_list)
        old_signature = self.db.get_message_match_signature(message.id)

        if new_signature == old_signature:
            return False

        if old_signature:
            deleted = self.db.delete_matches_by_message(message.id)
            logger.info(
                "Message %s resynchronisé : %d ancien(s) match(s) retiré(s)",
                message.id,
                deleted,
            )

        if not parsed_list:
            return bool(old_signature)

        season = self.db.get_active_season()
        if not season:
            logger.error("Aucune saison active")
            return bool(old_signature)

        guild = message.guild
        match_created_at = _to_mysql_datetime(message.created_at)
        inserted = 0

        for match_index, parsed in enumerate(parsed_list):
            discord_id_1 = self._resolve_discord_id_sync(guild, parsed.player1) if guild else None
            discord_id_2 = self._resolve_discord_id_sync(guild, parsed.player2) if guild else None

            for mention in message.mentions:
                name_lower = mention.display_name.lower()
                if (
                    parsed.player1.lower() in name_lower
                    or name_lower in parsed.player1.lower()
                ):
                    discord_id_1 = mention.id
                if (
                    parsed.player2.lower() in name_lower
                    or name_lower in parsed.player2.lower()
                ):
                    discord_id_2 = mention.id

            player1 = self.db.get_or_create_player(parsed.player1, discord_id_1)
            player2 = self.db.get_or_create_player(parsed.player2, discord_id_2)

            if score_region in config.VALID_REGIONS:
                self.db.set_player_region(player1["id"], score_region)
                self.db.set_player_region(player2["id"], score_region)

            winner_id = player1["id"] if parsed.winner_side == 1 else player2["id"]

            if self.db.insert_match(
                message_id=message.id,
                match_index=match_index,
                season_id=season["id"],
                player1_id=player1["id"],
                player2_id=player2["id"],
                score1=parsed.score1,
                score2=parsed.score2,
                ft_type=parsed.ft_type,
                winner_id=winner_id,
                created_at=match_created_at,
            ):
                inserted += 1
                winner_name = (
                    player1["name"] if parsed.winner_side == 1 else player2["name"]
                )
                logger.info(
                    "Match synchronisé: %s %s-%s %s → %s (FT%d)",
                    parsed.player1,
                    parsed.score1,
                    parsed.score2,
                    parsed.player2,
                    winner_name,
                    parsed.ft_type,
                )

        logger.info(
            "Message %s : %d/%d match(s) synchronisé(s)",
            message.id,
            inserted,
            len(parsed_list),
        )
        return True

    async def _apply_score_changes(self, guild: discord.Guild | None) -> None:
        season = self.db.get_active_season()
        if not season:
            return
        await asyncio.to_thread(self.db.recalculate_season_elo, season["id"])
        if guild:
            await self.update_leaderboard_message(guild)

    async def sync_score_message(
        self, message: discord.Message, *, from_history: bool = False
    ) -> bool:
        score_region = self.get_score_region(message)
        if score_region is None:
            return False

        region_for_db = score_region if score_region in config.VALID_REGIONS else None
        changed = await asyncio.to_thread(
            self._sync_message_scores_sync,
            message,
            score_region=region_for_db,
        )
        if changed and not from_history:
            await self._apply_score_changes(message.guild)
        return changed

    async def handle_score_message_deleted(
        self, message_id: int, guild: discord.Guild | None
    ) -> None:
        deleted = await asyncio.to_thread(
            self.db.delete_matches_by_message, message_id
        )
        if not deleted:
            return
        logger.info(
            "Message %s supprimé sur Discord : %d match(s) retiré(s)",
            message_id,
            deleted,
        )
        await self._apply_score_changes(guild)

    async def sync_guild_scores(
        self, guild: discord.Guild, *, quiet: bool = False
    ) -> None:
        discord_message_ids: set[int] = set()
        sync_count = 0

        for score_region, channel_name in config.score_channels():
            channel = self.get_channel_by_name(guild, channel_name)
            if not channel:
                if not quiet:
                    logger.warning(
                        "Salon #%s introuvable sur %s", channel_name, guild.name
                    )
                continue

            if not quiet:
                region_label = score_region or "général"
                logger.info(
                    "Sync #%s (%s) sur %s depuis %s…",
                    channel.name,
                    region_label,
                    guild.name,
                    config.START_DATE.date(),
                )

            processed = 0
            channels_to_scan: list[discord.abc.Messageable] = [channel]
            channels_to_scan.extend(channel.threads)
            try:
                async for archived in channel.archived_threads(limit=None):
                    channels_to_scan.append(archived)
            except (discord.Forbidden, discord.HTTPException):
                pass

            try:
                for scan_channel in channels_to_scan:
                    async for message in scan_channel.history(
                        limit=None,
                        after=config.START_DATE,
                        oldest_first=True,
                    ):
                        if message.author.bot:
                            continue
                        discord_message_ids.add(message.id)
                        processed += 1
                        if await self.sync_score_message(message, from_history=True):
                            sync_count += 1
                        if processed % 10 == 0:
                            await asyncio.sleep(0.05)
            except discord.Forbidden:
                logger.error(
                    "Permission manquante pour lire #%s", channel.name
                )
            except Exception:
                logger.exception("Erreur sync #%s", channel.name)

            if not quiet:
                logger.info("Sync #%s terminée (%d messages)", channel.name, processed)

        season = self.db.get_active_season()
        if season and discord_message_ids:
            removed = await asyncio.to_thread(
                self.db.delete_matches_not_in_message_ids,
                season["id"],
                discord_message_ids,
            )
            if removed:
                if not quiet:
                    logger.info(
                        "%d match(s) orphelin(s) supprimé(s)", removed
                    )
                sync_count += removed

        if sync_count:
            if quiet:
                logger.info(
                    "Sync auto : %d changement(s) sur %s — classements mis à jour",
                    sync_count,
                    guild.name,
                )
            await self._apply_score_changes(guild)
        elif not quiet:
            await self.update_leaderboard_message(guild)

        if season and not quiet:
            logger.info(
                "Sync %s : %d matchs, %d joueurs",
                guild.name,
                self.db.count_season_matches(season["id"]),
                self.db.count_season_players(season["id"]),
            )

    async def scan_history(self) -> None:
        """Compatibilité — resync complète."""
        await self.sync_all_guilds()

    async def ensure_leaderboard_message(self) -> None:
        for guild in self.guilds:
            await self.update_leaderboard_message(guild)

    async def update_leaderboard_message(
        self, guild: discord.Guild, *, force_create: bool = False
    ) -> None:
        async with self._leaderboard_lock:
            channel = self.get_channel_by_name(guild, config.LEADERBOARD_CHANNEL)
            if not channel:
                logger.warning(
                    "Salon #%s introuvable sur %s", config.LEADERBOARD_CHANNEL, guild.name
                )
                return

            season = self.db.get_active_season()
            if not season:
                return

            blocks = format_live_leaderboard_blocks(self.db, season["id"])
            stored_ids = [] if force_create else self._load_leaderboard_message_ids(guild.id)
            updated_ids: list[int] = []

            for index, content in enumerate(blocks):
                if len(content) > DISCORD_MSG_LIMIT:
                    content = content[: DISCORD_MSG_LIMIT - 3] + "..."

                if index < len(stored_ids) and not force_create:
                    try:
                        msg = await channel.fetch_message(stored_ids[index])
                        await msg.edit(content=content)
                        updated_ids.append(msg.id)
                        continue
                    except (discord.NotFound, discord.Forbidden, ValueError):
                        logger.warning(
                            "Message classement %s introuvable, recréation...",
                            stored_ids[index],
                        )
                    except discord.HTTPException as exc:
                        logger.error(
                            "Impossible d'éditer le classement (%s) : %s",
                            stored_ids[index],
                            exc,
                        )

                try:
                    msg = await channel.send(content)
                    updated_ids.append(msg.id)
                except discord.Forbidden:
                    logger.error("Permission manquante pour écrire dans #%s", channel.name)
                    return
                except discord.HTTPException as exc:
                    logger.error("Impossible d'envoyer le classement : %s", exc)
                    return

            self._save_leaderboard_message_ids(guild.id, updated_ids)

            for orphan_id in stored_ids[len(updated_ids) :]:
                try:
                    orphan = await channel.fetch_message(orphan_id)
                    await orphan.delete()
                    logger.info("Ancien message classement supprimé (%s)", orphan_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

            logger.info(
                "Classements mis à jour dans #%s (%d section(s) : BZ + PN)",
                channel.name,
                len(updated_ids),
            )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if self.is_scores_channel(message):
            await self.sync_score_message(message)

        await self.process_commands(message)

    async def on_message_delete(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self.is_scores_channel(message):
            return
        await self.handle_score_message_deleted(message.id, message.guild)

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.author.bot:
            return
        if before.content == after.content:
            return
        if self.is_scores_channel(after):
            await self.sync_score_message(after)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if not payload.guild_id:
            return
        guild = self.get_guild(payload.guild_id)
        if not guild:
            return
        channel = guild.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(payload.channel_id)
            except discord.HTTPException:
                return
        if not self.channel_is_scores(guild, channel):
            return
        await self.handle_score_message_deleted(payload.message_id, guild)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if not payload.guild_id or "content" not in payload.data:
            return
        guild = self.get_guild(payload.guild_id)
        if not guild:
            return
        channel = guild.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(payload.channel_id)
            except discord.HTTPException:
                return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        if self.get_score_region(message) is not None:
            await self.sync_score_message(message)


def main() -> None:
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN manquant dans .env")

    bot = FTBot()
    try:
        bot.run(config.DISCORD_TOKEN)
    except discord.PrivilegedIntentsRequired:
        print(
            "\n=== ERREUR : Intents privilégies non actives ===\n"
            "1. Ouvrez https://discord.com/developers/applications\n"
            "2. Votre application → onglet Bot\n"
            "3. Activez :\n"
            "   - PRESENCE INTENT (optionnel)\n"
            "   - SERVER MEMBERS INTENT  ← obligatoire\n"
            "   - MESSAGE CONTENT INTENT ← obligatoire\n"
            "4. Cliquez Save Changes\n"
            "5. Relancez le bot (py -3 bot.py)\n"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
