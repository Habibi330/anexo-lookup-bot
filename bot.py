import sqlite3
import time
import re
import secrets
from collections import deque, defaultdict
from pathlib import Path
from datetime import datetime, timedelta, date
from loguru import logger
import telebot

# =======================
# 🔐 CONFIGURAÇÃO INICIAL
# =======================

# ⚠️ TOKEN DO SEU BOT
TOKEN = "8552593474:AAHEpVabqkopc2AiMOXSVkAP8wMExmf5pL4"

# ID(s) de administrador do bot (o seu ID)
ADMIN_IDS = {8091481688}

# Canal obrigatório
REQUIRED_CHANNEL_USERNAME = "AnexoLookup"          # sem @
REQUIRED_CHANNEL_LINK = "https://t.me/AnexoLookup"  # link do canal

# Caminhos principais
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bot_data.db"

DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs"
LOGS_DIR = BASE_DIR / "logs"

# Limite de tamanho para envio de arquivo (MB)
MAX_FILE_SIZE_MB = 45

# Limite de pesquisa grátis para quem NÃO tem token
MAX_FREE_SEARCHES_PER_DAY = 10

# Garante que as pastas existam
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

logger.add(LOGS_DIR / "bot.log", rotation="1 week", encoding="utf-8")

# Cria o bot
bot = telebot.TeleBot(
    TOKEN,
    parse_mode="Markdown",
    disable_web_page_preview=True  # evita preview de links (mais limpo e seguro)
)


# =======================
# 🧠 FUNÇÕES DE BANCO
# =======================

def get_conn():
    return sqlite3.connect(DB_PATH)


