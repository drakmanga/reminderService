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
        "─── Una tantum ───\n"
        "  • oggi alle 14:30\n"
        "  • domani alle 9\n"
        "  • dopodomani alle 21\n"
        "  • stasera di …\n"
        "  • stasera alle 20:30 di …\n"
        "  • oggi pomeriggio di …\n"
        "  • stamattina di …\n"
        "  • stanotte di …\n"
        "  • domani mattina di …\n"
        "  • domani pomeriggio di …\n"
        "  • domani sera alle 22 di …\n"
        "  • tra/fra 2 ore\n"
        "  • tra/fra 30 minuti\n"
        "  • tra/fra mezz'ora\n"
        "  • venerdì alle 10\n"
        "  • il 15 aprile alle 9\n\n"
        "─── Ricorrenti (ogni …) ───\n"
        "  • ogni giorno alle 8\n"
        "  • ogni venerdì alle 9\n"
        "  • ogni settimana il lunedì alle 10\n"
        "  • ogni inizio mese alle 9\n"
        "  • ogni fine mese alle 18\n"
        "  • ogni 18 del mese alle 10\n"
        "  • ogni mese il 5 alle 9\n"
        "  • ogni 3 mesi il 1 alle 9\n"
        "  • ogni anno il 15 marzo alle 9\n\n"
        "Esempi:\n"
        "  /ricordami domani alle 9 di contattare Mario\n"
        "  /ricordami fra mezz'ora di controllare il forno\n"
        "  /ricordami ogni venerdì alle 9 di chiamare il cliente\n"
        "  /ricordami ogni 18 del mese alle 10 di pagare l'affitto\n"
        "  /ricordami ogni inizio mese alle 9 di controllare le spese"
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

    # 3b. domani mattina / domani pomeriggio / domani sera [alle HH[:MM]]
    if dt is None:
        m = re.match(rf'domani\s+(mattina|pomeriggio|sera)(?:\s+alle\s+{TIME_PAT})?\s*(.*)', text, FLAGS)
        if m:
            slot = m.group(1).lower()
            default_h = {'mattina': 9, 'pomeriggio': 15, 'sera': 21}[slot]
            h, mn = parse_hm(m.group(2), m.group(3)) if m.group(2) else (default_h, 0)
            if h is not None:
                base = now + timedelta(days=1)
                dt = make_dt(base.year, base.month, base.day, h, mn)
                rest = m.group(4)

    # 3c. stasera [alle HH[:MM]]
    if dt is None:
        m = re.match(rf'stasera(?:\s+alle\s+{TIME_PAT})?\s*(.*)', text, FLAGS)
        if m:
            h, mn = parse_hm(m.group(1), m.group(2)) if m.group(1) else (21, 0)
            if h is not None:
                dt = make_dt(now.year, now.month, now.day, h, mn)
                rest = m.group(3)

    # 3d. oggi pomeriggio [alle HH[:MM]]
    if dt is None:
        m = re.match(rf'oggi\s+pomeriggio(?:\s+alle\s+{TIME_PAT})?\s*(.*)', text, FLAGS)
        if m:
            h, mn = parse_hm(m.group(1), m.group(2)) if m.group(1) else (15, 0)
            if h is not None:
                dt = make_dt(now.year, now.month, now.day, h, mn)
                rest = m.group(3)

    # 3e. stamattina / stamani [alle HH[:MM]]
    if dt is None:
        m = re.match(rf'(?:stamattina|stamani)(?:\s+alle\s+{TIME_PAT})?\s*(.*)', text, FLAGS)
        if m:
            h, mn = parse_hm(m.group(1), m.group(2)) if m.group(1) else (9, 0)
            if h is not None:
                dt = make_dt(now.year, now.month, now.day, h, mn)
                rest = m.group(3)

    # 3f. stanotte [alle HH[:MM]]
    if dt is None:
        m = re.match(rf'stanotte(?:\s+alle\s+{TIME_PAT})?\s*(.*)', text, FLAGS)
        if m:
            h, mn = parse_hm(m.group(1), m.group(2)) if m.group(1) else (23, 0)
            if h is not None:
                dt = make_dt(now.year, now.month, now.day, h, mn)
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


