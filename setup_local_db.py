"""
Initialise la base MySQL locale (WAMP) et crée les tables via database.py.
Usage: py -3 setup_local_db.py
"""
import sys

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
        )
    except Error as exc:
        print("Impossible de se connecter a MySQL.")
        print(f"  Host: {config.MYSQL_HOST}")
        print(f"  User: {config.MYSQL_USER}")
        print(f"  Erreur: {exc}")
        print("\nVerifiez que WAMP est demarre (icone verte) et que MySQL tourne.")
        sys.exit(1)

    cursor = conn.cursor()
    cursor.execute(
        f"""
        CREATE DATABASE IF NOT EXISTS `{config.MYSQL_DATABASE}`
        CHARACTER SET utf8mb4
        COLLATE utf8mb4_unicode_ci
        """
    )
    conn.commit()
    cursor.close()
    conn.close()
    print(f"Base `{config.MYSQL_DATABASE}` creee ou deja existante.")


def init_tables() -> None:
    db = Database()
    db.init_schema()
    season = db.get_active_season()
    print(f"Tables creees. Saison active: {season['name']} (id={season['id']})")


def main() -> None:
    print("=== Setup base locale FT Championship ===\n")
    create_database()
    init_tables()
    print("\nPret pour les tests locaux.")
    print("Lancez le bot avec: py -3 bot.py")


if __name__ == "__main__":
    main()