def get_or_create_user(message) -> int:
    """
    Garante que o usuário exista na tabela users e retorna o id interno.
    """
    tg_id = message.from_user.id
    username = (message.from_user.username or "").strip()
    first_name = (message.from_user.first_name or "").strip()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE telegram_id = ?", (tg_id,))
    row = cur.fetchone()

    if row:
        user_id = row[0]
        # Atualiza nome/username se mudarem
        cur.execute(
            "UPDATE users SET username = ?, first_name = ? WHERE id = ?",
            (username, first_name, user_id),
        )
    else:
        created_at = datetime.utcnow().isoformat()
        cur.execute(
            """
            INSERT INTO users (telegram_id, username, first_name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (tg_id, username, first_name, created_at),
        )
        user_id = cur.lastrowid

    conn.commit()
    conn.close()
    return user_id


def get_active_token_info(user_id: int):
    """
    Retorna informações do token ativo do usuário (ou None se não tiver).
    """
    now_iso = datetime.utcnow().isoformat()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, token, plan_days, expires_at
        FROM tokens
        WHERE used_by_user_id = ?
          AND expires_at IS NOT NULL
          AND expires_at > ?
        ORDER BY expires_at DESC
        LIMIT 1
        """,
        (user_id, now_iso),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    token_id, token, plan_days, expires_at = row
    expires_dt = datetime.fromisoformat(expires_at)
    diff = expires_dt - datetime.utcnow()
    days_left = max(diff.days, 0)

    return {
        "id": token_id,
        "token": token,
        "plan_days": plan_days,
        "expires_at": expires_dt,
        "days_left": days_left,
    }


def update_free_search_counter(user_id: int):
    """
    Atualiza o contador diário de pesquisas para quem NÃO tem token.
    Retorna (allowed: bool, remaining: int)
    """
    today = date.today().isoformat()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT search_date, search_count FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()

    if not row:
        search_date = today
        search_count = 0
    else:
        search_date, search_count = row
        if search_count is None:
            search_count = 0

    # se mudou o dia, zera o contador
    if search_date != today:
        search_date = today
        search_count = 0

    if search_count >= MAX_FREE_SEARCHES_PER_DAY:
        conn.close()
        return False, 0

    search_count += 1

    cur.execute(
        "UPDATE users SET search_date = ?, search_count = ? WHERE id = ?",
        (search_date, search_count, user_id),
    )
    conn.commit()
    conn.close()

    remaining = MAX_FREE_SEARCHES_PER_DAY - search_count
    return True, remaining


def log_missing_domain(user_id: int, domain: str):
    """
    Registra no banco domínios pesquisados que ainda não existem em TXT.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS missing_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            domain TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    created_at = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO missing_searches (user_id, domain, created_at) VALUES (?, ?, ?)",
        (user_id, domain, created_at),
    )

    conn.commit()
    conn.close()
    logger.info(f"Domínio não encontrado registrado: user_id={user_id}, domain={domain}")


# =======================
# 🔧 FUNÇÕES AUXILIARES
# =======================

def normalize_domain(raw: str) -> str:
    """
    Normaliza a URL digitada pelo usuário:
    - remove http://, https://
    - remove caminho (/algo/depois)
    - remove query (?x=1)
    - remove www.
    - filtra caracteres estranhos (apenas a-z, 0-9, ponto e hífen)
    """
    text = raw.strip().lower()

    if "://" in text:
        text = text.split("://", 1)[1]

    # tira caminho e querystring
    text = text.split("/", 1)[0]
    text = text.split("?", 1)[0]

    if text.startswith("www."):
        text = text[4:]

    # tira qualquer coisa que não seja domínio normal
    text = re.sub(r"[^a-z0-9\.\-]", "", text)

    return text


def is_admin(message) -> bool:
    """
    Verifica se o usuário é administrador.
    """
    return message.from_user.id in ADMIN_IDS


# =======================
# 🚫 SISTEMA DE BLOQUEIO TEMPORÁRIO
# =======================

# Thresholds configuráveis
INVALID_TOKEN_THRESHOLD = 3            # tentativas inválidas antes do bloqueio
INVALID_TOKEN_BAN_HOURS = 24           # horas de bloqueio por token inválido
FLOOD_THRESHOLD = 5                    # comandos
FLOOD_WINDOW_SECONDS = 10              # janela de tempo para contar comandos
FLOOD_BAN_FIRST_HOURS = 1              # bloqueio inicial por flood
FLOOD_BAN_REPEAT_HOURS = 24            # se reincidir, bloqueio maior

# In-memory helpers
_recent_commands = defaultdict(lambda: deque())
_invalid_token_counts = defaultdict(int)
_flood_incidents = defaultdict(int)


def ensure_temp_bans_table():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS temp_bans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            reason TEXT,
            banned_at TEXT NOT NULL,
            ban_until TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


ensure_temp_bans_table()


def temp_ban_user(telegram_id: int, hours: float, reason: str = "Automático"):
    ban_until = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
    banned_at = datetime.utcnow().isoformat()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO temp_bans (telegram_id, reason, banned_at, ban_until) VALUES (?, ?, ?, ?)",
        (telegram_id, reason, banned_at, ban_until)
    )
    conn.commit()
    conn.close()
    logger.warning(f"Usuário {telegram_id} temporariamente bloqueado por {hours}h - motivo: {reason}")


def unban_user(telegram_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM temp_bans WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()
    logger.info(f"Usuário {telegram_id} desbanido manualmente.")


def is_user_blocked(telegram_id: int):
    """
    Retorna (blocked: bool, seconds_left: int, reason: str|None)
    """
    now_iso = datetime.utcnow().isoformat()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT reason, ban_until
        FROM temp_bans
        WHERE telegram_id = ?
          AND ban_until > ?
        ORDER BY ban_until DESC
        LIMIT 1
        """,
        (telegram_id, now_iso)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return False, 0, None

    reason, ban_until = row
    ban_until_dt = datetime.fromisoformat(ban_until)
    secs_left = max(int((ban_until_dt - datetime.utcnow()).total_seconds()), 0)
    return True, secs_left, reason


def list_active_bans():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT telegram_id, reason, banned_at, ban_until
        FROM temp_bans
        WHERE ban_until > ?
        ORDER BY ban_until ASC
        """,
        (datetime.utcnow().isoformat(),)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def check_block_and_reply(message):
    """
    Checa se o usuário está bloqueado. Se sim, responde e retorna True.
    """
    blocked, secs_left, reason = is_user_blocked(message.from_user.id)
    if blocked:
        hours = secs_left // 3600
        minutes = (secs_left % 3600) // 60
        reason_text = reason or "Automático"
        bot.reply_to(
            message,
            f"🚫 Seu acesso está temporariamente bloqueado.\n"
            f"Motivo: *{reason_text}*\n"
            f"Tempo restante: `{hours}h {minutes}m`.\n"
            f"Se achar que é um engano, fale com o suporte com `/suporte`."
        )
        return True
    return False


def register_command_and_check_flood(message):
    """
    Registra o comando e verifica flood.
    Retorna True se um ban foi aplicado e o handler deve parar.
    """
    now = datetime.utcnow().timestamp()
    dq = _recent_commands[message.from_user.id]
    dq.append(now)
    # Remove timestamps antigos fora da janela
    while dq and dq[0] < now - FLOOD_WINDOW_SECONDS:
        dq.popleft()

    if len(dq) > FLOOD_THRESHOLD:
        _flood_incidents[message.from_user.id] += 1
        incident_count = _flood_incidents[message.from_user.id]
        if incident_count == 1:
            temp_ban_user(message.from_user.id, FLOOD_BAN_FIRST_HOURS, "Flood de comandos detectado (automático)")
            bot.reply_to(message, "⚠️ Muitos comandos em pouco tempo. Bloqueio temporário aplicado por 1 hora.")
        else:
            temp_ban_user(message.from_user.id, FLOOD_BAN_REPEAT_HOURS, "Reincidência de flood de comandos")
            bot.reply_to(message, "⚠️ Reincidência de flood detectada. Bloqueio temporário aplicado por 24 horas.")
        _recent_commands.pop(message.from_user.id, None)
        return True
    return False


# =======================
# 📢 CHECAGEM DE CANAL
# =======================

def is_user_in_required_channel(user_id: int):
    """
    Verifica se o usuário é membro do canal obrigatório.
    Retorna:
      True  -> está no canal
      False -> não está
      None  -> erro ao verificar
    """
    chat_id = f"@{REQUIRED_CHANNEL_USERNAME}"
    try:
        member = bot.get_chat_member(chat_id, user_id)
    except Exception as e:
        logger.error(f"Erro ao verificar participação no canal: {e}")
        return None

    if member.status in ("creator", "administrator", "member"):
        return True
    return False


def ensure_in_channel_or_explain(message) -> bool:
    """
    Garante que o usuário esteja no canal.
    Se não estiver / ou erro, envia mensagem com link e retorna False.
    """
    result = is_user_in_required_channel(message.from_user.id)

    if result is True:
        return True

    if result is None:
        bot.reply_to(
            message,
            "⚠️ Não consegui verificar sua participação no canal agora.\n"
            "Tente novamente em alguns minutos."
        )
        return False

    # result is False -> mandar msg com botão
    markup = telebot.types.InlineKeyboardMarkup()
    btn = telebot.types.InlineKeyboardButton(
        "✅ Entrar no canal Anexo Lookup",
        url=REQUIRED_CHANNEL_LINK
    )
    markup.add(btn)

    bot.reply_to(
        message,
        "📢 Para utilizar o bot você precisa fazer parte do nosso canal oficial:\n"
        f"👉 {REQUIRED_CHANNEL_LINK}\n\n"
        "Entre no canal e depois envie o comando novamente aqui no bot. 😉",
        reply_markup=markup
    )
    return False


# =======================
# 🔑 GERADOR DE TOKENS (ADMIN)
# =======================

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem O, 0, I, 1


def generate_token_code(length: int = 16) -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(length))
    # Formata em grupos de 4: XXXX-XXXX-XXXX-XXXX
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


@bot.message_handler(commands=["criar_token", "gerar_token"])
def cmd_criar_token(message):
    if check_block_and_reply(message):
        return
    if not is_admin(message):
        bot.reply_to(message, "❌ Comando restrito a administradores.")
        return
    if not ensure_in_channel_or_explain(message):
        return
    if register_command_and_check_flood(message):
        return

    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(
            message,
            "Uso: `/criar_token <dias> <quantidade>`\n"
            "Exemplo: `/criar_token 7 5` (5 tokens de 7 dias)"
        )
        return

    try:
        plan_days = int(parts[1])
        quantity = int(parts[2])
    except ValueError:
        bot.reply_to(
            message,
            "❌ Parâmetros inválidos.\n"
            "Exemplo correto: `/criar_token 7 5`"
        )
        return

    if plan_days not in (7, 15, 30):
        bot.reply_to(
            message,
            "❌ Dias inválidos. Use apenas: 7, 15 ou 30."
        )
        return

    if not (1 <= quantity <= 50):
        bot.reply_to(
            message,
            "❌ Quantidade inválida. Use entre 1 e 50 tokens por vez."
        )
        return

    conn = get_conn()
    cur = conn.cursor()

    tokens = []
    for _ in range(quantity):
        code = generate_token_code()
        cur.execute(
            "INSERT INTO tokens (token, plan_days, is_used) VALUES (?, ?, 0)",
            (code, plan_days)
        )
        tokens.append(code)

    conn.commit()
    conn.close()

    lines = [f"- `{t}`" for t in tokens]
    text = (
        "✅ *Tokens gerados com sucesso!*\n\n"
        f"📅 Plano: *{plan_days} dias*\n"
        f"🔢 Quantidade: *{quantity}*\n\n"
        "📋 Lista de tokens:\n" +
        "\n".join(lines)
    )
    bot.reply_to(message, text)


# =======================
# 📋 LISTAR TOKENS LIVRES (ADMIN)
# =======================

@bot.message_handler(commands=["tokens_livres"])
def cmd_tokens_livres(message):
    if check_block_and_reply(message):
        return
    if not is_admin(message):
        bot.reply_to(message, "❌ Comando restrito a administradores.")
        return
    if not ensure_in_channel_or_explain(message):
        return
    if register_command_and_check_flood(message):
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT token, plan_days
        FROM tokens
        WHERE is_used = 0
        ORDER BY plan_days ASC, id DESC
        LIMIT 50
        """
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.reply_to(message, "Não há tokens livres (não utilizados) no momento.")
        return

    # Agrupa por plano
    grouped = defaultdict(list)
    for token, plan_days in rows:
        grouped[plan_days].append(token)

    lines = [
        "🎟 *Tokens livres (não utilizados)*",
        "_Mostrando até 50 últimos, agrupados por plano._",
        ""
    ]

    counter = 1
    for plan_days in sorted(grouped.keys()):
        tokens = grouped[plan_days]
        lines.append(f"📅 *Plano {plan_days} dias* — {len(tokens)} token(s):")
        for t in tokens:
            lines.append(f"{counter}. `{t}`")
            counter += 1
        lines.append("")  # linha em branco entre planos

    text = "\n".join(lines).strip()
    bot.reply_to(message, text)


# =======================
# 🎬 COMANDOS DO BOT
# =======================

@bot.message_handler(commands=["start"])
def cmd_start(message):
    if check_block_and_reply(message):
        return
    if not ensure_in_channel_or_explain(message):
        return
    if register_command_and_check_flood(message):
        return

    user_id = get_or_create_user(message)
    logger.info(f"/start de user_id={user_id}, tg_id={message.from_user.id}")

    text = (
        f"👋 Olá {message.from_user.first_name or 'usuário'}!\n\n"
        f"Bem-vindo ao *Anexo Lookup BOT* 🚀\n\n"
        f"🧾 Seu cadastro foi criado no sistema.\n\n"
        f"Comandos principais:\n"
        f"🔑 `/ativar SEU_TOKEN` – ativar seu plano (7, 15 ou 30 dias)\n"
        f"ℹ️ `/status` – ver situação do seu acesso\n"
        f"📘 `/help` – ver lista de comandos\n\n"
        f"🔎 `/pesquisar dominio.com` – ver quantas linhas temos\n"
        f"📂 `/gerar dominio.com` – receber o .TXT completo\n\n"
        f"Seu acesso é privado e protegido 🔐"
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["meu_id"])
def cmd_meu_id(message):
    if check_block_and_reply(message):
        return
    if not ensure_in_channel_or_explain(message):
        return
    if register_command_and_check_flood(message):
        return

    tg_id = message.from_user.id
    username = message.from_user.username or "-"
    if username != "-":
        text = (
            "🆔 *Seus dados no Telegram:*\n\n"
            f"• ID numérico: `{tg_id}`\n"
            f"• Username: @{username}"
        )
    else:
        text = (
            "🆔 *Seus dados no Telegram:*\n\n"
            f"• ID numérico: `{tg_id}`\n"
            f"• Username: (nenhum)"
        )
    bot.reply_to(message, text)


@bot.message_handler(commands=["ativar"])
def cmd_ativar(message):
    if check_block_and_reply(message):
        return
    if not ensure_in_channel_or_explain(message):
        return
    if register_command_and_check_flood(message):
        return

    user_id = get_or_create_user(message)
    parts = message.text.split()

    if len(parts) < 2:
        bot.reply_to(
            message,
            "❌ Formato incorreto.\n\nUse assim:\n`/ativar SEU_TOKEN_AQUI`",
        )
        return

    token_input = parts[1].strip()

    # basic hardening – tamanho mínimo do token
    if len(token_input) < 10:
        bot.reply_to(
            message,
            "❌ Esse token parece inválido.\nConfira com o suporte.",
        )
        return

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, plan_days, is_used, used_by_user_id FROM tokens WHERE token = ?",
        (token_input,),
    )
    row = cur.fetchone()

    if not row:
        conn.close()

        _invalid_token_counts[message.from_user.id] += 1
        attempts = _invalid_token_counts[message.from_user.id]

        if attempts >= INVALID_TOKEN_THRESHOLD:
            temp_ban_user(
                message.from_user.id,
                INVALID_TOKEN_BAN_HOURS,
                "Tentativas repetidas de token inválido"
            )
            _invalid_token_counts.pop(message.from_user.id, None)
            bot.reply_to(
                message,
                "🚫 Muitas tentativas de token inválido.\n"
                "Seu acesso foi temporariamente bloqueado por 24 horas."
            )
        else:
            remaining = INVALID_TOKEN_THRESHOLD - attempts
            bot.reply_to(
                message,
                "⛔ Esse token *não existe* ou está digitado de forma incorreta.\n"
                f"Você ainda tem *{remaining}* tentativa(s) antes de um bloqueio temporário."
            )

        logger.warning(f"Tentativa de ativar token inválido: {token_input} (user_id={user_id})")
        return

    token_id, plan_days, is_used, used_by_user_id = row

    if is_used:
        conn.close()
        temp_ban_user(
            message.from_user.id,
            INVALID_TOKEN_BAN_HOURS,
            "Tentativa de reutilizar token já ativado"
        )
        bot.reply_to(
            message,
            "🚫 Esse token *já foi usado* e não pode ser reutilizado.\n"
            "Bloqueio temporário aplicado. Se for um engano, fale com o suporte."
        )
        logger.warning(
            f"Token já usado tentando ativar: token_id={token_id}, user_id={user_id}"
        )
        return

    now = datetime.utcnow()
    expires = now + timedelta(days=plan_days)

    cur.execute(
        """
        UPDATE tokens
        SET is_used = 1,
            used_by_user_id = ?,
            activated_at = ?,
            expires_at = ?
        WHERE id = ?
        """,
        (user_id, now.isoformat(), expires.isoformat(), token_id),
    )
    conn.commit()
    conn.close()

    # reset contador de tentativas inválidas em caso de sucesso
    _invalid_token_counts.pop(message.from_user.id, None)

    logger.info(
        f"Token ativado com sucesso: token_id={token_id}, user_id={user_id}, dias={plan_days}"
    )

    text = (
        "✅ *Token ativado com sucesso!*\n\n"
        f"📅 Plano: *{plan_days} dias*\n"
        f"⏳ Expira em: `{expires.strftime('%d/%m/%Y %H:%M')} UTC`\n\n"
        "Agora você já pode usar os comandos liberados enquanto o plano estiver ativo."
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["status"])
def cmd_status(message):
    if check_block_and_reply(message):
        return
    if not ensure_in_channel_or_explain(message):
        return
    if register_command_and_check_flood(message):
        return

    user_id = get_or_create_user(message)
    info = get_active_token_info(user_id)

    if not info:
        bot.reply_to(
            message,
            "🔒 Você *não tem token ativo* no momento.\n\n"
            "Fale com o suporte para adquirir um plano de 7, 15 ou 30 dias.",
        )
        return

    expires = info["expires_at"]
    days_left = info["days_left"]
    plan_days = info["plan_days"]

    text = (
        "✅ *Você tem um token ativo!*\n\n"
        f"📅 Plano: *{plan_days} dias*\n"
        f"⏳ Expira em: `{expires.strftime('%d/%m/%Y %H:%M')} UTC`\n"
        f"📆 Dias restantes (aprox.): *{days_left}*"
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["help", "menu"])
def cmd_help(message):
    if check_block_and_reply(message):
        return
    if not ensure_in_channel_or_explain(message):
        return
    if register_command_and_check_flood(message):
        return

    text = (
        "📘 *Menu de comandos Anexo Lookup*\n\n"
        "`/start`  – iniciar ou reiniciar o bot\n"
        "`/meu_id` – ver seu ID no Telegram\n"
        "`/ativar SEU_TOKEN` – ativar seu plano (7, 15 ou 30 dias)\n"
        "`/status` – ver se seu token está ativo e quando expira\n\n"
        "🔎 `/pesquisar dominio.com` – ver quantas linhas temos da URL\n"
        "📂 `/gerar dominio.com` – receber o .TXT completo (com token ativo)\n"
    )
    bot.reply_to(message, text)