def _parse_recurrence(text: str):
    """
    Parsa 'ogni <spec> di <messaggio>' e restituisce (dt_utc, recurrence_json_str, message) o None.

    Pattern supportati:
      ogni giorno alle HH[:MM]
      ogni X giorni alle HH[:MM]
      ogni <weekday> alle HH[:MM]
      ogni settimana [il <weekday>] alle HH[:MM]
      ogni inizio mese [alle HH[:MM]]
      ogni fine mese [alle HH[:MM]]
      ogni DD del mese [alle HH[:MM]]
      ogni mese [il DD] [alle HH[:MM]]
      ogni X mesi [il DD] [alle HH[:MM]]
      ogni anno [il DD mese] [alle HH[:MM]]
    """
    import json as _json
    from dateutil.relativedelta import relativedelta
    import calendar

    if not re.match(r'ogni\b', text, re.IGNORECASE):
        return None

    body = re.sub(r'^ogni\s+', '', text, flags=re.IGNORECASE).strip()

    TZ = pytz.timezone("Europe/Rome")
    now = datetime.now(TZ)
    FLAGS = re.IGNORECASE | re.DOTALL
    TIME_PAT = r'(\d{1,2})(?::(\d{2}))?'

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

    MONTHS_IT = {
        'gennaio': 1, 'febbraio': 2, 'marzo': 3, 'aprile': 4,
        'maggio': 5, 'giugno': 6, 'luglio': 7, 'agosto': 8,
        'settembre': 9, 'ottobre': 10, 'novembre': 11, 'dicembre': 12,
        'gen': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'mag': 5, 'giu': 6,
        'lug': 7, 'ago': 8, 'set': 9, 'ott': 10, 'nov': 11, 'dic': 12,
    }
    month_pat = '|'.join(sorted(MONTHS_IT.keys(), key=len, reverse=True))

    def make_dt(year, month, day, hour, minute):
        try:
            return TZ.localize(datetime(year, month, day, hour, minute))
        except (ValueError, OverflowError):
            return None

    def parse_hm(h_str, m_str):
        if h_str is None:
            return None, None
        h, m = int(h_str), int(m_str) if m_str else 0
        return (h, m) if (0 <= h <= 23 and 0 <= m <= 59) else (None, None)

    def extract_msg(raw: str):
        s = raw.strip()
        if re.match(r'di\s+', s, re.IGNORECASE):
            s = s[s.index(' ') + 1:].strip()
        return s if s else None

    def next_dom(day: int, h: int, mn: int):
        """Prossima occorrenza del giorno del mese."""
        c = make_dt(now.year, now.month, day, h, mn)
        if c is None or c <= now:
            nm = now + relativedelta(months=1)
            c = make_dt(nm.year, nm.month, day, h, mn)
        return c

    def next_weekday(target_dow: int, h: int, mn: int):
        ahead = (target_dow - now.weekday()) % 7 or 7
        b = now + timedelta(days=ahead)
        return make_dt(b.year, b.month, b.day, h, mn)

    def today_or_tomorrow(h: int, mn: int):
        c = make_dt(now.year, now.month, now.day, h, mn)
        if c is None or c <= now:
            t = now + timedelta(days=1)
            c = make_dt(t.year, t.month, t.day, h, mn)
        return c

    dt = rec = rest = None

    # 1. ogni giorno alle HH[:MM]
    m = re.match(rf'^giorno\s+alle\s+{TIME_PAT}\s*(.*)', body, FLAGS)
    if m:
        h, mn = parse_hm(m.group(1), m.group(2))
        if h is not None:
            dt = today_or_tomorrow(h, mn)
            rec, rest = {"type": "daily", "interval": 1}, m.group(3)

    # 2. ogni X giorni alle HH[:MM]
    if dt is None:
        m = re.match(rf'^(\d+)\s+giorn[oi]\s+alle\s+{TIME_PAT}\s*(.*)', body, FLAGS)
        if m:
            iv, h, mn = int(m.group(1)), *parse_hm(m.group(2), m.group(3))
            if h is not None and iv >= 1:
                dt = today_or_tomorrow(h, mn)
                rec, rest = {"type": "daily", "interval": iv}, m.group(4)

    # 3. ogni <weekday> alle HH[:MM]
    if dt is None:
        m = re.match(rf'^({day_pat})\s+alle\s+{TIME_PAT}\s*(.*)', body, FLAGS)
        if m:
            target = DAYS.get(m.group(1).lower())
            h, mn = parse_hm(m.group(2), m.group(3))
            if h is not None and target is not None:
                dt = next_weekday(target, h, mn)
                rec, rest = {"type": "weekly", "interval": 1}, m.group(4)

    # 4. ogni settimana [il <weekday>] alle HH[:MM]
    if dt is None:
        m = re.match(rf'^settimana\s+(?:il\s+)?({day_pat})\s+alle\s+{TIME_PAT}\s*(.*)', body, FLAGS)
        if m:
            target = DAYS.get(m.group(1).lower())
            h, mn = parse_hm(m.group(2), m.group(3))
            if h is not None and target is not None:
                dt = next_weekday(target, h, mn)
                rec, rest = {"type": "weekly", "interval": 1}, m.group(4)

    # 5. ogni settimana alle HH[:MM] (stesso giorno)
    if dt is None:
        m = re.match(rf'^settimana\s+alle\s+{TIME_PAT}\s*(.*)', body, FLAGS)
        if m:
            h, mn = parse_hm(m.group(1), m.group(2))
            if h is not None:
                dt = today_or_tomorrow(h, mn)
                rec, rest = {"type": "weekly", "interval": 1}, m.group(3)

    # 6. ogni inizio mese [alle HH[:MM]]
    if dt is None:
        m = re.match(rf'^inizio\s+mese(?:\s+alle\s+{TIME_PAT})?\s*(.*)', body, FLAGS)
        if m:
            h, mn = parse_hm(m.group(1), m.group(2))
            h = h if h is not None else 9
            mn = mn if mn is not None else 0
            dt = next_dom(1, h, mn)
            rec, rest = {"type": "monthly", "interval": 1}, m.group(3)

    # 7. ogni fine mese [alle HH[:MM]]
    if dt is None:
        m = re.match(rf'^fine\s+mese(?:\s+alle\s+{TIME_PAT})?\s*(.*)', body, FLAGS)
        if m:
            h, mn = parse_hm(m.group(1), m.group(2))
            h = h if h is not None else 9
            mn = mn if mn is not None else 0
            last = calendar.monthrange(now.year, now.month)[1]
            c = make_dt(now.year, now.month, last, h, mn)
            if c is None or c <= now:
                nm = now + relativedelta(months=1)
                last = calendar.monthrange(nm.year, nm.month)[1]
                c = make_dt(nm.year, nm.month, last, h, mn)
            dt, rec, rest = c, {"type": "monthly", "interval": 1}, m.group(3)

    # 8. ogni DD del mese [alle HH[:MM]]
    if dt is None:
        m = re.match(rf'^(\d{{1,2}})\s+del\s+mese(?:\s+alle\s+{TIME_PAT})?\s*(.*)', body, FLAGS)
        if m:
            day_n = int(m.group(1))
            h, mn = parse_hm(m.group(2), m.group(3))
            h = h if h is not None else 9
            mn = mn if mn is not None else 0
            if 1 <= day_n <= 31:
                dt = next_dom(day_n, h, mn)
                rec, rest = {"type": "monthly", "interval": 1}, m.group(4)

    # 9. ogni mese [il DD] [alle HH[:MM]]
    if dt is None:
        m = re.match(rf'^mese(?:\s+il\s+(\d{{1,2}}))?(?:\s+alle\s+{TIME_PAT})?\s*(.*)', body, FLAGS)
        if m:
            day_n = int(m.group(1)) if m.group(1) else 1
            h, mn = parse_hm(m.group(2), m.group(3))
            h = h if h is not None else 9
            mn = mn if mn is not None else 0
            if 1 <= day_n <= 31:
                dt = next_dom(day_n, h, mn)
                rec, rest = {"type": "monthly", "interval": 1}, m.group(4)

    # 10. ogni X mesi [il DD] [alle HH[:MM]]
    if dt is None:
        m = re.match(rf'^(\d+)\s+mes[ei](?:\s+il\s+(\d{{1,2}}))?(?:\s+alle\s+{TIME_PAT})?\s*(.*)', body, FLAGS)
        if m:
            iv = int(m.group(1))
            day_n = int(m.group(2)) if m.group(2) else 1
            h, mn = parse_hm(m.group(3), m.group(4))
            h = h if h is not None else 9
            mn = mn if mn is not None else 0
            if iv >= 1 and 1 <= day_n <= 31:
                dt = next_dom(day_n, h, mn)
                rec, rest = {"type": "monthly", "interval": iv}, m.group(5)

    # 11. ogni anno [il DD mese] [alle HH[:MM]]
    if dt is None:
        m = re.match(
            rf'^anno(?:\s+il\s+(\d{{1,2}})\s+({month_pat}))?(?:\s+alle\s+{TIME_PAT})?\s*(.*)',
            body, FLAGS
        )
        if m:
            day_n = int(m.group(1)) if m.group(1) else now.day
            month_n = MONTHS_IT.get(m.group(2).lower()) if m.group(2) else now.month
            h, mn = parse_hm(m.group(3), m.group(4))
            h = h if h is not None else 9
            mn = mn if mn is not None else 0
            if month_n:
                c = make_dt(now.year, month_n, day_n, h, mn)
                if c is None or c <= now:
                    c = make_dt(now.year + 1, month_n, day_n, h, mn)
                dt, rec, rest = c, {"type": "yearly", "interval": 1}, m.group(5)

    if dt is None or rec is None or rest is None:
        return None

    msg = extract_msg(rest)
    if not msg:
        return None

    return dt.astimezone(timezone.utc), _json.dumps(rec), msg


