from fastapi import APIRouter, HTTPException, Depends, Request
from backend.database import get_connection
from backend.auth import get_current_user
from datetime import datetime, timezone

router = APIRouter(prefix="/confirm", tags=["confirm"])


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _apply_confirmation(conn, reminder_id: int, execution_id: int):
    """
    Logica condivisa tra conferma web e bot.

    - Reminder RICORRENTE → status = 'completed' per l'occorrenza corrente,
      ma next_execution è già impostata (lo scheduler lo ha già riprogrammato
      a 'pending'). Se per qualche motivo è ancora 'sent', ricalcola e rimette pending.
    - Reminder NON ricorrente → status = 'resolved' (chiuso definitivamente).
    """
    now_str = _utc_now_str()

    # Segna TUTTE le executions non confermate di questo reminder come confermate
    # (non solo quella specifica) per evitare solleciti fantasma da duplicati
    conn.execute(
        "UPDATE executions SET confirmed = 1, confirmed_at = ? WHERE reminder_id = ? AND confirmed = 0",
        (now_str, reminder_id),
    )

    reminder = conn.execute(
        "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
    ).fetchone()
    if not reminder:
        conn.commit()
        return

    has_recurrence = (
        reminder["recurrence_json"]
        and reminder["recurrence_json"] not in (None, "null", "")
    )

    if has_recurrence:
        if reminder["status"] == "pending":
            # Già rimesso a pending dallo scheduler (non dovrebbe succedere, ma safe)
            pass
        else:
            # Era 'sent': la next_execution è già la data di domani/prossima settimana ecc.
            # impostata dallo scheduler al momento dell'invio → rimetti semplicemente pending
            conn.execute(
                "UPDATE reminders SET status = 'pending', last_sent_at = NULL WHERE id = ?",
                (reminder_id,),
            )
    else:
        # Nessuna ricorrenza → risolto definitivamente
        conn.execute(
            "UPDATE reminders SET status = 'resolved' WHERE id = ?",
            (reminder_id,),
        )

    conn.commit()


@router.post("/{execution_id}")
async def confirm_execution(
    execution_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    conn = get_connection()
    execution = conn.execute(
        """SELECT e.*, r.user_id FROM executions e
           JOIN reminders r ON e.reminder_id = r.id
           WHERE e.id = ?""",
        (execution_id,),
    ).fetchone()

    if not execution:
        conn.close()
        raise HTTPException(status_code=404, detail="Execution non trovata")

    if execution["user_id"] != current_user["id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="Non autorizzato")

    _apply_confirmation(conn, execution["reminder_id"], execution_id)
    conn.close()
    return {"message": "Reminder confermato"}


@router.post("/bot/{execution_id}")
async def confirm_execution_bot(execution_id: int):
    """Endpoint chiamato dal bot Telegram."""
    conn = get_connection()
    execution = conn.execute(
        "SELECT * FROM executions WHERE id = ?", (execution_id,)
    ).fetchone()

    if not execution:
        conn.close()
        raise HTTPException(status_code=404, detail="Execution non trovata")

    _apply_confirmation(conn, execution["reminder_id"], execution_id)
    conn.close()
    return {"message": "Confermato via bot"}


@router.post("/resolve/{reminder_id}")
async def resolve_reminder(
    reminder_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Forza la chiusura definitiva di un reminder ricorrente (resolved)."""
    conn = get_connection()
    reminder = conn.execute(
        "SELECT * FROM reminders WHERE id = ? AND user_id = ?",
        (reminder_id, current_user["id"]),
    ).fetchone()

    if not reminder:
        conn.close()
        raise HTTPException(status_code=404, detail="Reminder non trovato")

    conn.execute(
        "UPDATE reminders SET status = 'resolved', deleted_at = NULL WHERE id = ?",
        (reminder_id,),
    )
    conn.commit()
    conn.close()
    return {"message": "Reminder risolto definitivamente"}