# =======================
# 🔍 /pesquisar
# =======================

@bot.message_handler(commands=["pesquisar"])
def cmd_pesquisar(message):
    if check_block_and_reply(message):
        return
    if not ensure_in_channel_or_explain(message):
        return
    if register_command_and_check_flood(message):
        return

    user_id = get_or_create_user(message)
    token_info = get_active_token_info(user_id)

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message,
            "❌ Formato incorreto.\n\nUse assim:\n`/pesquisar dominio.com`",
        )
        return

    raw_domain = parts[1]
    domain = normalize_domain(raw_domain)

    if not domain or "." not in domain or len(domain) > 255:
        bot.reply_to(
            message,
            "❌ Domínio inválido.\nTente algo como:\n`/pesquisar google.com`",
        )
        return

    # se NÃO tem token, aplica limite diário
    remaining_msg = ""
    if not token_info:
        allowed, remaining = update_free_search_counter(user_id)
        if not allowed:
            bot.reply_to(
                message,
                "⚠️ Você atingiu o limite *gratuito* de "
                f"{MAX_FREE_SEARCHES_PER_DAY} pesquisas hoje.\n\n"
                "Para pesquisas ilimitadas e gerar arquivos completos, "
                "ative um token com `/ativar SEU_TOKEN`.",
            )
            return
        remaining_msg = f"\n\n🔓 Pesquisas grátis restantes hoje: *{remaining}*"

    file_path = DATA_DIR / f"{domain}.txt"

    if not file_path.exists():
        log_missing_domain(user_id, domain)
        bot.reply_to(
            message,
            "🔍 Pesquisei na base e *não encontrei* esse domínio ainda.\n\n"
            "Sua pesquisa foi registrada para futura atualização da base.\n"
            "Se quiser prioridade, fale com o suporte.",
        )
        return

    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            line_count = sum(1 for _ in f)
    except Exception as e:
        logger.error(f"Erro lendo arquivo {file_path}: {e}")
        bot.reply_to(
            message,
            "⚠️ Tive um erro ao ler esse arquivo.\nAvise o suporte, por favor.",
        )
        return

    text = (
        f"✅ Encontrei *{line_count} linhas* para o domínio `{domain}`.\n"
        f"📄 Arquivo: `{file_path.name}`\n\n"
        f"Para receber o .TXT completo use:\n`/gerar {domain}` "
        f"(necessário ter token ativo)."
        f"{remaining_msg}"
    )
    bot.reply_to(message, text)


