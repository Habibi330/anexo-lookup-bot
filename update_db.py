# update_db.py
import sqlite3
from pathlib import Path
from loguru import logger

DB_PATH = Path("bot_data.db")


def update_database():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ===============================
    # TABELA TOKENS
    # ===============================
    try:
        cur.execute("ALTER TABLE tokens ADD COLUMN used INTEGER DEFAULT 0")
        logger.info("Coluna 'used' adicionada com sucesso à tabela tokens.")
    except sqlite3.OperationalError:
        logger.warning("Coluna 'used' já existe na tabela tokens.")

    try:
        cur.execute("ALTER TABLE tokens ADD COLUMN created_at TEXT")
        logger.info("Coluna 'created_at' adicionada com sucesso à tabela tokens.")
    except sqlite3.OperationalError:
        logger.warning("Coluna 'created_at' já existe na tabela tokens.")

    # ===============================
    # TABELA USERS – CAMPOS DE PLANO
    # ===============================
    try:
        cur.execute("ALTER TABLE users ADD COLUMN active_until TEXT")
        logger.info("Coluna 'active_until' adicionada com sucesso à tabela users.")
    except sqlite3.OperationalError:
        logger.warning("Coluna 'active_until' já existe na tabela users.")

    try:
        cur.execute("ALTER TABLE users ADD COLUMN plan_days INTEGER")
        logger.info("Coluna 'plan_days' adicionada com sucesso à tabela users.")
    except sqlite3.OperationalError:
        logger.warning("Coluna 'plan_days' já existe na tabela users.")

    try:
        cur.execute("ALTER TABLE users ADD COLUMN has_active_token INTEGER DEFAULT 0")
        logger.info("Coluna 'has_active_token' adicionada com sucesso à tabela users.")
    except sqlite3.OperationalError:
        logger.warning("Coluna 'has_active_token' já existe na tabela users.")

    try:
        cur.execute("ALTER TABLE users ADD COLUMN last_token TEXT")
        logger.info("Coluna 'last_token' adicionada com sucesso à tabela users.")
    except sqlite3.OperationalError:
        logger.warning("Coluna 'last_token' já existe na tabela users.")

    # ===============================
    # TABELA USERS – CONTROLE DE PESQUISA DIÁRIA
    # ===============================
    try:
        cur.execute("ALTER TABLE users ADD COLUMN search_date TEXT")
        logger.info("Coluna 'search_date' adicionada com sucesso à tabela users.")
    except sqlite3.OperationalError:
        logger.warning("Coluna 'search_date' já existe na tabela users.")

    try:
        cur.execute("ALTER TABLE users ADD COLUMN search_count INTEGER DEFAULT 0")
        logger.info("Coluna 'search_count' adicionada com sucesso à tabela users.")
    except sqlite3.OperationalError:
        logger.warning("Coluna 'search_count' já existe na tabela users.")

    conn.commit()
    conn.close()
    logger.success("Banco de dados atualizado com sucesso!")


if __name__ == "__main__":
    update_database()
