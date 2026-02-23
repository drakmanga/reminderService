import os
import sys
import threading
import uvicorn
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

# Aggiunge la root del progetto al path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backend.database import init_db
from backend.auth import router as auth_router, create_default_users
from backend.routers.reminders import router as reminders_router
from backend.routers.confirm import router as confirm_router
from backend.routers.settings import router as settings_router

# Carica config
import yaml
CONFIG_PATH = BASE_DIR / "config.yaml"
with open(CONFIG_PATH, "r") as f:
    CONFIG = yaml.safe_load(f)

app = FastAPI(title="Reminder System", version="1.0.0")

# Session middleware
SECRET_KEY = os.getenv("SECRET_KEY", "cambia-questa-chiave-segreta-in-produzione")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400)

# Static files e templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "frontend" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "frontend"))

# Router
app.include_router(auth_router)
app.include_router(reminders_router)
app.include_router(confirm_router)
app.include_router(settings_router)


@app.on_event("startup")
async def startup():
    init_db()
    create_default_users()

    # Avvia scheduler in thread separato
    from scheduler.scheduler import start_scheduler
    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()

    # Avvia bot Telegram in thread separato
    from bot.bot import start_bot
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = request.session.get("username")
    return templates.TemplateResponse(
        "index.html", {"request": request, "user": user}
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    env = CONFIG.get("app_env", "dev")
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=(env == "dev"),
        log_level="warning",   # evita il flood di GET /reminders ogni 30s
        access_log=False,      # disabilita access log (usa il logger applicativo)
    )

