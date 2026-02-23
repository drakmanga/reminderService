import bcrypt
from fastapi import APIRouter, Response, Request, HTTPException, Form
from fastapi.responses import HTMLResponse
from backend.database import get_connection
from backend.models import LoginRequest

router = APIRouter()

SESSION_COOKIE = "reminder_session"
SESSION_MAX_AGE = 60 * 60 * 24  # 24 ore


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def get_current_user(request: Request) -> dict:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Non autenticato")
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=401, detail="Utente non trovato")
    return dict(user)


def create_default_users():
    """Crea gli utenti di default se non esistono."""
    conn = get_connection()
    users = [
        ("admin", "admin123", "Europe/Rome"),
        ("ragazza", "ragazza123", "Europe/Rome"),
    ]
    for username, password, tz in users:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not existing:
            pw_hash = hash_password(password)
            conn.execute(
                "INSERT INTO users (username, password_hash, timezone) VALUES (?, ?, ?)",
                (username, pw_hash, tz),
            )
    conn.commit()
    conn.close()


@router.post("/login")
async def login(request: Request):
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        body = await request.json()
        username = body.get("username", "")
        password = body.get("password", "")
    else:
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username e password obbligatori")

    conn = get_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()

    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenziali non valide")

    request.session["user_id"] = user["id"]
    request.session["username"] = user["username"]
    return {"message": "Login effettuato", "username": user["username"]}


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"message": "Logout effettuato"}

