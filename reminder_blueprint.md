# Reminder System — Blueprint

## 1. Panoramica
Sistema web self-hosted per gestione promemoria con invio notifiche Telegram, multiutente (admin + ragazza), conferma interattiva via bot e scheduler modulare.

- **Stack:** Python + FastAPI, APScheduler, SQLite, HTML + HTMX + Jinja2
- **Modalità bot:** polling Telegram (`python-telegram-bot`)
- **Deployment:** Docker su container Proxmox
- **Backup:** automatico ogni 24h (ultimi 7 backup conservati)
- **Logs:** rotazione FIFO max 10 MB (cleanup a 5 MB liberi)

---

## 2. Stack Tecnologico

| Componente | Libreria / Versione | Motivazione |
|------------|---------------------|-------------|
| Backend | FastAPI 0.115.6 + Uvicorn 0.34.0 | leggero, rapido, async |
| Scheduler | APScheduler 3.10.4 | gestione job, retry, ricorrenze |
| DB | SQLite (WAL mode, FK ON) | sufficiente per 2 utenti |
| Frontend | HTML + HTMX + Jinja2 3.1.5 | zero build, partial reload |
| Bot | python-telegram-bot 21.9 (polling) | semplice, nessun HTTPS richiesto |
| Auth | bcrypt 4.2.1 + SessionMiddleware (itsdangerous 2.2.0) | hash sicuro + cookie session |
| Config | PyYAML 6.0.2 | file centrale `config.yaml` |
| Timezone | pytz 2.2024.2 + python-dateutil 2.9.0 | conversioni UTC ↔ locale |
| Deployment | Docker + docker-compose | facile backup, restart, upgrade |

---

## 3. Architettura

```
Browser (HTMX) ──→ FastAPI (main.py)
                       ├── SessionMiddleware (cookie 24h)
                       ├── /static  (StaticFiles)
                       ├── /        (Jinja2 dashboard)
                       ├── routers/
                       │     ├── auth.py          (login/logout)
                       │     ├── reminders.py     (CRUD + HTML fragments)
                       │     ├── confirm.py       (conferma execution)
                       │     └── settings.py      (token/chat_ids/password)
                       ├── SQLite (WAL)  ← database.py
                       ├── Scheduler Thread (APScheduler)
                       │     ├── check_and_send_reminders  (ogni N sec)
                       │     ├── resend_unconfirmed_reminders (ogni 1h)
                       │     ├── run_backup                 (ogni 24h)
                       │     └── recover_stuck_reminders    (solo a startup)
                       └── Bot Thread (python-telegram-bot polling)
```

### Thread al startup (`main.py`)
```python
scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
bot_thread       = threading.Thread(target=start_bot,       daemon=True)
```

---

## 4. Database Schema (SQLite)

### users
| Campo | Tipo | Note |
|-------|------|------|
| id | INTEGER PK AUTOINCREMENT | |
| username | TEXT UNIQUE NOT NULL | login |
| password_hash | TEXT NOT NULL | bcrypt |
| timezone | TEXT NOT NULL DEFAULT 'Europe/Rome' | per ogni utente |
| created_at | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | |

**Utenti di default** creati all'avvio: `admin / admin123` e `ragazza / ragazza123`

---

### reminders
| Campo | Tipo | Note |
|-------|------|------|
| id | INTEGER PK AUTOINCREMENT | |
| user_id | INTEGER NOT NULL | FK → users.id |
| message | TEXT NOT NULL CHECK(length ≤ 500) | sanitizzato HTML |
| next_execution | TIMESTAMP NOT NULL | UTC |
| recurrence_json | TEXT | JSON tipo `{"type":"daily","interval":1}` |
| status | TEXT NOT NULL DEFAULT 'pending' | vedi stati sotto |
| created_at | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | |
| deleted_at | TIMESTAMP | soft delete |
| last_sent_at | TIMESTAMP | anti-duplicazione invii |

**Stati validi:** `pending` · `sent` · `completed` · `paused` · `deleted` · `resolved`

