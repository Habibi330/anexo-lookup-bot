import sqlite3
import random
import string
from datetime import datetime
from loguru import logger

# Nome do banco de dados
DB_PATH = "bot_data.db"

# ========================
# Função para gerar tokens
# ========================
def generate_token(length=12):
    """Gera um token aleatório e único com letras e números"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

# ========================
# Função para inserir tokens no banco
# ========================
def insert_tokens(plan_days, amount=50):
    """Cria tokens e insere no banco de dados"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    created_at = datetime.utcnow().isoformat()

    for _ in range(amount):
        token = generate_token()
        cursor.execute("""
            INSERT INTO tokens (token, plan_days, used, created_at)
            VALUES (?, ?, 0, ?)
        """, (token, plan_days, created_at))

    conn.commit()
    conn.close()
    logger.info(f"{amount} tokens de {plan_days} dias criados com sucesso!")

# ========================
# Execução principal
# ========================
if __name__ == "__main__":
    logger.info("Gerando tokens...")
    insert_tokens(7, 50)
    insert_tokens(15, 50)
    insert_tokens(30, 50)
    logger.success("✅ Todos os tokens foram gerados e salvos no banco com sucesso!")