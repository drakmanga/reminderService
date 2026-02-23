import sys
import asyncio
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import yaml
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
        "Riceverai notifiche con il pulsante ✔ per confermare."
    )


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

