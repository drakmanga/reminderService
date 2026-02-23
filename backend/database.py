import sqlite3
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "reminder.db"))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'Europe/Rome',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL CHECK(length(message) <= 500),
            next_execution TIMESTAMP NOT NULL,
            recurrence_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','sent','completed','paused','deleted','resolved')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted_at TIMESTAMP,
            last_sent_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reminder_id INTEGER NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confirmed BOOLEAN DEFAULT 0,
            confirmed_at TIMESTAMP,
            FOREIGN KEY (reminder_id) REFERENCES reminders(id)
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK(type IN ('INFO','WARN','ERROR')),
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    conn.close()

    # Migrazione automatica: assicura che 'resolved' sia nel CHECK constraint
    _migrate_status_constraint()


def _migrate_status_constraint():
    """Ricrea la tabella reminders se il constraint status non include 'resolved'."""
    conn = get_connection()
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='reminders'"
    ).fetchone()
    if row and "resolved" not in row["sql"]:
        conn.executescript("""
            PRAGMA foreign_keys = OFF;
            ALTER TABLE reminders RENAME TO reminders_old;
            CREATE TABLE reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL CHECK(length(message) <= 500),
                next_execution TIMESTAMP NOT NULL,
                recurrence_json TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','sent','completed','paused','deleted','resolved')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deleted_at TIMESTAMP,
                last_sent_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            INSERT INTO reminders SELECT * FROM reminders_old;
            DROP TABLE reminders_old;
            PRAGMA foreign_keys = ON;
        """)
        conn.commit()
    conn.close()


def get_setting(key: str, default=None):
    """Legge un valore dalla tabella settings."""
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    """Scrive/aggiorna un valore nella tabella settings."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO settings (key, value, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value,
           updated_at = CURRENT_TIMESTAMP""",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_telegram_config() -> dict:
    """
    Restituisce la config Telegram attiva.
    Priorità per ogni campo: DB (impostato dalla UI) → config.yaml → valore vuoto.
    Token e chat_ids vengono letti indipendentemente l'uno dall'altro.
    """
    import json, yaml
    from pathlib import Path

    # Leggi config.yaml come fallback
    yaml_token = ""
    yaml_chat_ids = []
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if config_path.exists():
        try:
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            yaml_token = cfg.get("telegram_token", "")
            yaml_chat_ids = cfg.get("chat_ids", [])
        except Exception:
            pass

    # Token: DB ha priorità su config.yaml
    token = get_setting("telegram_token") or yaml_token

    # Chat IDs: DB ha priorità su config.yaml
    chat_ids_raw = get_setting("telegram_chat_ids")
    if chat_ids_raw:
        try:
            chat_ids = json.loads(chat_ids_raw)
        except Exception:
            chat_ids = yaml_chat_ids
    else:
        chat_ids = yaml_chat_ids

    return {"telegram_token": token, "chat_ids": chat_ids}