> **resolved** = reminder non ricorrente confermato dall'utente (chiuso definitivamente).  
> **completed** = stato futuro/manuale.  
> La migrazione automatica `_migrate_status_constraint()` aggiunge `resolved` ai DB esistenti.

---

### executions
| Campo | Tipo | Note |
|-------|------|------|
| id | INTEGER PK AUTOINCREMENT | |
| reminder_id | INTEGER NOT NULL | FK → reminders.id |
| sent_at | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | |
| confirmed | BOOLEAN DEFAULT 0 | |
| confirmed_at | TIMESTAMP | quando confermato |

---

### logs
| Campo | Tipo | Note |
|-------|------|------|
| id | INTEGER PK AUTOINCREMENT | |
| type | TEXT NOT NULL CHECK IN ('INFO','WARN','ERROR') | |
| message | TEXT NOT NULL | |
| created_at | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | |

---

### settings
| Campo | Tipo | Note |
|-------|------|------|
| key | TEXT PK | chiave univoca |
| value | TEXT NOT NULL | valore serializzato |
| updated_at | TIMESTAMP | aggiornato automaticamente |

**Chiavi usate:** `telegram_token`, `telegram_chat_ids` (JSON array)

> La config Telegram viene letta **sempre dal DB** (`get_telegram_config()`), non dal `config.yaml` — questo consente hot-reload dalla UI senza riavvio.

---

## 5. Configurazione Centrale (`config.yaml`)

```yaml
# config.yaml — NON committare con dati reali
telegram_token: "BOT_TOKEN_QUI"
chat_ids:
  - 140722661        # il tuo chat_id
  - 87654321         # chat_id della tua ragazza

polling_interval_sec: 2
scheduler_interval_sec: 5
log_max_size_mb: 10
log_cleanup_mb: 5
timezone_default: "Europe/Rome"

app_env: "dev"        # dev | prod

db_path: "data/reminder.db"
backup_path: "data/backups"
log_path: "logs/app.log"
backup_keep: 7
```

> `scheduler_interval_sec` viene forzato a **minimo 10** in `start_scheduler()`.

---

## 6. Scheduler (`scheduler/`)

### Job attivi

| Job ID | Trigger | Funzione |
|--------|---------|----------|
| `check_reminders` | ogni `scheduler_interval_sec` (min 10s) | `check_and_send_reminders()` |
| `resend_unconfirmed` | ogni 1 ora | `resend_unconfirmed_reminders()` |
| `daily_backup` | ogni 24 ore | `run_backup()` |

### Startup
1. `recover_stuck_reminders()` — gestisce downtime:
   - **MISSED**: reminder `pending` con `next_execution` nel passato → invia con prefisso `⏰ PERSO (X min fa)`
   - **STUCK SENT**: reminder `sent` ricorrenti nel passato → riprogramma alla prossima occorrenza senza reinviare
2. `_resend_on_startup()` — invia solleciti per executions non confermate pendenti

### Ricorrenze supportate (`recurrence_json`)
```json
{ "type": "minutely|hourly|daily|weekly|monthly|yearly", "interval": 1 }
```
Calcolo via `_calc_next_execution()` — mantiene l'orario originale per `daily`/`weekly`/`monthly`/`yearly`.

### Logica conferma (`confirm.py` — `_apply_confirmation()`)
- **Reminder ricorrente** → `status = 'pending'`, `last_sent_at = NULL` (pronto per la prossima occorrenza)
- **Reminder non ricorrente** → `status = 'resolved'` (chiuso)
- Tutte le executions non confermate dello stesso reminder vengono marcate confermate (anti-duplicati)

---

## 7. Telegram Bot (`bot/`)

- Polling con `python-telegram-bot` (asincrono, blocca il thread)
- Chat ID autorizzati ricaricati dal DB a ogni callback (**hot-reload**)
- Comandi: `/start` (benvenuto)
- Callback: `confirm:<execution_id>` → chiama `_apply_confirmation()`
- Token non configurato → bot non avviato (warning nel log)

---

