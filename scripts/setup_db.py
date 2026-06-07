#!/usr/bin/env python3
"""Crée la base MySQL et les tables. Usage: python scripts/setup_db.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mysql.connector
from mysql.connector import Error

import config
from database import Database


def create_database() -> None:
    try:
        conn = mysql.connector.connect(
            host=config.MYSQL_HOST,
            user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD,
            port=config.MYSQL_PORT,
        )
    except Error as exc:
        print(f"Erreur connexion MySQL ({config.MYSQL_HOST}): {exc}")
        sys.exit(1)

    cursor = conn.cursor()
    cursor.execute(
        f"""
        CREATE DATABASE IF NOT EXISTS `{config.MYSQL_DATABASE}`
        CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """
    )
    conn.commit()
    cursor.close()
    conn.close()
    print(f"Base `{config.MYSQL_DATABASE}` OK")


def init_tables() -> None:
    db = Database()
    db.init_schema()
    season = db.get_active_season()
    print(f"Tables OK — saison: {season['name']} (id={season['id']})")


def main() -> None:
    print("=== Setup base FT Championship ===")
    create_database()
    init_tables()
    print("Terminé.")


if __name__ == "__main__":
    main()
