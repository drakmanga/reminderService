from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from backend.auth import get_current_user, hash_password, verify_password
from backend.database import set_setting, get_telegram_config, get_connection
import json
import requests as req_lib

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(current_user: dict = Depends(get_current_user)):
    cfg = get_telegram_config()
    token = cfg.get("telegram_token", "")
    chat_ids = cfg.get("chat_ids", [])
    masked_token = ("*" * (len(token) - 6) + token[-6:]) if len(token) > 6 else ("***" if token else "")
    return {
        "telegram_token_set": bool(token),
        "telegram_token_masked": masked_token,
        "chat_ids": chat_ids,
    }


@router.post("/token")
async def save_token(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Salva solo il token bot."""
    form = await request.form()
    token = str(form.get("telegram_token", "")).strip()

    if not token:
        return JSONResponse(status_code=400, content={"error": "Token obbligatorio"})

    # Verifica validità token
    try:
        resp = req_lib.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
        if resp.status_code != 200:
            return JSONResponse(status_code=400, content={"error": "Token non valido — verificalo su @BotFather"})
        bot_name = resp.json().get("result", {}).get("username", "")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Impossibile raggiungere Telegram: {e}"})

    set_setting("telegram_token", token)
    return {"message": f"Token salvato — bot: @{bot_name}"}


@router.post("/chat-ids")
async def save_chat_ids(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Sostituisce la lista completa dei chat IDs."""
    form = await request.form()
    chat_ids_raw = str(form.get("chat_ids", "")).strip()

    chat_ids = []
    for part in chat_ids_raw.replace(",", "\n").splitlines():
        part = part.strip()
        if part:
            try:
                chat_ids.append(int(part))
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Chat ID non valido: '{part}'"}
                )

    set_setting("telegram_chat_ids", json.dumps(chat_ids))
    return {"message": "Chat IDs aggiornati", "chat_ids": chat_ids}


@router.post("/test")
async def test_telegram(current_user: dict = Depends(get_current_user)):
    """Invia un messaggio di test a tutti i chat IDs configurati."""
    cfg = get_telegram_config()
    token = cfg.get("telegram_token", "")
    chat_ids = cfg.get("chat_ids", [])

    if not token or not chat_ids:
        return JSONResponse(status_code=400, content={"error": "Token o Chat IDs non configurati"})

    results = []
    for chat_id in chat_ids:
        try:
            resp = req_lib.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "✅ Test connessione Reminder System — funziona!"},
                timeout=5,
            )
            results.append({"chat_id": chat_id, "ok": resp.status_code == 200})
        except Exception as e:
            results.append({"chat_id": chat_id, "ok": False, "error": str(e)})

    all_ok = all(r["ok"] for r in results)
    return {"message": "Test completato", "results": results, "all_ok": all_ok}


@router.post("/account")
async def update_account(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Aggiorna username e/o password dell'utente corrente."""
    form = await request.form()
    new_username = str(form.get("new_username", "")).strip()
    current_password = str(form.get("current_password", "")).strip()
    new_password = str(form.get("new_password", "")).strip()
    confirm_password = str(form.get("confirm_password", "")).strip()

    if not current_password:
        return JSONResponse(status_code=400, content={"error": "Inserisci la password attuale per confermare"})

    # Verifica password attuale
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (current_user["id"],)).fetchone()
    if not user or not verify_password(current_password, user["password_hash"]):
        conn.close()
        return JSONResponse(status_code=400, content={"error": "Password attuale non corretta"})

    fields, values = [], []

    # Cambio username
    if new_username and new_username != user["username"]:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ? AND id != ?",
            (new_username, current_user["id"])
        ).fetchone()
        if existing:
            conn.close()
            return JSONResponse(status_code=400, content={"error": f"Username '{new_username}' già in uso"})
        fields.append("username = ?")
        values.append(new_username)

    # Cambio password
    if new_password:
        if len(new_password) < 6:
            conn.close()
            return JSONResponse(status_code=400, content={"error": "La nuova password deve essere di almeno 6 caratteri"})
        if new_password != confirm_password:
            conn.close()
            return JSONResponse(status_code=400, content={"error": "Le password non coincidono"})
        fields.append("password_hash = ?")
        values.append(hash_password(new_password))

    if not fields:
        conn.close()
        return JSONResponse(status_code=400, content={"error": "Nessuna modifica da applicare"})

    values.append(current_user["id"])
    conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()

    # Aggiorna la sessione se è cambiato lo username
    if new_username and new_username != user["username"]:
        request.session["username"] = new_username

    changed = []
    if "username = ?" in fields: changed.append("username")
    if "password_hash = ?" in fields: changed.append("password")
    return {"message": f"{'e'.join(changed).capitalize()} aggiornato/a con successo", "new_username": new_username or user["username"]}

