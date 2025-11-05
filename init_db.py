import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path("bot_data.db")

def create_tables():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Tabela de usuários
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        username TEXT,
        first_name TEXT,
        created_at TEXT NOT NULL
    );
    """)

    # Tabela de tokens (estoque de tokens que você gera e vende)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        plan_days INTEGER NOT NULL,
        is_used INTEGER NOT NULL DEFAULT 0,   -- 0 = ainda não usado, 1 = já usado
        used_by_user_id INTEGER,              -- referencia users.id
        activated_at TEXT,
        expires_at TEXT
    );
    """)

    # Tabela de logs de pesquisa
    cur.execute("""
    CREATE TABLE IF NOT EXISTS search_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        domain TEXT NOT NULL,
        action TEXT NOT NULL,  -- 'pesquisar' ou 'gerar'
        lines_found INTEGER,
        created_at TEXT NOT NULL
    );
    """)

    # Tabela de URLs que o usuário pesquisou mas você não tem .TXT
    cur.execute("""
    CREATE TABLE IF NOT EXISTS requested_urls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        domain TEXT NOT NULL,
        created_at TEXT NOT NULL,
        handled INTEGER NOT NULL DEFAULT 0   -- 0 = pendente, 1 = você já cuidou
    );
    """)

    conn.commit()
    conn.close()

if __name__ == "__main__":
    create_tables()
    print("✅ Banco de dados 'bot_data.db' criado/atualizado com sucesso!")
