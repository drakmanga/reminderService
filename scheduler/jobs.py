import json
import sys
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backend.database import get_connection
from scheduler.log_manager import get_logger, db_log

logger = get_logger("scheduler.jobs")

import yaml
CONFIG_PATH = BASE_DIR / "config.yaml"
with open(CONFIG_PATH, "r") as f:
    CONFIG = yaml.safe_load(f)

TELEGRAM_TOKEN = CONFIG.get("telegram_token", "")  # fallback legacy, non usato
# CHAT_IDS caricati dinamicamente dal DB via _get_telegram_config()

# Lock per evitare esecuzioni parallele del job principale
_send_lock = threading.Lock()


def _get_telegram_config():
    """Ricarica la config Telegram dal DB a ogni chiamata (hot-reload dalla UI)."""
    from backend.database import get_telegram_config
    return get_telegram_config()


def _utc_now_str() -> str:
    """Restituisce il timestamp UTC corrente in formato ISO senza offset (per SQLite)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _send_telegram_sync(chat_id: int, text: str, execution_id: int) -> bool:
    """Invia messaggio Telegram con pulsante di conferma (sincrono)."""
    try:
        cfg = _get_telegram_config()
        token = cfg["telegram_token"]
        if not token:
            logger.warning("Token Telegram non configurato")
            return False
        import requests as req_lib
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": f"🔔 {text}",
            "parse_mode": "HTML",
            "reply_markup": json.dumps({
                "inline_keyboard": [[
                    {"text": "✔ Confermato", "callback_data": f"confirm:{execution_id}"}
                ]]
            }),
        }
        r = req_lib.post(url, json=payload, timeout=5)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Errore invio Telegram a {chat_id}: {e}")
        return False


def _calc_next_execution(reminder: dict, from_dt: datetime):
    """Calcola la prossima esecuzione mantenendo l'orario originale del giorno."""
    rec = reminder.get("recurrence_json")
    if not rec:
        return None
    try:
        recurrence = json.loads(rec)
        rec_type = recurrence.get("type")
        interval = int(recurrence.get("interval", 1))

        original_str = reminder.get("next_execution")
        if isinstance(original_str, str):
            original = datetime.fromisoformat(original_str)
        else:
            original = original_str
        if original and original.tzinfo is None:
            original = original.replace(tzinfo=timezone.utc)

        if rec_type == "minutely":
            return from_dt + timedelta(minutes=interval)
        elif rec_type == "hourly":
            return from_dt + timedelta(hours=interval)
        elif rec_type == "daily":
            return original + timedelta(days=interval)
        elif rec_type == "weekly":
            return original + timedelta(weeks=interval)
        elif rec_type == "monthly":
            from dateutil.relativedelta import relativedelta
            return original + relativedelta(months=interval)
        elif rec_type == "yearly":
            from dateutil.relativedelta import relativedelta
            return original + relativedelta(years=interval)
    except Exception as e:
        logger.error(f"Errore calcolo ricorrenza: {e}")
    return None