## 8. API Endpoints

### Auth
| Metodo | Endpoint | Funzione |
|--------|----------|----------|
| POST | `/login` | login (form o JSON) — salva `user_id` in sessione |
| POST | `/logout` | logout — svuota sessione |

### Reminders
| Metodo | Endpoint | Note |
|--------|----------|------|
| GET | `/reminders` | lista HTML (HTMX fragment) — params: `sort`, `show_deleted` |
| POST | `/reminders` | crea reminder (form multipart) |
| PUT | `/reminders/{id}` | modifica reminder |
| PATCH | `/reminders/{id}/status` | cambia solo status |
| DELETE | `/reminders/{id}` | soft delete (`deleted_at` + `status=deleted`) |

**Sort disponibili:** `status` (default) · `date` · `date_desc` · `id` · `id_desc`

### Confirm
| Metodo | Endpoint | Funzione |
|--------|----------|----------|
| POST | `/confirm/{execution_id}` | conferma da web (utente autenticato) |
| POST | `/confirm/bot/{execution_id}` | conferma da bot Telegram (no auth) |

### Settings
| Metodo | Endpoint | Funzione |
|--------|----------|----------|
| GET | `/settings` | lettura token (mascherato) + chat IDs |
| POST | `/settings/token` | salva token bot (verifica su Telegram) |
| POST | `/settings/chat-ids` | aggiorna lista chat IDs |
| POST | `/settings/test` | invia messaggio di test a tutti i chat IDs |
| POST | `/settings/timezone` | aggiorna timezone utente |
| POST | `/settings/password` | cambio password utente |

### Utilità
| Metodo | Endpoint | Funzione |
|--------|----------|----------|
| GET | `/` | dashboard (HTML, Jinja2) |
| GET | `/health` | health check `{"status":"ok"}` |

---

## 9. Frontend

- **Template:** Jinja2 (`frontend/index.html` + `frontend/partials/reminders_list.html`)
- **Static:** `frontend/static/` (CSS + icone)
- **HTMX:** partial reload della lista reminder (polling ogni 30s disabilitato nel log Uvicorn)

**Filtri Jinja2 custom:**
| Filtro | Uso |
|--------|-----|
| `to_local` | UTC → `dd/mm/YYYY HH:MM` locale |
| `to_local_input` | UTC → `YYYY-MM-DDTHH:MM` (input datetime-local) |
| `to_local_short` | UTC → `oggi HH:MM` / `domani HH:MM` / `25 mar` / `25 mar 26` |
| `from_json` | stringa JSON → oggetto |

**Colori stato reminder:**
- `pending` → giallo
- `sent` → arancione
- `completed` / `resolved` → verde
- `paused` → grigio
- `deleted` → rosso/barrato

---

## 10. Sicurezza

- Login con username + password (bcrypt hash)
- Cookie session (`SessionMiddleware`, `max_age=86400` — 24h)
- `SECRET_KEY` da variabile ENV (`SECRET_KEY`, fallback stringa di default)
- Escape HTML su tutti i messaggi (validatore Pydantic `html.escape`)
- Chat ID Telegram autorizzati verificati a ogni callback del bot
- Token Telegram validato via API `/getMe` prima di salvarlo

---

## 11. Backup

- Funzione: `scheduler/backup.py` → `run_backup()`
- Percorso: `data/backups/reminder_YYYYMMDD_HHMMSS.db`
- Conservati: ultimi `backup_keep` (default 7) backup
- Trigger: ogni 24h dallo scheduler

---

## 12. Log

- File: `logs/app.log`
- Rotazione FIFO: max `log_max_size_mb` (10 MB), cleanup fino a `log_cleanup_mb` (5 MB) liberi
- DB log: tabella `logs` per eventi importanti (backup, conferme bot, errori scheduler)
- Uvicorn: `log_level=warning`, `access_log=False` (evita flood di GET /reminders)

---

## 13. Docker