# =======================
# 📂 /gerar
# =======================

@bot.message_handler(commands=["gerar"])
def cmd_gerar(message):
    if check_block_and_reply(message):
        return
    if not ensure_in_channel_or_explain(message):
        return
    if register_command_and_check_flood(message):
        return

    user_id = get_or_create_user(message)
    token_info = get_active_token_info(user_id)

    if not token_info:
        bot.reply_to(
            message,
            "🔒 Esse comando é exclusivo para quem tem *token ativo*.\n\n"
            "Use `/status` para ver seu acesso ou `/ativar SEU_TOKEN`.",
        )
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message,
            "❌ Formato incorreto.\n\nUse assim:\n`/gerar dominio.com`",
        )
        return

    raw_domain = parts[1]
    domain = normalize_domain(raw_domain)

    if not domain or "." not in domain or len(domain) > 255:
        bot.reply_to(
            message,
            "❌ Domínio inválido.\nTente algo como:\n`/gerar google.com`",
        )
        return

    file_path = DATA_DIR / f"{domain}.txt"

    if not file_path.exists():
        log_missing_domain(user_id, domain)
        bot.reply_to(
            message,
            "⚠️ Ainda não tenho esse domínio em TXT completo.\n"
            "Sua solicitação foi registrada para atualização da base.",
        )
        return

    # verifica tamanho do arquivo
    try:
        size_mb = file_path.stat().st_size / (1024 * 1024)
    except Exception as e:
        logger.error(f"Erro ao obter tamanho do arquivo {file_path}: {e}")
        size_mb = 0

    if size_mb > MAX_FILE_SIZE_MB:
        bot.reply_to(
            message,
            "⚠️ O arquivo desse domínio é muito grande para envio direto pelo Telegram.\n"
            "Fale com o suporte para receber por outro meio (ex: link ou e-mail).",
        )
        return

    try:
        with file_path.open("rb") as f:
            bot.send_document(
                chat_id=message.chat.id,
                document=f,
                visible_file_name=file_path.name,
                caption=f"📂 Arquivo para `{domain}`",
            )
    except Exception as e:
        logger.error(f"Erro enviando arquivo {file_path}: {e}")
        bot.reply_to(
            message,
            "⚠️ Tive um erro ao enviar o arquivo.\nAvise o suporte, por favor.",
        )