def recover_stuck_reminders():
    """
    Chiamata all'avvio: gestisce tutti i casi di reminder persi durante il downtime.

    Caso 1 — MISSED: reminder 'pending' con next_execution nel passato
             → inviati subito con prefisso ⏰ PERSO, poi riprogrammati se ricorrenti

    Caso 2 — STUCK SENT: reminder 'sent' ricorrenti con next_execution nel passato
             → riprogrammati alla prossima occorrenza futura senza reinvio
    """
    try:
        conn = get_connection()
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S")

        # ── CASO 1: missed (pending con data passata) ──────────────────────────
        missed_rows = conn.execute(
            """SELECT r.*, u.timezone FROM reminders r
               JOIN users u ON r.user_id = u.id
               WHERE r.status = 'pending'
               AND r.deleted_at IS NULL
               AND substr(r.next_execution,1,19) <= ?
               ORDER BY r.next_execution ASC""",
            (now_str,),
        ).fetchall()

        for row in missed_rows:
            reminder = dict(row)
            try:
                due = datetime.fromisoformat(reminder["next_execution"])
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                delay_min = int((now - due).total_seconds() / 60)
                delay_str = f"{delay_min} min fa" if delay_min < 120 else f"{delay_min // 60}h fa"
            except Exception:
                delay_str = "tempo fa"

            cur = conn.execute(
                "INSERT INTO executions (reminder_id, sent_at) VALUES (?, ?)",
                (reminder["id"], _utc_now_str()),
            )
            execution_id = cur.lastrowid
            conn.commit()

            text = f"⏰ PERSO ({delay_str}): {reminder['message']}"
            success = False
            for chat_id in _get_telegram_config()["chat_ids"]:
                ok = _send_telegram_sync(chat_id, text, execution_id)
                success = success or ok

            if success:
                logger.info(f"Reminder missed {reminder['id']} inviato in recovery (ritardo: {delay_str})")
                db_log("INFO", f"Reminder {reminder['id']} inviato in recovery dopo riavvio")

                next_exec = _calc_next_execution(reminder, now)
                while next_exec and next_exec <= now:
                    reminder["next_execution"] = next_exec.isoformat()
                    next_exec = _calc_next_execution(reminder, now)

                if next_exec and next_exec > now:
                    # Ricorrente: sent con prossima data già impostata
                    conn.execute(
                        """UPDATE reminders SET status = 'sent', next_execution = ?,
                           last_sent_at = ?, recurrence_json = ? WHERE id = ?""",
                        (next_exec.strftime("%Y-%m-%dT%H:%M:%S"), _utc_now_str(),
                         reminder["recurrence_json"], reminder["id"]),
                    )
                else:
                    # Non ricorrente: sent, aspetta conferma
                    conn.execute(
                        "UPDATE reminders SET status = 'sent', last_sent_at = ? WHERE id = ?",
                        (_utc_now_str(), reminder["id"]),
                    )
                conn.commit()
            else:
                conn.execute("DELETE FROM executions WHERE id = ?", (execution_id,))
                conn.commit()
                logger.warning(f"Recovery invio fallito per reminder {reminder['id']}, verrà riprovato")

        # ── CASO 2: stuck sent ricorrenti ──────────────────────────────────────
        stuck_rows = conn.execute(
            """SELECT * FROM reminders
               WHERE status = 'sent'
               AND recurrence_json IS NOT NULL
               AND recurrence_json != 'null'
               AND substr(next_execution,1,19) <= ?
               AND deleted_at IS NULL""",
            (now_str,),
        ).fetchall()

        for row in stuck_rows:
            reminder = dict(row)
            next_exec = _calc_next_execution(reminder, now)
            while next_exec and next_exec <= now:
                reminder["next_execution"] = next_exec.isoformat()
                next_exec = _calc_next_execution(reminder, now)

            if next_exec and next_exec > now:
                conn.execute(
                    "UPDATE reminders SET status = 'pending', next_execution = ?, last_sent_at = NULL WHERE id = ?",
                    (next_exec.strftime("%Y-%m-%dT%H:%M:%S"), reminder["id"]),
                )
            else:
                conn.execute(
                    "UPDATE reminders SET status = 'pending' WHERE id = ?",
                    (reminder["id"],),
                )
        conn.commit()
        conn.close()

    except Exception as e:
        logger.error(f"Errore recover_stuck_reminders: {e}")
        db_log("ERROR", f"Errore recovery: {e}")


def _resend_on_startup():
    """
    Chiamata all'avvio: invia subito il sollecito per TUTTE le executions
    non confermate, indipendentemente da quando sono state inviate.
    Questo copre il caso in cui il sistema era spento e non ha potuto
    inviare i solleciti orari.
    """
    try:
        conn = get_connection()
        now = _utc_now_str()

        rows = conn.execute(
            """SELECT e.reminder_id, r.message
               FROM executions e
               JOIN reminders r ON e.reminder_id = r.id
               WHERE e.confirmed = 0
               AND r.deleted_at IS NULL
               AND r.status NOT IN ('paused', 'resolved', 'deleted')
               GROUP BY e.reminder_id""",
        ).fetchall()

        for row in rows:
            reminder_id = row["reminder_id"]
            message = row["message"]

            cur = conn.execute(
                "INSERT INTO executions (reminder_id, sent_at) VALUES (?, ?)",
                (reminder_id, now),
            )
            execution_id = cur.lastrowid
            conn.commit()

            text = f"⚠️ SOLLECITO: {message}"
            success = False
            for chat_id in _get_telegram_config()["chat_ids"]:
                ok = _send_telegram_sync(chat_id, text, execution_id)
                success = success or ok

            if success:
                logger.info(f"Sollecito riavvio inviato per reminder {reminder_id}")
                db_log("INFO", f"Sollecito riavvio reminder {reminder_id}")
            else:
                conn.execute("DELETE FROM executions WHERE id = ?", (execution_id,))
                conn.commit()

        conn.close()
    except Exception as e:
        logger.error(f"Errore _resend_on_startup: {e}")
        db_log("ERROR", str(e))


