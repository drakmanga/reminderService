import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import yaml
from scheduler.log_manager import get_logger, db_log

logger = get_logger("scheduler.backup")

CONFIG_PATH = BASE_DIR / "config.yaml"
with open(CONFIG_PATH, "r") as f:
    CONFIG = yaml.safe_load(f)

DB_PATH = BASE_DIR / CONFIG.get("db_path", "data/reminder.db")
BACKUP_DIR = BASE_DIR / CONFIG.get("backup_path", "data/backups")
BACKUP_KEEP = CONFIG.get("backup_keep", 7)


def run_backup():
    """Esegue il backup del database e mantiene solo gli ultimi BACKUP_KEEP backup."""
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        if not DB_PATH.exists():
            logger.warning("Database non trovato, skip backup")
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest = BACKUP_DIR / f"reminder_{timestamp}.db"
        shutil.copy2(str(DB_PATH), str(dest))
        logger.info(f"Backup creato: {dest}")
        db_log("INFO", f"Backup creato: {dest.name}")

        # Mantieni solo gli ultimi BACKUP_KEEP backup
        backups = sorted(BACKUP_DIR.glob("reminder_*.db"), key=lambda p: p.stat().st_mtime)
        while len(backups) > BACKUP_KEEP:
            oldest = backups.pop(0)
            oldest.unlink()
            logger.info(f"Backup rimosso: {oldest}")

    except Exception as e:
        logger.error(f"Errore durante backup: {e}")
        db_log("ERROR", f"Errore backup: {e}")

