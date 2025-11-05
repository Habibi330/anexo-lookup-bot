import shutil
from datetime import datetime
from pathlib import Path

# pasta base (mesma do bot.py)
BASE_DIR = Path(__file__).resolve().parent

# caminho do banco original
DB_PATH = BASE_DIR / "bot_data.db"

# pasta onde os backups vão ficar
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

def backup_db():
    if not DB_PATH.exists():
        print(f"[ERRO] Banco de dados não encontrado em: {DB_PATH}")
        return

    # nome do arquivo de backup com data/hora
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"bot_data_backup_{timestamp}.db"

    shutil.copy2(DB_PATH, backup_file)
    print(f"[OK] Backup criado: {backup_file}")

if __name__ == "__main__":
    backup_db()
