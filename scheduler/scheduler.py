import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from scheduler.jobs import check_and_send_reminders, resend_unconfirmed_reminders, recover_stuck_reminders, _resend_on_startup
from scheduler.backup import run_backup
from scheduler.log_manager import get_logger

logger = get_logger("scheduler.main")

CONFIG_PATH = BASE_DIR / "config.yaml"
with open(CONFIG_PATH, "r") as f:
    CONFIG = yaml.safe_load(f)

_scheduler: BackgroundScheduler = None


def start_scheduler():
    global _scheduler
    interval_sec = CONFIG.get("scheduler_interval_sec", 5)

    _scheduler = BackgroundScheduler(timezone="UTC")

    # Recupera reminder bloccati dal riavvio precedente
    recover_stuck_reminders()
    logger.info("Recovery reminder completato")

    # Invia subito solleciti per executions non confermate (sistema era spento)
    _resend_on_startup()
    logger.info("Solleciti riavvio inviati")

    # Job principale: ogni N secondi (minimo 10 per non sovraccaricare)
    interval_sec = max(interval_sec, 10)
    _scheduler.add_job(
        check_and_send_reminders,
        trigger=IntervalTrigger(seconds=interval_sec),
        id="check_reminders",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Job solleciti: ogni ora
    _scheduler.add_job(
        resend_unconfirmed_reminders,
        trigger=IntervalTrigger(hours=1),
        id="resend_unconfirmed",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Backup giornaliero
    _scheduler.add_job(
        run_backup,
        trigger=IntervalTrigger(hours=24),
        id="daily_backup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    _scheduler.start()
    logger.info(f"Scheduler avviato (intervallo: {interval_sec}s)")

    # Blocca il thread (daemon=True garantisce la chiusura con il processo)
    import time
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        _scheduler.shutdown()
        logger.info("Scheduler fermato")


def get_scheduler() -> BackgroundScheduler:
    return _scheduler

