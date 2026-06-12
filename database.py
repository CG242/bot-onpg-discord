import json
import logging
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import mysql.connector
from mysql.connector import Error

import config
from parser import (
    is_valid_player_name,
    normalize_key,
    normalize_name,
    pick_display_name,
    sanitize_player_name,
)
from ranking import base_elo_for_player, compute_match_elo_changes, elo_for_tier

logger = logging.getLogger(__name__)


class Database:
    def __init__(self) -> None:
        self._config = {
            "host": config.MYSQL_HOST,
            "user": config.MYSQL_USER,
            "password": config.MYSQL_PASSWORD,
            "database": config.MYSQL_DATABASE,
            "autocommit": False,
        }

    @contextmanager
    def _session(self, dictionary: bool = False):
        conn = mysql.connector.connect(**self._config, buffered=True)
        cursor = conn.cursor(dictionary=dictionary, buffered=True)
        try:
            yield conn, cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    def _index_exists(self, cursor, table: str, key_name: str) -> bool:
        cursor.execute(f"SHOW INDEX FROM `{table}` WHERE Key_name = %s", (key_name,))
        return len(cursor.fetchall()) > 0

    def _column_exists(self, cursor, table: str, column: str) -> bool:
        cursor.execute(
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
            """,
            (config.MYSQL_DATABASE, table, column),
        )
        row = cursor.fetchone()
        return bool(row and row[0] > 0)

    def _migrate_schema(self, cursor) -> None:
        if not self._column_exists(cursor, "matches", "match_index"):
            cursor.execute(
                "ALTER TABLE matches ADD COLUMN match_index INT NOT NULL DEFAULT 0"
            )
            logger.info("Migration: colonne match_index ajoutée")

        if self._index_exists(cursor, "matches", "message_id"):
            cursor.execute("ALTER TABLE matches DROP INDEX message_id")
            logger.info("Migration: ancien index message_id supprimé")

        if not self._index_exists(cursor, "matches", "unique_message_match"):
            cursor.execute(
                """
                ALTER TABLE matches
                ADD UNIQUE KEY unique_message_match (message_id, match_index)
                """
            )
            logger.info("Migration: index unique (message_id, match_index) créé")

        if not self._column_exists(cursor, "players", "region"):
            cursor.execute(
                "ALTER TABLE players ADD COLUMN region VARCHAR(2) NULL DEFAULT NULL"
            )
            logger.info("Migration: colonne region ajoutée")

        if not self._column_exists(cursor, "players", "tier_rank"):
            cursor.execute(
                "ALTER TABLE players ADD COLUMN tier_rank VARCHAR(5) NOT NULL DEFAULT 'NR'"
            )
            logger.info("Migration: colonne tier_rank ajoutée")

        if not self._column_exists(cursor, "players", "elo"):
            cursor.execute(
                "ALTER TABLE players ADD COLUMN elo INT NOT NULL DEFAULT 1000"
            )
            logger.info("Migration: colonne elo ajoutée")

        if not self._column_exists(cursor, "players", "rank_manual"):
            cursor.execute(
                "ALTER TABLE players ADD COLUMN rank_manual TINYINT(1) NOT NULL DEFAULT 0"
            )
            logger.info("Migration: colonne rank_manual ajoutée")

        if not self._column_exists(cursor, "seasons", "end_date"):
            cursor.execute("ALTER TABLE seasons ADD COLUMN end_date DATE NULL")
            logger.info("Migration: colonne end_date ajoutée")

        if not self._column_exists(cursor, "seasons", "status"):
            cursor.execute(
                "ALTER TABLE seasons ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active'"
            )
            logger.info("Migration: colonne status ajoutée")
            cursor.execute(
                "UPDATE seasons SET status = 'archived' WHERE is_active = 0"
            )
            cursor.execute(
                "UPDATE seasons SET status = 'active' WHERE is_active = 1"
            )

        if not self._column_exists(cursor, "seasons", "data_reset_at"):
            cursor.execute(
                "ALTER TABLE seasons ADD COLUMN data_reset_at DATETIME NULL"
            )
            logger.info("Migration: colonne data_reset_at ajoutée")

    def init_schema(self) -> None:
        try:
            with self._session() as (_, cursor):
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS seasons (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(100) NOT NULL,
                        start_date DATE NOT NULL,
                        is_active TINYINT(1) NOT NULL DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS players (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        discord_id BIGINT UNIQUE NULL,
                        name VARCHAR(255) NOT NULL,
                        normalized_name VARCHAR(191) NOT NULL,
                        region VARCHAR(2) NULL DEFAULT NULL,
                        tier_rank VARCHAR(5) NOT NULL DEFAULT 'NR',
                        elo INT NOT NULL DEFAULT 1000,
                        rank_manual TINYINT(1) NOT NULL DEFAULT 0,
                        UNIQUE KEY unique_normalized_name (normalized_name)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS matches (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        message_id BIGINT NOT NULL,
                        match_index INT NOT NULL DEFAULT 0,
                        season_id INT NOT NULL,
                        player1_id INT NOT NULL,
                        player2_id INT NOT NULL,
                        score1 INT NOT NULL,
                        score2 INT NOT NULL,
                        ft_type INT NOT NULL,
                        winner_id INT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE KEY unique_message_match (message_id, match_index),
                        FOREIGN KEY (season_id) REFERENCES seasons(id),
                        FOREIGN KEY (player1_id) REFERENCES players(id),
                        FOREIGN KEY (player2_id) REFERENCES players(id),
                        FOREIGN KEY (winner_id) REFERENCES players(id)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bot_settings (
                        setting_key VARCHAR(50) PRIMARY KEY,
                        setting_value VARCHAR(255) NOT NULL
                    )
                    """
                )
                self._migrate_schema(cursor)

            if self.get_active_season() is None:
                self.create_season("Saison 1", config.START_DATE.date())

            self.cleanup_corrupt_players()
            self._migrate_old_tiers()
            self.deduplicate_all_players()
        except Error as exc:
            logger.exception("Erreur init_schema: %s", exc)
            raise

    def _is_valid_player(self, player: dict[str, Any]) -> bool:
        return is_valid_player_name(player.get("name", ""))

    def player_display_name(self, player: dict[str, Any]) -> str:
        name = (player.get("name") or "").strip()
        if name:
            return name
        return "Inconnu"

    def _migrate_old_tiers(self) -> None:
        placeholders = ", ".join(["%s"] * len(config.VALID_TIERS))
        with self._session() as (_, cursor):
            cursor.execute(
                f"""
                UPDATE players SET tier_rank = 'NR'
                WHERE tier_rank NOT IN ({placeholders})
                """,
                config.VALID_TIERS,
            )

    def cleanup_corrupt_players(self) -> None:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute("SELECT id, name, normalized_name, discord_id FROM players")
            players = cursor.fetchall()

        fixed = 0
        deleted = 0
        for player in players:
            if self._is_valid_player(player):
                continue

            clean = sanitize_player_name(player["name"])
            normalized = normalize_name(clean) if clean else ""

            if clean and normalized:
                with self._session() as (_, cursor):
                    cursor.execute(
                        """
                        UPDATE players
                        SET name = %s, normalized_name = %s
                        WHERE id = %s
                        """,
                        (clean, normalized, player["id"]),
                    )
                fixed += 1
                continue

            with self._session() as (_, cursor):
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM matches
                    WHERE player1_id = %s OR player2_id = %s OR winner_id = %s
                    """,
                    (player["id"], player["id"], player["id"]),
                )
                match_count = cursor.fetchone()[0]

            if match_count == 0:
                with self._session() as (_, cursor):
                    cursor.execute("DELETE FROM players WHERE id = %s", (player["id"],))
                deleted += 1

        if fixed or deleted:
            logger.info(
                "Nettoyage joueurs: %d corrigés, %d supprimés", fixed, deleted
            )

    def get_setting(self, key: str) -> str | None:
        with self._session() as (_, cursor):
            cursor.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = %s",
                (key,),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._session() as (_, cursor):
            cursor.execute(
                """
                INSERT INTO bot_settings (setting_key, setting_value)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)
                """,
                (key, value),
            )

    def get_active_season(self) -> dict[str, Any] | None:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT * FROM seasons
                WHERE status = 'active' OR is_active = 1
                ORDER BY id DESC LIMIT 1
                """
            )
            return cursor.fetchone()

    def get_season_score_cutoff(self, season: dict[str, Any] | None):
        """Date/heure minimum pour importer un message Discord dans la saison."""
        from datetime import timezone as tz

        if not season:
            return config.START_DATE

        start = season.get("start_date") or config.START_DATE.date()
        if isinstance(start, datetime):
            start_dt = start if start.tzinfo else start.replace(tzinfo=tz.utc)
        else:
            start_dt = datetime.combine(start, datetime.min.time()).replace(
                tzinfo=tz.utc
            )

        reset_at = season.get("data_reset_at")
        if reset_at:
            if isinstance(reset_at, datetime):
                reset_dt = (
                    reset_at if reset_at.tzinfo else reset_at.replace(tzinfo=tz.utc)
                )
            else:
                reset_dt = datetime.combine(reset_at, datetime.min.time()).replace(
                    tzinfo=tz.utc
                )
            if reset_dt > start_dt:
                return reset_dt
        return start_dt

    def get_season(self, season_id: int) -> dict[str, Any] | None:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute("SELECT * FROM seasons WHERE id = %s", (season_id,))
            return cursor.fetchone()

    def list_archived_seasons(self) -> list[dict[str, Any]]:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT * FROM seasons
                WHERE status = 'archived' OR is_active = 0
                ORDER BY id DESC
                """
            )
            return cursor.fetchall()

    def archive_active_season(self, end_date: date | None = None) -> dict[str, Any] | None:
        active = self.get_active_season()
        if not active:
            return None
        end = end_date or date.today()
        with self._session() as (_, cursor):
            cursor.execute(
                """
                UPDATE seasons
                SET is_active = 0, status = 'archived', end_date = %s
                WHERE id = %s
                """,
                (end, active["id"]),
            )
        active["status"] = "archived"
        active["end_date"] = end
        active["is_active"] = 0
        return active

    def create_season(self, name: str, start_date) -> int:
        with self._session() as (_, cursor):
            cursor.execute(
                """
                UPDATE seasons
                SET is_active = 0, status = 'archived',
                    end_date = COALESCE(end_date, CURDATE())
                WHERE is_active = 1 OR status = 'active'
                """
            )
            if isinstance(start_date, date) and start_date > date.today():
                cursor.execute(
                    """
                    INSERT INTO seasons
                    (name, start_date, is_active, status, data_reset_at)
                    VALUES (%s, %s, 1, 'active', %s)
                    """,
                    (name, start_date, datetime.combine(start_date, datetime.min.time())),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO seasons
                    (name, start_date, is_active, status, data_reset_at)
                    VALUES (%s, %s, 1, 'active', UTC_TIMESTAMP())
                    """,
                    (name, start_date),
                )
            return cursor.lastrowid

    def start_new_season(self, name: str, start_date) -> tuple[dict[str, Any] | None, int]:
        archived = self.archive_active_season(end_date=start_date)
        new_id = self.create_season(name, start_date)
        return archived, new_id

    def reset_active_season_data(self) -> int:
        """Supprime les matchs de la saison active, remet les points de base."""
        season = self.get_active_season()
        if not season:
            return 0
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                "DELETE FROM matches WHERE season_id = %s", (season["id"],)
            )
            deleted = cursor.rowcount
            cursor.execute("SELECT * FROM players")
            for row in cursor.fetchall():
                base = base_elo_for_player(
                    row.get("tier_rank"), bool(row.get("rank_manual"))
                )
                cursor.execute(
                    "UPDATE players SET elo = %s WHERE id = %s",
                    (base, row["id"]),
                )
            cursor.execute(
                """
                UPDATE seasons SET data_reset_at = UTC_TIMESTAMP()
                WHERE id = %s
                """,
                (season["id"],),
            )
        logger.info(
            "Saison active %s réinitialisée (%d matchs supprimés)",
            season["id"],
            deleted,
        )
        return deleted

    def count_matches_by_message(self, message_id: int) -> int:
        with self._session() as (_, cursor):
            cursor.execute(
                "SELECT COUNT(*) FROM matches WHERE message_id = %s", (message_id,)
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def delete_matches_by_message(self, message_id: int) -> int:
        with self._session() as (_, cursor):
            cursor.execute(
                "DELETE FROM matches WHERE message_id = %s", (message_id,)
            )
            return cursor.rowcount

    def get_message_match_signature(self, message_id: int) -> list[tuple]:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT
                    m.match_index,
                    p1.normalized_name AS player1_norm,
                    p2.normalized_name AS player2_norm,
                    m.score1,
                    m.score2,
                    m.ft_type,
                    CASE WHEN m.winner_id = m.player1_id THEN 1 ELSE 2 END AS winner_side
                FROM matches m
                JOIN players p1 ON p1.id = m.player1_id
                JOIN players p2 ON p2.id = m.player2_id
                WHERE m.message_id = %s
                ORDER BY m.match_index ASC
                """,
                (message_id,),
            )
            rows = cursor.fetchall()
        return [
            (
                int(r["match_index"]),
                r["player1_norm"],
                r["player2_norm"],
                int(r["score1"]),
                int(r["score2"]),
                int(r["ft_type"]),
                int(r["winner_side"]),
            )
            for r in rows
        ]

    def delete_matches_not_in_message_ids(
        self, season_id: int, valid_message_ids: set[int]
    ) -> int:
        if not valid_message_ids:
            return 0
        placeholders = ", ".join(["%s"] * len(valid_message_ids))
        with self._session() as (_, cursor):
            cursor.execute(
                f"""
                DELETE FROM matches
                WHERE season_id = %s
                  AND message_id NOT IN ({placeholders})
                """,
                (season_id, *valid_message_ids),
            )
            return cursor.rowcount

    def recalculate_season_elo(self, season_id: int) -> None:
        active = self.get_active_season()
        if not active or active["id"] != season_id:
            self._replay_season_elo_snapshot(season_id)
            return

        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT DISTINCT p.*
                FROM players p
                JOIN matches m ON p.id IN (m.player1_id, m.player2_id)
                WHERE m.season_id = %s
                """,
                (season_id,),
            )
            players = cursor.fetchall()
            elo_map: dict[int, int] = {}
            for player in players:
                base = base_elo_for_player(
                    player.get("tier_rank"), bool(player.get("rank_manual"))
                )
                elo_map[player["id"]] = base
                cursor.execute(
                    "UPDATE players SET elo = %s WHERE id = %s",
                    (base, player["id"]),
                )

            cursor.execute(
                """
                SELECT id, player1_id, player2_id, ft_type, winner_id
                FROM matches
                WHERE season_id = %s
                ORDER BY created_at ASC, match_index ASC
                """,
                (season_id,),
            )
            matches = cursor.fetchall()

            for match in matches:
                winner_id = match["winner_id"]
                loser_id = (
                    match["player2_id"]
                    if winner_id == match["player1_id"]
                    else match["player1_id"]
                )
                winner_elo = elo_map.get(winner_id, 1000)
                loser_elo = elo_map.get(loser_id, 1000)
                win_delta, loss_delta = compute_match_elo_changes(
                    winner_elo, loser_elo, match["ft_type"]
                )
                elo_map[winner_id] = max(0, winner_elo + win_delta)
                elo_map[loser_id] = max(0, loser_elo + loss_delta)
                cursor.execute(
                    "UPDATE players SET elo = %s WHERE id = %s",
                    (elo_map[winner_id], winner_id),
                )
                cursor.execute(
                    "UPDATE players SET elo = %s WHERE id = %s",
                    (elo_map[loser_id], loser_id),
                )

        logger.info("Points recalculés (ELO compétitif) — saison %s", season_id)

    def _replay_season_elo_snapshot(self, season_id: int) -> dict[int, int]:
        """Recalcule l'ELO d'une saison archivée sans modifier la base."""
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT DISTINCT p.id, p.tier_rank, p.rank_manual
                FROM players p
                JOIN matches m ON p.id IN (m.player1_id, m.player2_id)
                WHERE m.season_id = %s
                """,
                (season_id,),
            )
            elo_map = {
                row["id"]: base_elo_for_player(
                    row.get("tier_rank"), bool(row.get("rank_manual"))
                )
                for row in cursor.fetchall()
            }
            cursor.execute(
                """
                SELECT player1_id, player2_id, ft_type, winner_id
                FROM matches
                WHERE season_id = %s
                ORDER BY created_at ASC, match_index ASC
                """,
                (season_id,),
            )
            for match in cursor.fetchall():
                winner_id = match["winner_id"]
                loser_id = (
                    match["player2_id"]
                    if winner_id == match["player1_id"]
                    else match["player1_id"]
                )
                win_d, loss_d = compute_match_elo_changes(
                    elo_map.get(winner_id, 1000),
                    elo_map.get(loser_id, 1000),
                    match["ft_type"],
                )
                elo_map[winner_id] = max(0, elo_map.get(winner_id, 1000) + win_d)
                elo_map[loser_id] = max(0, elo_map.get(loser_id, 1000) + loss_d)
        return elo_map

    def count_season_matches(self, season_id: int) -> int:
        with self._session() as (_, cursor):
            cursor.execute(
                "SELECT COUNT(*) FROM matches WHERE season_id = %s", (season_id,)
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def count_season_players(self, season_id: int) -> int:
        with self._session() as (_, cursor):
            cursor.execute(
                """
                SELECT COUNT(DISTINCT player_id) FROM (
                    SELECT player1_id AS player_id FROM matches WHERE season_id = %s
                    UNION
                    SELECT player2_id AS player_id FROM matches WHERE season_id = %s
                ) AS all_players
                """,
                (season_id, season_id),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def get_player_by_discord_id(self, discord_id: int) -> dict[str, Any] | None:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                "SELECT * FROM players WHERE discord_id = %s", (discord_id,)
            )
            return cursor.fetchone()

    def get_player_by_normalized_name(self, normalized_name: str) -> dict[str, Any] | None:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                "SELECT * FROM players WHERE normalized_name = %s", (normalized_name,)
            )
            return cursor.fetchone()

    def get_or_create_player(
        self, name: str, discord_id: int | None = None
    ) -> dict[str, Any]:
        from player_identity import resolve_or_create_player

        return resolve_or_create_player(self, name, discord_id)

    def create_player(
        self, display_name: str, normalized: str, discord_id: int | None = None
    ) -> dict[str, Any]:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO players (discord_id, name, normalized_name)
                VALUES (%s, %s, %s)
                """,
                (discord_id, display_name, normalized),
            )
            player_id = cursor.lastrowid
            cursor.execute("SELECT * FROM players WHERE id = %s", (player_id,))
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"Joueur créé introuvable: id={player_id}")
            return row

    def update_player_fields(self, player_id: int, **fields) -> None:
        allowed = {"name", "normalized_name", "discord_id", "region", "tier_rank", "elo"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        
        # Check if normalized_name would cause a duplicate
        if "normalized_name" in updates:
            with self._session(dictionary=True) as (_, cursor):
                cursor.execute(
                    "SELECT id FROM players WHERE normalized_name = %s AND id != %s",
                    (updates["normalized_name"], player_id),
                )
                if cursor.fetchone():
                    logger.warning(
                        "Cannot update normalized_name to %s for player %s: already exists",
                        updates["normalized_name"],
                        player_id,
                    )
                    del updates["normalized_name"]
        
        if not updates:
            return
            
        cols = ", ".join(f"{k} = %s" for k in updates)
        with self._session() as (_, cursor):
            cursor.execute(
                f"UPDATE players SET {cols} WHERE id = %s",
                (*updates.values(), player_id),
            )

    def _update_player_name(
        self, player_id: int, name: str, normalized_name: str
    ) -> None:
        with self._session() as (_, cursor):
            cursor.execute(
                """
                UPDATE players SET name = %s, normalized_name = %s WHERE id = %s
                """,
                (name, normalized_name, player_id),
            )

    def link_discord_id(self, player_id: int, discord_id: int) -> None:
        with self._session() as (_, cursor):
            cursor.execute(
                "UPDATE players SET discord_id = %s WHERE id = %s AND discord_id IS NULL",
                (discord_id, player_id),
            )

    def insert_match(
        self,
        message_id: int,
        match_index: int,
        season_id: int,
        player1_id: int,
        player2_id: int,
        score1: int,
        score2: int,
        ft_type: int,
        winner_id: int,
        *,
        created_at=None,
    ) -> bool:
        try:
            with self._session() as (_, cursor):
                if created_at is not None:
                    cursor.execute(
                        """
                        INSERT INTO matches (
                            message_id, match_index, season_id, player1_id, player2_id,
                            score1, score2, ft_type, winner_id, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            message_id,
                            match_index,
                            season_id,
                            player1_id,
                            player2_id,
                            score1,
                            score2,
                            ft_type,
                            winner_id,
                            created_at,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO matches (
                            message_id, match_index, season_id, player1_id, player2_id,
                            score1, score2, ft_type, winner_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            message_id,
                            match_index,
                            season_id,
                            player1_id,
                            player2_id,
                            score1,
                            score2,
                            ft_type,
                            winner_id,
                        ),
                    )
            return True
        except Error as exc:
            if exc.errno == 1062:
                logger.debug(
                    "Match déjà enregistré: message_id=%s index=%s",
                    message_id,
                    match_index,
                )
                return False
            raise

    def get_leaderboard(
        self,
        season_id: int,
        *,
        region: str | None = None,
        active_only: bool = True,
        date_debut=None,
        date_fin=None,
    ) -> list[dict[str, Any]]:
        region_filter = ""
        date_filter = ""
        params: list[Any] = [season_id]

        if region:
            region_filter = "AND p.region = %s"
            params.append(region)
        if date_debut:
            date_filter += " AND m.created_at >= %s"
            params.append(date_debut)
        if date_fin:
            date_filter += " AND m.created_at <= %s"
            params.append(date_fin)

        active_filter = ""
        if active_only and not date_debut and not date_fin:
            active_filter = f"""
                HAVING MAX(m.created_at) >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL {config.ACTIVE_DAYS} DAY)
            """

        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                f"""
                SELECT
                    p.id,
                    p.name,
                    p.normalized_name,
                    p.discord_id,
                    p.region,
                    p.tier_rank,
                    p.elo,
                    MAX(m.created_at) AS last_match_at,
                    SUM(CASE WHEN m.winner_id = p.id THEN 1 ELSE 0 END) AS ft_wins,
                    SUM(
                        CASE
                            WHEN m.winner_id != p.id
                             AND (m.player1_id = p.id OR m.player2_id = p.id)
                            THEN 1 ELSE 0
                        END
                    ) AS ft_losses,
                    SUM(
                        CASE
                            WHEN m.winner_id != p.id
                             AND (m.player1_id = p.id OR m.player2_id = p.id)
                            THEN CASE WHEN m.player1_id = p.id THEN m.score1 ELSE m.score2 END
                            ELSE 0
                        END
                    ) AS loss_points_scored,
                    SUM(
                        CASE
                            WHEN m.winner_id != p.id
                             AND (m.player1_id = p.id OR m.player2_id = p.id)
                            THEN CASE WHEN m.player1_id = p.id THEN m.score2 ELSE m.score1 END
                            ELSE 0
                        END
                    ) AS loss_points_conceded
                FROM matches m
                JOIN players p ON p.id IN (m.player1_id, m.player2_id)
                WHERE m.season_id = %s
                {region_filter}
                {date_filter}
                GROUP BY p.id, p.name, p.normalized_name, p.discord_id,
                         p.region, p.tier_rank, p.elo
                {active_filter}
                ORDER BY p.normalized_name ASC
                """,
                tuple(params),
            )
            rows = cursor.fetchall()

        active = self.get_active_season()
        archived_elo: dict[int, int] = {}
        if active and active["id"] != season_id:
            archived_elo = self._replay_season_elo_snapshot(season_id)

        for row in rows:
            row["display_name"] = self.player_display_name(row)
            row["is_active"] = True
            for key in (
                "ft_wins",
                "ft_losses",
                "loss_points_scored",
                "loss_points_conceded",
                "elo",
            ):
                row[key] = int(row.get(key) or 0)
            if archived_elo:
                row["elo"] = archived_elo.get(row["id"], row["elo"])
            wins = row["ft_wins"]
            losses = row["ft_losses"]
            row["winrate"] = (wins / (wins + losses) * 100) if (wins + losses) else 0.0

        rows.sort(
            key=lambda r: (
                -int(r.get("elo") or 0),
                -float(r.get("winrate") or 0),
                -int(r.get("ft_wins") or 0),
                r.get("display_name", ""),
            )
        )
        return rows

    def _normalize_tier(self, tier: str) -> str:
        raw = tier.strip()
        for valid in config.VALID_TIERS:
            if valid.upper() == raw.upper():
                return valid
        raise ValueError(f"Rang invalide: {tier}")

    def set_player_rank(self, player_id: int, tier: str, *, manual: bool = True) -> int:
        from ranking import elo_for_tier

        tier = self._normalize_tier(tier)
        elo = elo_for_tier(tier)
        with self._session() as (_, cursor):
            cursor.execute(
                """
                UPDATE players
                SET tier_rank = %s, elo = %s, rank_manual = %s
                WHERE id = %s
                """,
                (tier, elo, 1 if manual else 0, player_id),
            )
        return elo

    def adjust_elo_after_match(
        self, winner_id: int, loser_id: int, ft_type: int
    ) -> None:
        winner = self.get_player_by_id(winner_id)
        loser = self.get_player_by_id(loser_id)
        if not winner or not loser:
            return
        win_delta, loss_delta = compute_match_elo_changes(
            int(winner.get("elo") or 1000),
            int(loser.get("elo") or 1000),
            ft_type,
        )
        with self._session() as (_, cursor):
            cursor.execute(
                "UPDATE players SET elo = GREATEST(0, elo + %s) WHERE id = %s",
                (win_delta, winner_id),
            )
            cursor.execute(
                "UPDATE players SET elo = GREATEST(0, elo + %s) WHERE id = %s",
                (loss_delta, loser_id),
            )

    def is_player_active(self, player_id: int, season_id: int) -> bool:
        with self._session() as (_, cursor):
            cursor.execute(
                """
                SELECT MAX(created_at) FROM matches
                WHERE season_id = %s
                  AND (player1_id = %s OR player2_id = %s)
                """,
                (season_id, player_id, player_id),
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                return False
            cursor.execute(
                f"""
                SELECT %s >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL {config.ACTIVE_DAYS} DAY)
                """,
                (row[0],),
            )
            active_row = cursor.fetchone()
            return bool(active_row and active_row[0])

    def set_player_region(self, player_id: int, region: str) -> None:
        region = region.upper()
        if region not in config.VALID_REGIONS:
            raise ValueError(f"Région invalide: {region}")
        with self._session() as (_, cursor):
            cursor.execute(
                "UPDATE players SET region = %s WHERE id = %s",
                (region, player_id),
            )

    def clear_player_region(self, player_id: int) -> None:
        with self._session() as (_, cursor):
            cursor.execute(
                "UPDATE players SET region = NULL WHERE id = %s",
                (player_id,),
            )

    def list_players_without_region(self, season_id: int) -> list[dict[str, Any]]:
        return [
            p
            for p in self.list_season_players(season_id)
            if not (p.get("region") or "").strip()
        ]

    def list_players_with_region(self, season_id: int) -> list[dict[str, Any]]:
        return [
            p
            for p in self.list_season_players(season_id)
            if (p.get("region") or "").strip()
        ]

    def assign_region_bulk(self, season_id: int, region: str) -> int:
        """Attribue une région à tous les joueurs de la saison sans région."""
        region = region.upper()
        count = 0
        for player in self.list_players_without_region(season_id):
            self.set_player_region(player["id"], region)
            count += 1
        return count

    def link_discord_id_force(self, player_id: int, discord_id: int) -> None:
        with self._session() as (_, cursor):
            cursor.execute(
                "UPDATE players SET discord_id = NULL WHERE discord_id = %s AND id != %s",
                (discord_id, player_id),
            )
            cursor.execute(
                "UPDATE players SET discord_id = %s WHERE id = %s",
                (discord_id, player_id),
            )

    def list_season_players(self, season_id: int) -> list[dict[str, Any]]:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT DISTINCT p.*
                FROM players p
                JOIN matches m ON p.id IN (m.player1_id, m.player2_id)
                WHERE m.season_id = %s
                ORDER BY p.normalized_name ASC
                """,
                (season_id,),
            )
            rows = cursor.fetchall()
        return [r for r in rows if self._is_valid_player(r)]

    def list_all_players(self) -> list[dict[str, Any]]:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                "SELECT * FROM players ORDER BY normalized_name ASC"
            )
            rows = cursor.fetchall()
        return [r for r in rows if self._is_valid_player(r)]

    def list_players_for_menus(self) -> list[dict[str, Any]]:
        """Liste fraîche pour menus admin — uniquement les joueurs encore en base."""
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT p.*
                FROM players p
                WHERE EXISTS (
                    SELECT 1 FROM players p2 WHERE p2.id = p.id
                )
                ORDER BY p.name ASC
                """
            )
            rows = cursor.fetchall()
        valid = [r for r in rows if self._is_valid_player(r)]
        seen_keys: dict[str, int] = {}
        unique: list[dict[str, Any]] = []
        for p in valid:
            pid = p["id"]
            if self.get_player_by_id(pid) is None:
                continue
            key = p.get("normalized_name") or normalize_key(p.get("name", ""))
            if key and key in seen_keys:
                continue
            if key:
                seen_keys[key] = pid
            unique.append(p)
        return unique

    def list_players_by_region(self, season_id: int, region: str) -> list[dict[str, Any]]:
        return [
            p
            for p in self.list_season_players(season_id)
            if (p.get("region") or "").upper() == region.upper()
        ]

    def get_player_matches(self, player_id: int, season_id: int) -> list[dict[str, Any]]:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT m.*,
                       p1.name AS player1_name,
                       p1.normalized_name AS player1_norm,
                       p2.name AS player2_name,
                       p2.normalized_name AS player2_norm
                FROM matches m
                JOIN players p1 ON p1.id = m.player1_id
                JOIN players p2 ON p2.id = m.player2_id
                WHERE m.season_id = %s
                  AND (m.player1_id = %s OR m.player2_id = %s)
                ORDER BY m.created_at ASC, m.match_index ASC
                """,
                (season_id, player_id, player_id),
            )
            rows = cursor.fetchall()

        for row in rows:
            is_player1 = row["player1_id"] == player_id
            row["won"] = row["winner_id"] == player_id
            row["my_score"] = row["score1"] if is_player1 else row["score2"]
            row["opp_score"] = row["score2"] if is_player1 else row["score1"]
            if is_player1:
                row["opponent_display"] = row.get("player2_name") or "?"
            else:
                row["opponent_display"] = row.get("player1_name") or "?"
        return rows

    def get_player_stats(self, player_id: int, season_id: int) -> dict[str, Any] | None:
        player = self.get_player_by_id(player_id)
        if not player:
            return None

        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT
                    SUM(CASE WHEN m.winner_id = %s THEN 1 ELSE 0 END) AS ft_wins,
                    SUM(
                        CASE
                            WHEN m.winner_id != %s
                             AND (m.player1_id = %s OR m.player2_id = %s)
                            THEN 1 ELSE 0
                        END
                    ) AS ft_losses,
                    SUM(CASE WHEN m.winner_id = %s AND m.ft_type = 2 THEN 1 ELSE 0 END) AS ft_2,
                    SUM(CASE WHEN m.winner_id = %s AND m.ft_type = 3 THEN 1 ELSE 0 END) AS ft_3,
                    SUM(CASE WHEN m.winner_id = %s AND m.ft_type = 5 THEN 1 ELSE 0 END) AS ft_5,
                    SUM(CASE WHEN m.winner_id = %s AND m.ft_type = 7 THEN 1 ELSE 0 END) AS ft_7,
                    SUM(CASE WHEN m.winner_id = %s AND m.ft_type = 10 THEN 1 ELSE 0 END) AS ft_10
                FROM matches m
                WHERE m.season_id = %s
                  AND (m.player1_id = %s OR m.player2_id = %s)
                """,
                (
                    player_id,
                    player_id,
                    player_id,
                    player_id,
                    player_id,
                    player_id,
                    player_id,
                    player_id,
                    player_id,
                    season_id,
                    player_id,
                    player_id,
                ),
            )
            stats = cursor.fetchone()

        result = {
            **player,
            "display_name": self.player_display_name(player),
            "ft_wins": int(stats["ft_wins"] or 0) if stats else 0,
            "ft_losses": int(stats["ft_losses"] or 0) if stats else 0,
            "ft_2": int(stats["ft_2"] or 0) if stats else 0,
            "ft_3": int(stats["ft_3"] or 0) if stats else 0,
            "ft_5": int(stats["ft_5"] or 0) if stats else 0,
            "ft_7": int(stats["ft_7"] or 0) if stats else 0,
            "ft_10": int(stats["ft_10"] or 0) if stats else 0,
            "is_active": self.is_player_active(player_id, season_id),
            "matches": self.get_player_matches(player_id, season_id),
        }
        return result

    def get_player_by_id(self, player_id: int) -> dict[str, Any] | None:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute("SELECT * FROM players WHERE id = %s", (player_id,))
            return cursor.fetchone()

    @staticmethod
    def _name_tokens(normalized: str) -> list[str]:
        tokens: list[str] = []
        for part in normalized.split():
            for sub in part.split("_"):
                if sub:
                    tokens.append(sub)
        return tokens

    @classmethod
    def _player_match_score(cls, search: str, player_norm: str) -> tuple[int, int]:
        """Score de correspondance (plus bas = meilleur)."""
        if not search or not player_norm:
            return (99, 999)
        if player_norm == search:
            return (0, len(player_norm))
        if player_norm.startswith(search):
            return (1, len(player_norm))
        if search in player_norm:
            return (2, len(player_norm))

        search_tokens = cls._name_tokens(search)
        player_tokens = cls._name_tokens(player_norm)
        if not search_tokens or not player_tokens:
            return (99, 999)

        if all(
            any(pt.startswith(st) or st in pt for pt in player_tokens)
            for st in search_tokens
        ):
            return (3, len(player_norm))

        if len(search_tokens) == 1:
            token = search_tokens[0]
            if any(pt.startswith(token) or token in pt for pt in player_tokens):
                return (4, len(player_norm))

        return (99, 999)

    def _fetch_player_candidates(
        self, normalized: str, season_id: int | None
    ) -> list[dict[str, Any]]:
        patterns = {f"%{normalized}%"}
        for token in self._name_tokens(normalized):
            if len(token) >= 2:
                patterns.add(f"%{token}%")

        conditions: list[str] = []
        params: list[Any] = []
        for pattern in patterns:
            conditions.append(
                "(p.normalized_name LIKE %s OR LOWER(p.name) LIKE %s)"
            )
            params.extend([pattern, pattern])

        where_clause = " OR ".join(conditions)
        with self._session(dictionary=True) as (_, cursor):
            if season_id:
                cursor.execute(
                    f"""
                    SELECT DISTINCT p.*
                    FROM players p
                    JOIN matches m ON p.id IN (m.player1_id, m.player2_id)
                    WHERE m.season_id = %s AND ({where_clause})
                    """,
                    [season_id, *params],
                )
            else:
                cursor.execute(
                    f"""
                    SELECT * FROM players p
                    WHERE {where_clause}
                    """,
                    params,
                )
            return cursor.fetchall()

    def _best_name_match(
        self, normalized: str, candidates: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_score = (99, 999)

        for player in candidates:
            if not self._is_valid_player(player):
                continue
            score = self._player_match_score(
                normalized, player.get("normalized_name", "")
            )
            if score < best_score:
                best_score = score
                best = player

        if best_score[0] >= 99:
            return None
        if len(normalized) < 2 and best_score[0] > 1:
            return None
        return best

    def find_player_by_name(self, name, season_id: int | None = None) -> dict[str, Any] | None:
        if hasattr(name, "id") and hasattr(name, "display_name"):
            by_discord = self.get_player_by_discord_id(name.id)
            if by_discord:
                return by_discord
            name = name.display_name

        from player_identity import find_existing_player

        return find_existing_player(self, str(name))

    def get_head_to_head(
        self, player_a_id: int, player_b_id: int, season_id: int
    ) -> list[dict[str, Any]]:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT m.*,
                       p1.name AS player1_name,
                       p1.normalized_name AS player1_norm,
                       p2.name AS player2_name,
                       p2.normalized_name AS player2_norm,
                       w.name AS winner_name,
                       w.normalized_name AS winner_norm
                FROM matches m
                JOIN players p1 ON p1.id = m.player1_id
                JOIN players p2 ON p2.id = m.player2_id
                JOIN players w ON w.id = m.winner_id
                WHERE m.season_id = %s
                  AND (
                    (m.player1_id = %s AND m.player2_id = %s)
                    OR (m.player1_id = %s AND m.player2_id = %s)
                  )
                ORDER BY m.created_at ASC, m.match_index ASC
                """,
                (season_id, player_a_id, player_b_id, player_b_id, player_a_id),
            )
            rows = cursor.fetchall()

        for row in rows:
            row["player1_display"] = row.get("player1_name") or "?"
            row["player2_display"] = row.get("player2_name") or "?"
            row["winner_display"] = row.get("winner_name") or "?"
        return rows

    def merge_player_into(
        self,
        keep_id: int,
        drop_id: int,
        *,
        keep_display_name: str | None = None,
    ) -> None:
        if keep_id == drop_id:
            return
        keep = self.get_player_by_id(keep_id)
        drop = self.get_player_by_id(drop_id)
        if not keep or not drop:
            return

        raw_name = keep_display_name or keep.get("name", "")
        display = sanitize_player_name(raw_name) or raw_name.strip()[:255]
        if not display:
            display = keep.get("name", "Inconnu")
        normalized = normalize_key(display) or keep.get("normalized_name", "")

        # Check if normalized_name would cause a duplicate with another player
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                "SELECT id FROM players WHERE normalized_name = %s AND id != %s AND id != %s",
                (normalized, keep_id, drop_id),
            )
            conflict = cursor.fetchone()
            if conflict:
                logger.warning(
                    "Cannot set normalized_name to %s for player %s: conflicts with player %s",
                    normalized,
                    keep_id,
                    conflict["id"],
                )
                # Keep the original normalized_name to avoid duplicate
                normalized = keep.get("normalized_name", "")

        # Record the alias mapping BEFORE deleting the player (to avoid foreign key constraint)
        self.record_player_alias(drop.get("name", ""), keep_id, drop_id)

        with self._session() as (_, cursor):
            for col in ("player1_id", "player2_id", "winner_id"):
                cursor.execute(
                    f"UPDATE matches SET {col} = %s WHERE {col} = %s",
                    (keep_id, drop_id),
                )
            if drop.get("discord_id") and not keep.get("discord_id"):
                cursor.execute(
                    "UPDATE players SET discord_id = %s WHERE id = %s",
                    (drop["discord_id"], keep_id),
                )
            if not keep.get("region") and drop.get("region"):
                cursor.execute(
                    "UPDATE players SET region = %s WHERE id = %s",
                    (drop["region"], keep_id),
                )
            cursor.execute(
                """
                UPDATE players
                SET name = %s, normalized_name = %s
                WHERE id = %s
                """,
                (display, normalized, keep_id),
            )
            # Delete deduplication_history records for the source player before deleting the player
            cursor.execute(
                "DELETE FROM deduplication_history WHERE source_player_id = %s",
                (drop_id,),
            )
            cursor.execute("DELETE FROM players WHERE id = %s", (drop_id,))
            deleted = cursor.rowcount

        if deleted == 0:
            logger.error(
                "Fusion: suppression échouée pour drop_id=%s", drop_id
            )
            raise RuntimeError(f"Impossible de supprimer le joueur {drop_id}")

        self.deduplicate_all_players()
        logger.info(
            "Fusion joueur %s → %s (nom conservé: %s)",
            drop_id,
            keep_id,
            display,
        )

    def record_player_alias(self, alias_name: str, target_player_id: int, source_player_id: int) -> None:
        """Record a player alias mapping when a merge happens."""
        target = self.get_player_by_id(target_player_id)
        if not target:
            return
        
        with self._session() as (_, cursor):
            cursor.execute(
                """
                INSERT INTO deduplication_history 
                (source_player_id, target_player_id, source_player_name, target_player_name, merged_by)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (source_player_id, target_player_id, alias_name, target.get("name", ""), "system")
            )
        logger.info(
            "Alias enregistré: '%s' → joueur %s (id=%s)",
            alias_name,
            target.get("name", ""),
            target_player_id,
        )

    def resolve_player_alias(self, name: str) -> dict[str, Any] | None:
        """Check if a name was previously merged and return the target player."""
        normalized = normalize_key(name)
        
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT target_player_id, target_player_name
                FROM deduplication_history
                WHERE source_player_name = %s
                ORDER BY merged_at DESC
                LIMIT 1
                """,
                (name,),
            )
            result = cursor.fetchone()
            
            if result:
                target = self.get_player_by_id(result["target_player_id"])
                if target:
                    logger.info(
                        "Alias résolu: '%s' → '%s' (id=%s)",
                        name,
                        result["target_player_name"],
                        result["target_player_id"],
                    )
                    return target
        
        return None

    def list_players_by_tier(self, tier: str | None = None) -> list[dict[str, Any]]:
        players = self.list_all_players()
        if not tier or tier.upper() == "TOUS":
            return players
        tier_norm = self._normalize_tier(tier)
        return [
            p for p in players
            if (p.get("tier_rank") or "NR").upper() == tier_norm.upper()
        ]

    def set_players_rank_bulk(
        self, player_ids: list[int], tier: str, *, manual: bool = True
    ) -> int:
        count = 0
        for pid in player_ids:
            self.set_player_rank(pid, tier, manual=manual)
            count += 1
        return count

    def clear_season_reset_flag(self, season_id: int) -> None:
        with self._session() as (_, cursor):
            cursor.execute(
                "UPDATE seasons SET data_reset_at = NULL WHERE id = %s",
                (season_id,),
            )

    def list_season_matches_detailed(self, season_id: int) -> list[dict[str, Any]]:
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                SELECT m.*,
                       p1.name AS player1_name,
                       p1.region AS player1_region,
                       p2.name AS player2_name,
                       p2.region AS player2_region,
                       w.name AS winner_name
                FROM matches m
                JOIN players p1 ON p1.id = m.player1_id
                JOIN players p2 ON p2.id = m.player2_id
                JOIN players w ON w.id = m.winner_id
                WHERE m.season_id = %s
                ORDER BY m.created_at ASC, m.match_index ASC
                """,
                (season_id,),
            )
            return cursor.fetchall()

    def get_inter_region_stats(self, season_id: int) -> dict[str, Any]:
        matches = self.list_season_matches_detailed(season_id)
        inter = [
            m for m in matches
            if m.get("player1_region") in config.VALID_REGIONS
            and m.get("player2_region") in config.VALID_REGIONS
            and m.get("player1_region") != m.get("player2_region")
        ]
        bz_wins = sum(
            1 for m in inter
            if (
                (m["player1_region"] == "BZ" and m["winner_id"] == m["player1_id"])
                or (m["player2_region"] == "BZ" and m["winner_id"] == m["player2_id"])
            )
        )
        pn_wins = len(inter) - bz_wins

        player_stats: dict[int, dict[str, Any]] = {}
        for m in inter:
            for pid, pname, preg in (
                (m["player1_id"], m["player1_name"], m["player1_region"]),
                (m["player2_id"], m["player2_name"], m["player2_region"]),
            ):
                if pid not in player_stats:
                    player_stats[pid] = {
                        "name": pname,
                        "region": (preg or "—").upper(),
                        "wins": 0,
                        "losses": 0,
                    }
                if m["winner_id"] == pid:
                    player_stats[pid]["wins"] += 1
                else:
                    player_stats[pid]["losses"] += 1

        ranked = sorted(
            player_stats.values(),
            key=lambda r: (-r["wins"], r["losses"], r["name"]),
        )
        return {
            "total_matches": len(inter),
            "bz_wins": bz_wins,
            "pn_wins": pn_wins,
            "player_stats": ranked,
        }

    def deduplicate_all_players(self) -> int:
        from player_identity import similarity

        merged = 0
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute("SELECT * FROM players ORDER BY id ASC")
            players = cursor.fetchall()

        key_owner: dict[str, int] = {}
        for player in players:
            key = normalize_key(player.get("name", ""))
            if not key:
                continue
            if key != player.get("normalized_name"):
                self.update_player_fields(player["id"], normalized_name=key)

            owner = key_owner.get(key)
            if owner and owner != player["id"]:
                self.merge_player_into(owner, player["id"])
                merged += 1
            else:
                key_owner[key] = player["id"]

        remaining = self.list_all_players()
        for i, a in enumerate(remaining):
            key_a = a.get("normalized_name") or normalize_key(a.get("name", ""))
            for b in remaining[i + 1 :]:
                key_b = b.get("normalized_name") or normalize_key(b.get("name", ""))
                if similarity(key_a, key_b) >= config.SIMILARITY_THRESHOLD:
                    self.merge_player_into(a["id"], b["id"])
                    merged += 1
        if merged:
            logger.info("Déduplication : %d fusion(s)", merged)
        return merged

    def export_backup(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {"exported_at": datetime.utcnow().isoformat()}
        with self._session(dictionary=True) as (_, cursor):
            cursor.execute("SELECT * FROM seasons ORDER BY id")
            data["seasons"] = cursor.fetchall()
            cursor.execute("SELECT * FROM players ORDER BY id")
            data["players"] = cursor.fetchall()
            cursor.execute("SELECT * FROM matches ORDER BY id")
            data["matches"] = cursor.fetchall()
            cursor.execute("SELECT * FROM bot_settings")
            data["bot_settings"] = cursor.fetchall()
            cursor.execute("SELECT * FROM deduplication_history ORDER BY id")
            data["deduplication_history"] = cursor.fetchall()
        for table in ("seasons", "players", "matches", "deduplication_history"):
            for row in data[table]:
                for k, v in list(row.items()):
                    if isinstance(v, (date, datetime)):
                        row[k] = v.isoformat()
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return path

    def restore_backup(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        with self._session() as (_, cursor):
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            cursor.execute("TRUNCATE TABLE matches")
            cursor.execute("TRUNCATE TABLE players")
            cursor.execute("TRUNCATE TABLE seasons")
            cursor.execute("TRUNCATE TABLE bot_settings")
            cursor.execute("TRUNCATE TABLE deduplication_history")
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            for season in raw.get("seasons", []):
                cursor.execute(
                    """
                    INSERT INTO seasons
                    (id, name, start_date, end_date, is_active, status,
                     data_reset_at, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        season["id"],
                        season["name"],
                        season["start_date"],
                        season.get("end_date"),
                        season.get("is_active", 0),
                        season.get("status", "archived"),
                        season.get("data_reset_at"),
                        season.get("created_at"),
                    ),
                )
            for player in raw.get("players", []):
                cursor.execute(
                    """
                    INSERT INTO players
                    (id, discord_id, name, normalized_name, region, tier_rank, elo, rank_manual)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        player["id"],
                        player.get("discord_id"),
                        player["name"],
                        player["normalized_name"],
                        player.get("region"),
                        player.get("tier_rank", "NR"),
                        player.get("elo", 1000),
                        player.get("rank_manual", 0),
                    ),
                )
            for match in raw.get("matches", []):
                cursor.execute(
                    """
                    INSERT INTO matches
                    (id, message_id, match_index, season_id, player1_id, player2_id,
                     score1, score2, ft_type, winner_id, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        match["id"],
                        match["message_id"],
                        match.get("match_index", 0),
                        match["season_id"],
                        match["player1_id"],
                        match["player2_id"],
                        match["score1"],
                        match["score2"],
                        match["ft_type"],
                        match["winner_id"],
                        match.get("created_at"),
                    ),
                )
            for setting in raw.get("bot_settings", []):
                cursor.execute(
                    """
                    INSERT INTO bot_settings (setting_key, setting_value)
                    VALUES (%s, %s)
                    """,
                    (setting["setting_key"], setting["setting_value"]),
                )
            for alias in raw.get("deduplication_history", []):
                cursor.execute(
                    """
                    INSERT INTO deduplication_history
                    (id, source_player_id, target_player_id, source_player_name, target_player_name, merged_at, merged_by, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        alias["id"],
                        alias["source_player_id"],
                        alias["target_player_id"],
                        alias["source_player_name"],
                        alias["target_player_name"],
                        alias.get("merged_at"),
                        alias.get("merged_by"),
                        alias.get("notes"),
                    ),
                )

    def wipe_all_and_reset(self) -> None:
        """Vide toutes les tables et recrée une saison vierge."""
        with self._session() as (_, cursor):
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            cursor.execute("TRUNCATE TABLE matches")
            cursor.execute("TRUNCATE TABLE players")
            cursor.execute("TRUNCATE TABLE seasons")
            cursor.execute("TRUNCATE TABLE bot_settings")
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        self.create_season("Saison 1", date.today())
        logger.info("Base vidée et réinitialisée à zéro")

    def health_report(self) -> dict[str, Any]:
        season = self.get_active_season()
        with self._session() as (_, cursor):
            cursor.execute("SELECT COUNT(*) FROM players")
            players = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM matches")
            matches = int(cursor.fetchone()[0])
            cursor.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT normalized_name FROM players
                    GROUP BY normalized_name HAVING COUNT(*) > 1
                ) d
                """
            )
            dup_keys = int(cursor.fetchone()[0])
        return {
            "db_ok": True,
            "active_season": season,
            "players": players,
            "matches": matches,
            "duplicate_keys": dup_keys,
        }