def _recurrence_label(rec_json: str) -> str:
    """Restituisce una descrizione leggibile della ricorrenza."""
    import json as _json
    try:
        r = _json.loads(rec_json)
        t, iv = r.get("type"), int(r.get("interval", 1))
        if t == "daily":
            return "ogni giorno" if iv == 1 else f"ogni {iv} giorni"
        if t == "weekly":
            return "ogni settimana" if iv == 1 else f"ogni {iv} settimane"
        if t == "monthly":
            return "ogni mese" if iv == 1 else f"ogni {iv} mesi"
        if t == "yearly":
            return "ogni anno"
        if t == "hourly":
            return "ogni ora" if iv == 1 else f"ogni {iv} ore"
    except Exception:
        pass
    return "ricorrente"


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
            "Scrivi /help per vedere tutti i formati supportati."
        )
        return

    # Branch ricorrente
    rec_json = None
    if re.match(r'ogni\b', args, re.IGNORECASE):
        result = _parse_recurrence(args)
        if result is None:
            await update.message.reply_text(
                "❌ Non riesco a capire la ricorrenza.\n\n"
                "Formati supportati:\n"
                "• ogni giorno alle 8 di …\n"
                "• ogni venerdì alle 9 di …\n"
                "• ogni settimana il lunedì alle 10 di …\n"
                "• ogni inizio mese alle 9 di …\n"
                "• ogni fine mese alle 18 di …\n"
                "• ogni 18 del mese alle 10 di …\n"
                "• ogni mese il 5 alle 9 di …\n"
                "• ogni 3 mesi il 1 alle 9 di …\n"
                "• ogni anno il 15 marzo alle 9 di …"
            )
            return
        dt_utc, rec_json, message = result
    else:
        result = _parse_reminder(args)
        if result is None:
            await update.message.reply_text(
                "❌ Non riesco a capire la data/ora.\n\n"
                "Formati supportati:\n"
                "• domani alle 9 di …\n"
                "• oggi alle 18:30 di …\n"
                "• dopodomani alle 21 di …\n"
                "• stasera di … (→ 21:00)\n"
                "• oggi pomeriggio di … (→ 15:00)\n"
                "• stamattina di … (→ 9:00)\n"
                "• stanotte di … (→ 23:00)\n"
                "• domani mattina/pomeriggio/sera di …\n"
                "• tra/fra 2 ore di …\n"
                "• lunedì alle 10 di …\n"
                "• il 5 marzo alle 9 di …\n\n"
                "Per reminder ricorrenti: ogni venerdì alle 9 di …"
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
        """INSERT INTO reminders (user_id, message, next_execution, recurrence_json, status)
           VALUES (?, ?, ?, ?, 'pending')""",
        (user["id"], safe_message, dt_utc.isoformat(), rec_json),
    )
    conn.commit()
    conn.close()

    TZ = pytz.timezone("Europe/Rome")
    local_dt = dt_utc.astimezone(TZ)
    formatted = local_dt.strftime("%d/%m/%Y alle %H:%M")

    logger.info(f"Reminder creato via bot: '{safe_message}' per {formatted} rec={rec_json}")
    db_log("INFO", f"Reminder creato via bot: '{safe_message}' per {formatted}")

    if rec_json:
        label = _recurrence_label(rec_json)
        reply = (
            f"✅ Reminder ricorrente aggiunto!\n"
            f"🔁 {label}\n"
            f"📅 Prima esecuzione: {formatted}\n"
            f"📝 {message}"
        )
    else:
        reply = (
            f"✅ Reminder aggiunto!\n"
            f"📅 {formatted}\n"
            f"📝 {message}"
        )
    await update.message.reply_text(reply)


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