```yaml
# docker/docker-compose.yml (estratto)
container_name: reminder_system
restart: unless-stopped
ports: ["8000:8000"]
environment:
  - APP_ENV=prod
  - SECRET_KEY=${SECRET_KEY}
volumes:
  - ./data:/app/data       # DB + backup persistenti
  - ./logs:/app/logs       # log persistenti
  - ./config.yaml:/app/config.yaml:ro
healthcheck:
  test: urllib.request.urlopen('http://localhost:8000/health')
  interval: 30s
```

---

## 14. Struttura Cartelle

```text
Reminder/
├── backend/
│   ├── main.py            # FastAPI app, startup, mount router
│   ├── auth.py            # login/logout, bcrypt, get_current_user
│   ├── database.py        # init_db, get_connection, settings CRUD, get_telegram_config
│   ├── models.py          # Pydantic: LoginRequest, ReminderCreate/Update/Out
│   └── routers/
│       ├── reminders.py   # CRUD reminder + filtri Jinja2
│       ├── confirm.py     # conferma web + bot (_apply_confirmation)
│       └── settings.py    # token, chat-ids, test, timezone, password
├── scheduler/
│   ├── scheduler.py       # start_scheduler, BackgroundScheduler setup
│   ├── jobs.py            # check_and_send, resend_unconfirmed, recover, startup
│   ├── backup.py          # run_backup
│   └── log_manager.py     # get_logger, db_log, rotazione log
├── bot/
│   └── bot.py             # polling Telegram, /start, callback confirm
├── frontend/
│   ├── index.html         # dashboard Jinja2
│   ├── partials/
│   │   └── reminders_list.html
│   └── static/
│       ├── style.css
│       └── icon.png
├── data/
│   ├── reminder.db        # SQLite database
│   └── backups/           # backup automatici
├── logs/
│   └── app.log
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── config.yaml            # configurazione centrale (NON committare con token reali)
└── requirements.txt
```

---

## 15. Flussi Operativi

### Creazione reminder
1. Form dashboard → `POST /reminders` (multipart)
2. Backend converte `next_execution` dalla timezone utente a UTC (`_localize_to_utc`)
3. Costruisce `recurrence_json` se selezionata una ricorrenza
4. Salva su DB con `status=pending`
5. HTMX aggiorna la lista senza reload

### Invio reminder (scheduler)
1. `check_and_send_reminders()` legge reminder `pending` con `next_execution ≤ NOW`
2. Invia a tutti i `chat_ids` configurati con pulsante `✔ Confermato`
3. Crea record in `executions`
4. Se ricorrente: aggiorna `next_execution` → prossima occorrenza, `status=pending`
5. Se non ricorrente: `status=sent`
6. Aggiorna `last_sent_at`

### Conferma utente (bot)
1. Click `✔ Confermato` sul messaggio Telegram
2. `callback_handler` verifica `chat_id` autorizzato
3. Chiama `_apply_confirmation(conn, reminder_id, execution_id)`
4. Reminder ricorrente → `status=pending`, `last_sent_at=NULL`
5. Reminder non ricorrente → `status=resolved`
6. Tutte le executions non confermate dello stesso reminder vengono chiuse (anti-duplicati)

### Reminder non confermati (solleciti)
1. `resend_unconfirmed_reminders()` ogni ora
2. Cerca executions con `confirmed=0` inviate > 1h fa
3. Reinvia messaggio con prefisso `🔁 SOLLECITO`
4. Ripete **infinito** finché non confermato

### Recovery dopo riavvio
1. `recover_stuck_reminders()` a ogni startup
2. Reminder `pending` nel passato → inviati con prefisso `⏰ PERSO (X min fa)`
3. Reminder `sent` ricorrenti nel passato → riprogrammati senza reinvio
4. `_resend_on_startup()` → solleciti immediati per executions non confermate

### Modifica / Pausa / Eliminazione
- **Modifica** → aggiorna DB (next_execution riconvertita in UTC)
- **Pausa** → `status=paused` → scheduler ignora
- **Soft delete** → `deleted_at=NOW`, `status=deleted` → non visibile in lista (a meno di `show_deleted=true`)