@bot.message_handler(commands=["suporte"])
def cmd_suporte(message):
    if check_block_and_reply(message):
        return
    if not ensure_in_channel_or_explain(message):
        return
    if register_command_and_check_flood(message):
        return

    text = (
        "🧑‍💻 *Suporte ANEXO*\n\n"
        "Para dúvidas, compra de token ou problemas de acesso,\n"
        "fale diretamente com o administrador no privado:\n\n"
        "@AnexoEsc"
    )
    bot.reply_to(message, text)


# =======================
# 👮 COMANDOS ADMIN (tempban / unban / banlist)
# =======================

@bot.message_handler(commands=["tempban"])
def cmd_tempban(message):
    if check_block_and_reply(message):
        return
    if not is_admin(message):
        bot.reply_to(message, "❌ Comando restrito a administradores.")
        return
    if register_command_and_check_flood(message):
        return

    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        bot.reply_to(message, "Uso: `/tempban <telegram_id> <horas> [motivo]`")
        return
    try:
        tg = int(parts[1])
        hours = float(parts[2])
        reason = parts[3] if len(parts) >= 4 else "Manual (admin)"
        temp_ban_user(tg, hours, reason)
        bot.reply_to(message, f"✅ Usuário `{tg}` bloqueado por *{hours}h*.\nMotivo: {reason}")
    except Exception as e:
        bot.reply_to(message, f"Erro: `{e}`")