def check_and_send_reminders():
    """Job principale: controlla reminder imminenti e li invia."""
    if not _send_lock.acquire(blocking=False):
        logger.debug("check_and_send_reminders già in esecuzione, skip")
        return
    try:
        conn = get_connection()
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S")

        # Prende sia i pending normali sia i ricorrenti 'sent' la cui prossima
        # occorrenza è già scaduta (utente non ha confermato quella precedente)
        rows = conn.execute(
            """SELECT r.*, u.timezone FROM reminders r
               JOIN users u ON r.user_id = u.id
               WHERE r.deleted_at IS NULL
               AND substr(r.next_execution,1,19) <= ?
               AND (
                   r.status = 'pending'
                   OR (
                       r.status = 'sent'
                       AND r.recurrence_json IS NOT NULL
                       AND r.recurrence_json != 'null'
                       AND r.recurrence_json != ''
                   )
               )
               ORDER BY r.next_execution ASC""",
            (now_str,),
        ).fetchall()

        for row in rows:
            reminder = dict(row)

            # Se era 'sent' ricorrente con occorrenza scaduta: marca le vecchie
            # executions non confermate come superate e procedi con il nuovo invio
            if reminder["status"] == "sent":
                conn.execute(
                    """UPDATE executions SET confirmed = 1, confirmed_at = ?
                       WHERE reminder_id = ? AND confirmed = 0""",
                    (_utc_now_str(), reminder["id"]),
                )
                conn.commit()
                logger.info(f"Reminder {reminder['id']} ricorrente: occorrenza precedente superata, invio nuova")

            # Anti-duplicazione: se già inviato nell'ultimo minuto, skip
            if reminder["last_sent_at"]:
                try:
                    last = datetime.fromisoformat(reminder["last_sent_at"])
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if (now - last).total_seconds() < 60:
                        continue
                except Exception:
                    pass

            cur = conn.execute(
                "INSERT INTO executions (reminder_id, sent_at) VALUES (?, ?)",
                (reminder["id"], _utc_now_str()),
            )
            execution_id = cur.lastrowid
            conn.commit()

            success = False
            for chat_id in _get_telegram_config()["chat_ids"]:
                ok = _send_telegram_sync(chat_id, reminder["message"], execution_id)
                success = success or ok

            if success:
                logger.info(f"Reminder {reminder['id']} inviato (execution {execution_id})")
                db_log("INFO", f"Reminder {reminder['id']} inviato")

                next_exec = _calc_next_execution(reminder, now)

                if next_exec:
                    # Ricorrente: va a 'sent' (in attesa conferma)
                    # next_execution è già la prossima data, così quando
                    # l'utente conferma, confirm.py lo rimette a 'pending'
                    conn.execute(
                        """UPDATE reminders
                           SET status = 'sent',
                               next_execution = ?,
                               last_sent_at = ?,
                               recurrence_json = ?
                           WHERE id = ?""",
                        (next_exec.strftime("%Y-%m-%dT%H:%M:%S"), _utc_now_str(),
                         reminder["recurrence_json"], reminder["id"]),
                    )
                    logger.info(f"Reminder {reminder['id']} ricorrente → sent, prossima: {next_exec}")
                else:
                    # Non ricorrente: aspetta conferma
                    conn.execute(
                        "UPDATE reminders SET last_sent_at = ?, status = 'sent' WHERE id = ?",
                        (_utc_now_str(), reminder["id"]),
                    )

                conn.commit()
            else:
                conn.execute("DELETE FROM executions WHERE id = ?", (execution_id,))
                conn.commit()
                logger.warning(f"Reminder {reminder['id']}: invio fallito")

        conn.close()
    except Exception as e:
        logger.error(f"Errore check_and_send_reminders: {e}")
        db_log("ERROR", str(e))
    finally:
        _send_lock.release()


def resend_unconfirmed_reminders():
    """
    Job orario: reinvia sollecito per QUALSIASI execution non confermata
    (sia reminder singoli che ricorrenti) più vecchia di 1 ora.
    Continua ogni ora finché l'utente non preme ✔.
    """
    try:
        conn = get_connection()
        now = datetime.now(timezone.utc)
        one_hour_ago = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")

        rows = conn.execute(
            """SELECT e.reminder_id, r.message
               FROM executions e
               JOIN reminders r ON e.reminder_id = r.id
               WHERE e.confirmed = 0
               AND r.deleted_at IS NULL
               AND r.status NOT IN ('paused', 'resolved', 'deleted')
               GROUP BY e.reminder_id
               HAVING MAX(substr(e.sent_at,1,19)) <= ?""",
            (one_hour_ago,),
        ).fetchall()

        for row in rows:
            reminder_id = row["reminder_id"]
            message = row["message"]

            cur = conn.execute(
                "INSERT INTO executions (reminder_id, sent_at) VALUES (?, ?)",
                (reminder_id, _utc_now_str()),
            )
            execution_id = cur.lastrowid
            conn.commit()

            text = f"⚠️ SOLLECITO: {message}"
            success = False
            for chat_id in _get_telegram_config()["chat_ids"]:
                ok = _send_telegram_sync(chat_id, text, execution_id)
                success = success or ok

            if success:
                logger.info(f"Sollecito inviato per reminder {reminder_id} (execution {execution_id})")
                db_log("INFO", f"Sollecito reminder {reminder_id}")
            else:
                conn.execute("DELETE FROM executions WHERE id = ?", (execution_id,))
                conn.commit()

        conn.close()
    except Exception as e:
        logger.error(f"Errore resend_unconfirmed: {e}")
        db_log("ERROR", str(e))
