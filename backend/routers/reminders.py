from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from backend.database import get_connection
from backend.auth import get_current_user
from datetime import datetime, timezone
from pathlib import Path
import json
import pytz

router = APIRouter(prefix="/reminders", tags=["reminders"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "frontend"))

import json as _json
import pytz as _pytz

templates.env.filters["from_json"] = _json.loads

def _to_local_filter(dt, tz_str="Europe/Rome"):
    """Filtro Jinja2: converte datetime UTC in ora locale."""
    if dt is None:
        return "—"
    try:
        tz = _pytz.timezone(tz_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(tz)
        return local_dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return dt.strftime("%d/%m/%Y %H:%M") if dt else "—"

def _to_local_input_filter(dt, tz_str="Europe/Rome"):
    """Filtro Jinja2: converte datetime UTC in formato datetime-local (YYYY-MM-DDTHH:MM)."""
    if dt is None:
        return ""
    try:
        tz = _pytz.timezone(tz_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(tz)
        return local_dt.strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return dt.strftime("%Y-%m-%dT%H:%M") if dt else ""

def _to_local_short_filter(dt, tz_str="Europe/Rome"):
    """Filtro Jinja2: data compatta — 'oggi HH:MM', 'domani HH:MM', '25 mar', '25 mar 27'."""
    if dt is None:
        return ""
    try:
        from datetime import date as _date, timedelta as _td
        tz = _pytz.timezone(tz_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(tz)
        today = datetime.now(tz).date()
        tomorrow = today + _td(days=1)
        d = local_dt.date()
        months = ["gen","feb","mar","apr","mag","giu","lug","ago","set","ott","nov","dic"]
        if d == today:
            return f"oggi {local_dt.strftime('%H:%M')}"
        elif d == tomorrow:
            return f"domani {local_dt.strftime('%H:%M')}"
        elif d.year == today.year:
            return f"{d.day} {months[d.month-1]}"
        else:
            return f"{d.day} {months[d.month-1]} {str(d.year)[2:]}"
    except Exception:
        return ""

templates.env.filters["to_local_short"] = _to_local_short_filter
templates.env.filters["to_local"] = _to_local_filter
templates.env.filters["to_local_input"] = _to_local_input_filter


def _localize_to_utc(dt_str: str, user_tz: str) -> datetime:
    """
    Converte una stringa datetime-local (es. "2026-02-23T09:50")
    dalla timezone dell'utente a UTC.
    """
    try:
        tz = pytz.timezone(user_tz)
    except Exception:
        tz = pytz.timezone("Europe/Rome")

    # fromisoformat può già avere tz info se il client manda offset
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        # Naive: assume sia nell'ora locale dell'utente
        dt = tz.localize(dt)
    # Converti in UTC
    return dt.astimezone(timezone.utc)


def _row_to_dict(row) -> dict:
    d = dict(row)
    for ts_field in ("next_execution", "created_at", "last_sent_at", "deleted_at"):
        if d.get(ts_field) and isinstance(d[ts_field], str):
            try:
                d[ts_field] = datetime.fromisoformat(d[ts_field])
            except ValueError:
                d[ts_field] = None
    return d


def _get_reminders_html(
    request: Request,
    user_id: int,
    user_tz: str = "Europe/Rome",
    sort: str = "status",
    show_deleted: bool = False,
) -> HTMLResponse:
    """Restituisce la lista reminder come HTML fragment per HTMX."""
    conn = get_connection()

    if show_deleted:
        where = "WHERE user_id = ?"
        params = (user_id,)
    else:
        where = "WHERE user_id = ? AND status != 'deleted'"
        params = (user_id,)

    if sort == "date":
        order = "ORDER BY next_execution ASC"
    elif sort == "date_desc":
        order = "ORDER BY next_execution DESC"
    elif sort == "id":
        order = "ORDER BY id ASC"
    elif sort == "id_desc":
        order = "ORDER BY id DESC"
    else:  # default: per stato
        order = """ORDER BY
               CASE status
                   WHEN 'pending' THEN 1
                   WHEN 'sent' THEN 2
                   WHEN 'completed' THEN 3
                   WHEN 'paused' THEN 4
                   WHEN 'resolved' THEN 5
                   WHEN 'deleted' THEN 6
                   ELSE 7
               END, next_execution ASC"""

    rows = conn.execute(
        f"SELECT * FROM reminders {where} {order}", params
    ).fetchall()
    conn.close()
    reminders = [_row_to_dict(r) for r in rows]
    return templates.TemplateResponse(
        "partials/reminders_list.html",
        {"request": request, "reminders": reminders, "user_tz": user_tz,
         "sort": sort, "show_deleted": show_deleted},
    )


@router.get("", response_class=HTMLResponse)
async def list_reminders(
    request: Request,
    sort: str = "status",
    show_deleted: bool = False,
    current_user: dict = Depends(get_current_user)
):
    return _get_reminders_html(
        request, current_user["id"],
        current_user.get("timezone", "Europe/Rome"),
        sort, show_deleted
    )


def _filter_params(request: Request) -> tuple:
    """Estrae i parametri di filtro dagli header HTMX o dai query params."""
    sort = request.query_params.get("sort", "status")
    show_deleted = request.query_params.get("show_deleted", "false").lower() == "true"
    # HTMX manda i parametri nel header HX-Current-URL
    current_url = request.headers.get("hx-current-url", "")
    if "sort=" in current_url:
        import re
        m = re.search(r"sort=([^&]+)", current_url)
        if m:
            sort = m.group(1)
    if "show_deleted=true" in current_url:
        show_deleted = True
    return sort, show_deleted


@router.post("", response_class=HTMLResponse, status_code=201)
async def create_reminder(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    form = await request.form()
    message = str(form.get("message", "")).strip()
    next_execution_str = str(form.get("next_execution", ""))
    recurrence_json = form.get("recurrence_json") or None
    recurrence_type = form.get("recurrence_type", "")
    recurrence_interval = form.get("recurrence_interval", 1)

    if not message or not next_execution_str:
        raise HTTPException(status_code=400, detail="Campi obbligatori mancanti")

    import html as _html
    message = _html.escape(message[:500])

    # Costruisci recurrence_json se non fornito direttamente
    if not recurrence_json and recurrence_type:
        recurrence_json = json.dumps({
            "type": recurrence_type,
            "interval": int(recurrence_interval or 1)
        })

    try:
        next_exec = _localize_to_utc(next_execution_str, current_user.get("timezone", "Europe/Rome"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Data non valida")

    conn = get_connection()
    conn.execute(
        """INSERT INTO reminders (user_id, message, next_execution, recurrence_json, status)
           VALUES (?, ?, ?, ?, 'pending')""",
        (current_user["id"], message, next_exec.isoformat(), recurrence_json),
    )
    conn.commit()
    conn.close()
    sort, show_deleted = _filter_params(request)
    return _get_reminders_html(request, current_user["id"], current_user.get("timezone", "Europe/Rome"), sort, show_deleted)


@router.put("/{reminder_id}", response_class=HTMLResponse)
async def update_reminder(
    reminder_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM reminders WHERE id = ? AND user_id = ?",
        (reminder_id, current_user["id"]),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Reminder non trovato")

    # Supporta sia JSON body che form data
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        message = body.get("message")
        next_exec_str = body.get("next_execution")
        recurrence_json = body.get("recurrence_json")
        status = body.get("status")
    else:
        form = await request.form()
        message = form.get("message")
        next_exec_str = form.get("next_execution")
        recurrence_json = form.get("recurrence_json")
        status = form.get("status")

    fields = []
    values = []
    if message is not None:
        import html as _html
        fields.append("message = ?")
        values.append(_html.escape(str(message)[:500]))
    if next_exec_str:
        try:
            fields.append("next_execution = ?")
            values.append(
                _localize_to_utc(str(next_exec_str), current_user.get("timezone", "Europe/Rome")).isoformat()
            )
        except ValueError:
            pass
    if recurrence_json is not None:
        fields.append("recurrence_json = ?")
        # Stringa vuota = rimuovi ricorrenza (NULL nel DB)
        if str(recurrence_json).strip() == '':
            values.append(None)
        else:
            values.append(str(recurrence_json))
    if status is not None:
        allowed = {"pending", "sent", "completed", "paused", "deleted", "resolved"}
        if status in allowed:
            fields.append("status = ?")
            values.append(status)

    if fields:
        values.append(reminder_id)
        conn.execute(
            f"UPDATE reminders SET {', '.join(fields)} WHERE id = ?", values
        )
        conn.commit()
    conn.close()
    sort, show_deleted = _filter_params(request)
    return _get_reminders_html(request, current_user["id"], current_user.get("timezone", "Europe/Rome"), sort, show_deleted)


@router.delete("/{reminder_id}", response_class=HTMLResponse)
async def delete_reminder(
    reminder_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM reminders WHERE id = ? AND user_id = ?",
        (reminder_id, current_user["id"]),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Reminder non trovato")

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE reminders SET status = 'deleted', deleted_at = ? WHERE id = ?",
        (now, reminder_id),
    )
    conn.commit()
    conn.close()
    sort, show_deleted = _filter_params(request)
    return _get_reminders_html(request, current_user["id"], current_user.get("timezone", "Europe/Rome"), sort, show_deleted)