@bot.message_handler(commands=["unban"])
def cmd_unban(message):
    if check_block_and_reply(message):
        return
    if not is_admin(message):
        bot.reply_to(message, "❌ Comando restrito a administradores.")
        return
    if register_command_and_check_flood(message):
        return

    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Uso: `/unban <telegram_id>`")
        return
    try:
        tg = int(parts[1])
        unban_user(tg)
        bot.reply_to(message, f"✅ Usuário `{tg}` desbanido.")
    except Exception as e:
        bot.reply_to(message, f"Erro: `{e}`")


@bot.message_handler(commands=["banlist"])
def cmd_banlist(message):
    if check_block_and_reply(message):
        return
    if not is_admin(message):
        bot.reply_to(message, "❌ Comando restrito a administradores.")
        return
    if register_command_and_check_flood(message):
        return

    rows = list_active_bans()
    if not rows:
        bot.reply_to(message, "Nenhum bloqueio temporário ativo.")
        return
    text_lines = []
    for r in rows:
        tg_id, reason, banned_at, ban_until = r
        ban_until_dt = datetime.fromisoformat(ban_until)
        secs = max(int((ban_until_dt - datetime.utcnow()).total_seconds()), 0)
        text_lines.append(f"`{tg_id}` — {reason} — expira em {secs//3600}h {(secs%3600)//60}m")
    bot.reply_to(message, "🔒 *Bloqueios ativos:*\n" + "\n".join(text_lines))


# =======================
# 🧱 HANDLER PADRÃO (fallback)
# =======================

@bot.message_handler(func=lambda m: True, content_types=["text"])
def cmd_default(message):
    if check_block_and_reply(message):
        return
    if not ensure_in_channel_or_explain(message):
        return
    if register_command_and_check_flood(message):
        return

    bot.reply_to(
        message,
        "🤖 Não reconheci esse comando.\n\nUse `/help` para ver a lista de comandos disponíveis.",
    )


# =======================
# 🚀 LOOP PRINCIPAL
# =======================

if __name__ == "__main__":
    print("✅ BOT iniciado com sucesso! Aguardando comandos no Telegram...")
    logger.info("Bot iniciado.")

    while True:
        try:
            # timeout e long_polling_timeout ajudam a manter a conexão estável
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except KeyboardInterrupt:
            print("🛑 Bot interrompido manualmente (Ctrl+C). Saindo...")
            logger.info("Bot interrompido manualmente.")
            break
        except Exception as e:
            logger.error(f"Erro no polling: {e}")
            print("⚠️ Erro de conexão com o Telegram. Tentando reconectar em 5 segundos...")
            time.sleep(5)
