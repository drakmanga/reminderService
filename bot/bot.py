import sys
import asyncio
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import yaml
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from scheduler.log_manager import get_logger, db_log
from backend.database import get_connection, get_telegram_config
from backend.routers.confirm import _apply_confirmation

logger = get_logger("bot.telegram")

CONFIG_PATH = BASE_DIR / "config.yaml"
with open(CONFIG_PATH, "r") as f:
    CONFIG = yaml.safe_load(f)

POLLING_INTERVAL = CONFIG.get("polling_interval_sec", 2)


def _get_authorized_ids() -> set:
    """Ricarica i chat ID autorizzati dal DB (hot-reload dalla UI)."""
    cfg = get_telegram_config()
    return set(cfg.get("chat_ids", []))


def _is_authorized(update: Update) -> bool:
    cid = update.effective_chat.id if update.effective_chat else None
    return cid in _get_authorized_ids()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await update.message.reply_text("⛔ Non autorizzato.")
        return
    await update.message.reply_text(
        "👋 Ciao! Sono il tuo bot Reminder.\n"
        "Riceverai notifiche con il pulsante ✔ per confermare.\n\n"
        "Scrivi /help per vedere i comandi disponibili."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await update.message.reply_text("⛔ Non autorizzato.")
        return
    await update.message.reply_text(
        "📋 Comandi disponibili:\n\n"
        "/start — Messaggio di benvenuto\n"
        "/help — Mostra questo messaggio\n"
        "/reminders — Lista dei reminder attivi\n"
        "/ricordami <quando> di <cosa> — Crea un nuovo reminder\n\n"
        "Formati supportati per <quando>:\n"
        "  • oggi alle 14:30\n"
        "  • domani alle 9\n"
        "  • dopodomani alle 21\n"
        "  • tra/fra 2 ore\n"
        "  • tra/fra 30 minuti\n"
        "  • tra/fra mezz'ora\n"
        "  • lunedì alle 10\n"
        "  • il 15 aprile alle 9\n\n"
        "Esempi:\n"
        "  /ricordami domani alle 9 di contattare Mario\n"
        "  /ricordami fra mezz'ora di controllare il forno\n"
        "  /ricordami venerdì alle 20 di cena"
    )


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await update.message.reply_text("⛔ Non autorizzato.")
        return

    conn = get_connection()
    user = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if not user:
        conn.close()
        await update.message.reply_text("❌ Nessun utente configurato nel sistema.")
        return

    rows = conn.execute(
        """SELECT message, next_execution, status
           FROM reminders
           WHERE user_id = ? AND status NOT IN ('deleted', 'resolved', 'completed')
           ORDER BY next_execution ASC""",
        (user["id"],),
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 Nessun reminder attivo.")
        return

    TZ = pytz.timezone("Europe/Rome")
    now = datetime.now(TZ)

    STATUS_ICON = {
        "pending": "🕐",
        "sent": "📨",
        "paused": "⏸",
    }

    lines = ["📋 Reminder attivi:\n"]
    for row in rows:
        dt_str = row["next_execution"]
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local_dt = dt.astimezone(TZ)
            today = now.date()
            d = local_dt.date()
            if d == today:
                when = f"oggi alle {local_dt.strftime('%H:%M')}"
            elif d == today + timedelta(days=1):
                when = f"domani alle {local_dt.strftime('%H:%M')}"
            else:
                when = local_dt.strftime("%d/%m/%Y alle %H:%M")
        except Exception:
            when = dt_str

        icon = STATUS_ICON.get(row["status"], "•")
        lines.append(f"{icon} {when}\n   {row['message']}")

    await update.message.reply_text("\n".join(lines))


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        await query.edit_message_text("⛔ Non autorizzato.")
        return

    data = query.data
    if not data.startswith("confirm:"):
        return

    try:
        execution_id = int(data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Dati non validi.")
        return

    conn = get_connection()
    execution = conn.execute(
        "SELECT * FROM executions WHERE id = ?", (execution_id,)
    ).fetchone()

    if not execution:
        conn.close()
        await query.edit_message_text("❌ Reminder non trovato.")
        return

    if execution["confirmed"]:
        conn.close()
        await query.edit_message_text("✅ Già confermato in precedenza.")
        return

    # Usa la logica centralizzata di conferma (gestisce ricorrenza, resolved, ecc.)
    _apply_confirmation(conn, execution["reminder_id"], execution_id)
    conn.close()

    logger.info(f"Execution {execution_id} confermata via bot")
    db_log("INFO", f"Execution {execution_id} confermata via bot")

    await query.edit_message_text("✅ Reminder confermato! Grazie.")


def _parse_reminder(text: str):
    """
    Parsa il testo di /ricordami e restituisce (datetime_utc, message) o None.

    Formati supportati (case-insensitive):
      - "domani alle HH[:MM] [di] messaggio"
      - "oggi alle HH[:MM] [di] messaggio"
      - "dopodomani alle HH[:MM] [di] messaggio"
      - "tra X minuto/i ora/e giorno/i [di] messaggio"
      - "lunedì/martedì/... alle HH[:MM] [di] messaggio"
      - "[il] 5 marzo [YYYY] [alle HH[:MM]] [di] messaggio"
    """
    TZ = pytz.timezone("Europe/Rome")
    now = datetime.now(TZ)
    FLAGS = re.IGNORECASE | re.DOTALL
    TIME_PAT = r'(\d{1,2})(?::(\d{2}))?'

    def make_dt(year, month, day, hour, minute):
        try:
            return TZ.localize(datetime(year, month, day, hour, minute))
        except (ValueError, OverflowError):
            return None

    def parse_hm(h_str, m_str):
        h, m = int(h_str), int(m_str) if m_str else 0
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
        return None, None

    def extract_msg(raw: str) -> str:
        s = raw.strip()
        if re.match(r'di\s+', s, re.IGNORECASE):
            s = s[s.index(' ') + 1:].strip()
        return s if s else None

    dt = None
    rest = None

    # 1. domani alle HH[:MM]
    m = re.match(rf'domani\s+alle\s+{TIME_PAT}\s*(.*)', text, FLAGS)
    if m:
        h, mn = parse_hm(m.group(1), m.group(2))
        if h is not None:
            base = now + timedelta(days=1)
            dt = make_dt(base.year, base.month, base.day, h, mn)
            rest = m.group(3)

    # 2. oggi alle HH[:MM]
    if dt is None:
        m = re.match(rf'oggi\s+alle\s+{TIME_PAT}\s*(.*)', text, FLAGS)
        if m:
            h, mn = parse_hm(m.group(1), m.group(2))
            if h is not None:
                dt = make_dt(now.year, now.month, now.day, h, mn)
                rest = m.group(3)

    # 3. dopodomani alle HH[:MM]
    if dt is None:
        m = re.match(rf'dopodomani\s+alle\s+{TIME_PAT}\s*(.*)', text, FLAGS)
        if m:
            h, mn = parse_hm(m.group(1), m.group(2))
            if h is not None:
                base = now + timedelta(days=2)
                dt = make_dt(base.year, base.month, base.day, h, mn)
                rest = m.group(3)

    # 4. tra/fra mezz'ora / mezzora
    if dt is None:
        m = re.match(r"(?:tra|fra)\s+mezz'?ora\s*(.*)", text, FLAGS)
        if m:
            dt = now + timedelta(minutes=30)
            rest = m.group(1)

    # 4b. tra/fra X minuto/i, ora/e, giorno/i
    if dt is None:
        m = re.match(r'(?:tra|fra)\s+(\d+)\s+(minut[oi]|or[ae]|giorn[oi])\s*(.*)', text, FLAGS)
        if m:
            n, unit = int(m.group(1)), m.group(2).lower()
            rest = m.group(3)
            if unit.startswith('minut'):
                dt = now + timedelta(minutes=n)
            elif unit.startswith('or'):
                dt = now + timedelta(hours=n)
            elif unit.startswith('giorn'):
                dt = now + timedelta(days=n)

    # 5. Giorno della settimana alle HH[:MM]
    if dt is None:
        DAYS = {
            'lunedì': 0, 'lunedi': 0,
            'martedì': 1, 'martedi': 1,
            'mercoledì': 2, 'mercoledi': 2,
            'giovedì': 3, 'giovedi': 3,
            'venerdì': 4, 'venerdi': 4,
            'sabato': 5,
            'domenica': 6,
        }
        day_pat = '|'.join(sorted(DAYS.keys(), key=len, reverse=True))
        m = re.match(rf'({day_pat})\s+alle\s+{TIME_PAT}\s*(.*)', text, FLAGS)
        if m:
            day_name = m.group(1).lower()
            h, mn = parse_hm(m.group(2), m.group(3))
            if h is not None:
                target_dow = DAYS.get(day_name)
                if target_dow is not None:
                    days_ahead = (target_dow - now.weekday()) % 7 or 7
                    base = now + timedelta(days=days_ahead)
                    dt = make_dt(base.year, base.month, base.day, h, mn)
                    rest = m.group(4)

    # 6. [il] DD mese [YYYY] [alle HH[:MM]]
    if dt is None:
        MONTHS = {
            'gennaio': 1, 'febbraio': 2, 'marzo': 3, 'aprile': 4,
            'maggio': 5, 'giugno': 6, 'luglio': 7, 'agosto': 8,
            'settembre': 9, 'ottobre': 10, 'novembre': 11, 'dicembre': 12,
            'gen': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'mag': 5, 'giu': 6,
            'lug': 7, 'ago': 8, 'set': 9, 'ott': 10, 'nov': 11, 'dic': 12,
        }
        month_pat = '|'.join(sorted(MONTHS.keys(), key=len, reverse=True))
        m = re.match(
            rf'(?:il\s+)?(\d{{1,2}})\s+({month_pat})(?:\s+(\d{{4}}))?'
            rf'(?:\s+alle\s+{TIME_PAT})?\s*(.*)',
            text, FLAGS
        )
        if m:
            day_n = int(m.group(1))
            month_n = MONTHS.get(m.group(2).lower())
            year_n = int(m.group(3)) if m.group(3) else now.year
            h = int(m.group(4)) if m.group(4) else 9
            mn = int(m.group(5)) if m.group(5) else 0
            rest = m.group(6)
            if month_n:
                candidate = make_dt(year_n, month_n, day_n, h, mn)
                if candidate and candidate < now and not m.group(3):
                    candidate = make_dt(year_n + 1, month_n, day_n, h, mn)
                dt = candidate

    if dt is None or rest is None:
        return None

    msg = extract_msg(rest)
    if not msg:
        return None

    return dt.astimezone(timezone.utc), msg


async def ricordami_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        await update.message.reply_text("⛔ Non autorizzato.")
        return

    text = update.message.text or ""
    # Rimuovi il comando (es. "/ricordami" o "/ricordami@botname")
    args = re.sub(r'^/ricordami(?:@\S+)?\s*', '', text, flags=re.IGNORECASE).strip()

    if not args:
        await update.message.reply_text(
            "❌ Sintassi: /ricordami <quando> di <cosa>\n\n"
            "Esempi:\n"
            "• /ricordami domani alle 9 di contattare Mario\n"
            "• /ricordami oggi alle 18:30 di comprare il pane\n"
            "• /ricordami tra 2 ore di controllare il forno\n"
            "• /ricordami venerdì alle 14 di riunione team\n"
            "• /ricordami il 15 aprile alle 10 di visita medica"
        )
        return

    result = _parse_reminder(args)
    if result is None:
        await update.message.reply_text(
            "❌ Non riesco a capire la data/ora.\n\n"
            "Formati supportati:\n"
            "• domani alle 9 di …\n"
            "• oggi alle 18:30 di …\n"
            "• dopodomani alle 21 di …\n"
            "• tra 2 ore di …\n"
            "• lunedì alle 10 di …\n"
            "• il 5 marzo alle 9 di …"
        )
        return

    dt_utc, message = result

    conn = get_connection()
    user = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if not user:
        conn.close()
        await update.message.reply_text("❌ Nessun utente configurato nel sistema.")
        return

    import html as _html
    safe_message = _html.escape(message[:500])

    conn.execute(
        "INSERT INTO reminders (user_id, message, next_execution, status) VALUES (?, ?, ?, 'pending')",
        (user["id"], safe_message, dt_utc.isoformat()),
    )
    conn.commit()
    conn.close()

    TZ = pytz.timezone("Europe/Rome")
    local_dt = dt_utc.astimezone(TZ)
    formatted = local_dt.strftime("%d/%m/%Y alle %H:%M")

    logger.info(f"Reminder creato via bot: '{safe_message}' per {formatted}")
    db_log("INFO", f"Reminder creato via bot: '{safe_message}' per {formatted}")

    await update.message.reply_text(
        f"✅ Reminder aggiunto!\n"
        f"📅 {formatted}\n"
        f"📝 {message}"
    )


def start_bot():
    """Avvia il bot in polling (blocca il thread). Ricarica il token dal DB."""
    cfg = get_telegram_config()
    token = cfg.get("telegram_token", "")

    if not token or token == "BOT_TOKEN_QUI":
        logger.warning("Token Telegram non configurato, bot non avviato.")
        return

    async def _run():
        app = ApplicationBuilder().token(token).build()
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("reminders", reminders_command))
        app.add_handler(CommandHandler("ricordami", ricordami_command))
        app.add_handler(CallbackQueryHandler(callback_handler))

        await app.initialize()
        await app.start()
        await app.updater.start_polling(poll_interval=POLLING_INTERVAL)
        logger.info("Bot Telegram avviato in polling")

        # Tieni vivo il thread finché l'applicazione gira
        while app.running:
            await asyncio.sleep(1)

        await app.updater.stop()
        await app.stop()
        await app.shutdown()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    except Exception as e:
        logger.error(f"Bot arrestato: {e}")
    finally:
        loop.close()

