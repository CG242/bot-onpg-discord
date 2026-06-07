import logging
from contextlib import contextmanager
from typing import Any

import mysql.connector
from mysql.connector import Error

import config
from parser import (
    format_display_name,
    is_valid_player_name,
    normalize_name,
    sanitize_player_name,
)

logger = logging.getLogger(__name__)


class Database:
    def __init__(self) -> None:
        self._config = {
            "host": config.MYSQL_HOST,
            "port": config.MYSQL_PORT,
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
        except Error as exc:
            logger.exception("Erreur init_schema: %s", exc)
            raise

    def _is_valid_player(self, player: dict[str, Any]) -> bool:
        return is_valid_player_name(player.get("name", ""))

    def player_display_name(self, player: dict[str, Any]) -> str:
        if self._is_valid_player(player):
            return player["name"].strip()
        return format_display_name(player.get("normalized_name", ""), player.get("name", ""))

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
                "SELECT * FROM seasons WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
            )
            return cursor.fetchone()

    def create_season(self, name: str, start_date) -> int:
        with self._session() as (_, cursor):
            cursor.execute("UPDATE seasons SET is_active = 0 WHERE is_active = 1")
            cursor.execute(
                "INSERT INTO seasons (name, start_date, is_active) VALUES (%s, %s, 1)",
                (name, start_date),
            )
            return cursor.lastrowid

    def reset_season(self, name: str, start_date) -> int:
        return self.create_season(name, start_date)

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
        from ranking import compute_match_elo_delta, elo_for_tier

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
            for player in players:
                if player.get("rank_manual"):
                    base = elo_for_tier(player.get("tier_rank"))
                else:
                    tier = (player.get("tier_rank") or "NR").upper()
                    base = 1000 if tier == "NR" else elo_for_tier(tier)
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
                win_delta = compute_match_elo_delta(match["ft_type"], True)
                loss_delta = compute_match_elo_delta(match["ft_type"], False)
                loser_id = (
                    match["player2_id"]
                    if match["winner_id"] == match["player1_id"]
                    else match["player1_id"]
                )
                cursor.execute(
                    "UPDATE players SET elo = GREATEST(0, elo + %s) WHERE id = %s",
                    (win_delta, match["winner_id"]),
                )
                cursor.execute(
                    "UPDATE players SET elo = GREATEST(0, elo + %s) WHERE id = %s",
                    (loss_delta, loser_id),
                )

        logger.info("Points recalculés pour la saison %s", season_id)

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
        clean_name = sanitize_player_name(name)
        if not clean_name:
            clean_name = name.strip()[:64]
        normalized = normalize_name(clean_name)

        existing = self.get_player_by_normalized_name(normalized)
        if existing:
            if discord_id and not existing.get("discord_id"):
                self.link_discord_id(existing["id"], discord_id)
                existing["discord_id"] = discord_id
            if not self._is_valid_player(existing):
                self._update_player_name(existing["id"], clean_name, normalized)
                existing["name"] = clean_name
                existing["normalized_name"] = normalized
            return existing

        if discord_id:
            by_discord = self.get_player_by_discord_id(discord_id)
            if by_discord:
                return by_discord

        with self._session(dictionary=True) as (_, cursor):
            cursor.execute(
                """
                INSERT INTO players (discord_id, name, normalized_name)
                VALUES (%s, %s, %s)
                """,
                (discord_id, clean_name, normalized),
            )
            player_id = cursor.lastrowid
            cursor.execute("SELECT * FROM players WHERE id = %s", (player_id,))
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"Joueur créé introuvable: id={player_id}")
            return row

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
                ORDER BY
                    ft_wins DESC,
                    ft_losses ASC,
                    (loss_points_scored / GREATEST(loss_points_conceded, 1)) DESC,
                    p.elo DESC,
                    p.normalized_name ASC
                """,
                tuple(params),
            )
            rows = cursor.fetchall()

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
        from ranking import compute_match_elo_delta

        win_delta = compute_match_elo_delta(ft_type, True)
        loss_delta = compute_match_elo_delta(ft_type, False)
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
                row["opponent_display"] = format_display_name(
                    row.get("player2_norm", ""), row.get("player2_name", "")
                )
            else:
                row["opponent_display"] = format_display_name(
                    row.get("player1_norm", ""), row.get("player1_name", "")
                )
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

        normalized = normalize_name(str(name))
        if not normalized:
            return None

        exact = self.get_player_by_normalized_name(normalized)
        if exact and self._is_valid_player(exact):
            return exact

        candidates = self._fetch_player_candidates(normalized, season_id)
        return self._best_name_match(normalized, candidates)

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
            row["player1_display"] = format_display_name(
                row.get("player1_norm", ""), row.get("player1_name", "")
            )
            row["player2_display"] = format_display_name(
                row.get("player2_norm", ""), row.get("player2_name", "")
            )
            row["winner_display"] = format_display_name(
                row.get("winner_norm", ""), row.get("winner_name", "")
            )
        return rows
