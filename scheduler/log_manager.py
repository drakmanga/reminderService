import logging
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import yaml

CONFIG_PATH = BASE_DIR / "config.yaml"
with open(CONFIG_PATH, "r") as f:
    CONFIG = yaml.safe_load(f)

LOG_PATH = BASE_DIR / CONFIG.get("log_path", "logs/app.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

LOG_MAX_BYTES = CONFIG.get("log_max_size_mb", 10) * 1024 * 1024
LOG_CLEANUP_BYTES = CONFIG.get("log_cleanup_mb", 5) * 1024 * 1024


def rotate_log_if_needed():
    """Rotazione FIFO: se il log supera LOG_MAX_BYTES, taglia le righe più vecchie."""
    if not LOG_PATH.exists():
        return
    size = LOG_PATH.stat().st_size
    if size < LOG_MAX_BYTES:
        return

    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    # Calcola quante righe rimuovere per liberare LOG_CLEANUP_BYTES
    to_remove_bytes = 0
    cut_index = 0
    for i, line in enumerate(lines):
        to_remove_bytes += len(line.encode("utf-8"))
        if to_remove_bytes >= LOG_CLEANUP_BYTES:
            cut_index = i + 1
            break

    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines[cut_index:])


def get_logger(name: str) -> logging.Logger:
    rotate_log_if_needed()

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = logging.DEBUG if CONFIG.get("app_env", "dev") == "dev" else logging.INFO
    logger.setLevel(level)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # File handler
    fh = logging.FileHandler(str(LOG_PATH), encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


def db_log(log_type: str, message: str):
    """Salva un log anche nel database."""
    try:
        from backend.database import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT INTO logs (type, message) VALUES (?, ?)", (log_type, message)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

