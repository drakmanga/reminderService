# 🔔 Reminder System

Sistema web self-hosted per gestione promemoria con notifiche Telegram, conferma interattiva e scheduler automatico.

---

## 🖥️ Installazione su Proxmox (CT Debian) — metodo consigliato

### Metodo A — Automatico (da Proxmox)

Copia il progetto sul nodo Proxmox, poi esegui:

```bash
bash create_ct.sh
```

Lo script ti chiede in ordine:
- ID del CT, hostname, password root
- Storage, dimensione disco, RAM, CPU
- IP statico o DHCP + gateway
- Porta dell'app

Poi crea il CT, copia il progetto e avvia automaticamente `install.sh` dentro il CT.

---

### Metodo B — Manuale (dentro il CT già creato)

Se hai già un CT Debian 12 pronto, copia la cartella del progetto dentro il CT e poi esegui:

```bash
bash /percorso/progetto/install.sh
```

Lo script ti chiede:
- Directory di installazione
- Porta HTTP
- Secret key (o la genera automaticamente)
- Username e password admin
- Timezone
- Token Telegram e Chat ID (opzionale, configurabile dalla UI)

Al termine installa il servizio **systemd** (`reminder.service`) con avvio automatico al boot.

---

### Comandi utili dopo l'installazione

```bash
# Stato servizio
systemctl status reminder

# Log in tempo reale
journalctl -u reminder -f

# Riavvia
systemctl restart reminder

# Log applicativo
tail -f /opt/reminder/logs/app.log
```

---

## 📋 Stack Tecnologico

| Componente | Tecnologia |
|---|---|
| Backend | Python 3.11 + FastAPI |
| Scheduler | APScheduler |
| Database | SQLite |
| Frontend | HTML + HTMX |
| Bot | Telegram (polling) |
| Deploy | Docker su Proxmox/Debian |

---

## 🚀 Setup Iniziale

### 1. Configura `config.yaml`

```yaml
telegram_token: "IL_TUO_BOT_TOKEN"
chat_ids:
  - 12345678       # il tuo Telegram chat_id
  - 87654321       # chat_id della tua ragazza
timezone_default: "Europe/Rome"
```

Per ottenere il token: parla con [@BotFather](https://t.me/BotFather) su Telegram.  
Per ottenere il tuo chat_id: parla con [@userinfobot](https://t.me/userinfobot).

### 2. Cambia le password di default

Modifica `backend/auth.py` nella funzione `create_default_users()`:
- Utente `admin` → password `admin123`
- Utente `ragazza` → password `ragazza123`

> ⚠️ **CAMBIA QUESTE PASSWORD PRIMA DEL DEPLOY!**

---

## 💻 Avvio in modalità Dev (locale)

```bash
# Installa dipendenze
pip install -r requirements.txt

# Avvia il server
python -m backend.main
```

Il sistema sarà disponibile su: http://localhost:8000

---

## 🐳 Deploy con Docker (Proxmox/Debian)

### Prerequisiti
```bash
# Su Debian/Ubuntu
apt-get update && apt-get install -y docker.io docker-compose
```

### Avvio

```bash
# 1. Copia il progetto sul server
scp -r reminder_project/ utente@server:/opt/reminder_project/

# 2. Entra nella cartella
cd /opt/reminder_project

# 3. Crea il file .env
cp .env.example .env
# Modifica .env con una chiave segreta casuale

# 4. Avvia con Docker Compose (dalla cartella docker/)
cd docker
docker compose up -d --build

# 5. Controlla i log
docker compose logs -f
```

Il sistema sarà disponibile su: http://IP_SERVER:8000

### Comandi utili Docker

```bash
# Ferma il container
docker compose down

# Riavvia
docker compose restart

# Aggiorna (dopo modifiche al codice)
docker compose up -d --build

# Vedi log in tempo reale
docker compose logs -f reminder_app

# Accedi al container
docker exec -it reminder_system bash
```

---

## 📁 Struttura del Progetto

```
reminder_project/
├── backend/
│   ├── main.py          # App FastAPI principale + avvio scheduler/bot
│   ├── database.py      # Schema SQLite e connessione
│   ├── models.py        # Modelli Pydantic
│   ├── auth.py          # Autenticazione + gestione sessioni
│   └── routers/
│       ├── reminders.py # CRUD reminder (restituisce HTML per HTMX)
│       └── confirm.py   # Conferma reminder
├── scheduler/
│   ├── scheduler.py     # APScheduler main
│   ├── jobs.py          # Logica invio e reinvio reminder
│   ├── backup.py        # Backup giornaliero DB
│   └── log_manager.py   # Logging con rotazione FIFO
├── bot/
│   └── bot.py           # Bot Telegram polling
├── frontend/
│   ├── index.html       # Dashboard principale (Jinja2 + HTMX)
│   ├── partials/
│   │   └── reminders_list.html  # Fragment HTML lista reminder
│   └── static/
│       └── style.css    # Stile dark theme
├── data/                # SQLite DB e backup (persistente, non in git)
├── logs/                # Log applicazione (non in git)
├── docker/
│   ├── Dockerfile       # Image Python 3.11-slim
│   └── docker-compose.yml
├── config.yaml          # ⚙️ Configurazione centrale
├── requirements.txt     # Dipendenze Python
└── README.md
```

---

## 🔌 API Endpoints

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| POST | `/login` | Login utente |
| POST | `/logout` | Logout |
| GET | `/reminders` | Lista reminder (HTML fragment) |
| POST | `/reminders` | Crea reminder |
| PUT | `/reminders/{id}` | Modifica reminder |
| DELETE | `/reminders/{id}` | Soft delete |
| POST | `/confirm/{execution_id}` | Conferma via web |
| POST | `/confirm/bot/{execution_id}` | Conferma via bot |
| GET | `/health` | Healthcheck |

---

## 🔁 Ricorrenze Supportate

Il campo `recurrence_json` accetta:

```json
{"type": "minutely", "interval": N}   // ogni N minuti  (1–59)
{"type": "hourly",   "interval": N}   // ogni N ore     (1–23)
{"type": "daily",    "interval": N}   // ogni N giorni  (1–6)
{"type": "weekly",   "interval": N}   // ogni N settimane (1–3)
{"type": "monthly",  "interval": N}   // ogni N mesi    (1–11)
```

> Superato il limite superiore di ogni tipo si passa alla ricorrenza successiva (es. 60 minuti → hourly, 24 ore → daily, 7 giorni → weekly, ecc.).

---

## 🗄️ Database Schema

- **users**: credenziali e timezone
- **reminders**: messaggi, prossima esecuzione, stato, ricorrenza
- **executions**: storico invii e conferme
- **logs**: log applicazione con rotazione

---

## ⚙️ Regole Scheduler

- Controllo reminder: ogni **5 secondi**
- Polling Telegram: ogni **2 secondi**
- Reminder non confermati: **reinvio ogni ora, infinito**
- Backup DB: ogni **24 ore**, mantieni ultimi **7 backup**
- Log: rotazione FIFO, max **10 MB**, cleanup a **5 MB**

---

## 🔒 Sicurezza

- Password hashate con **bcrypt**
- Sessioni con cookie sicuro, timeout **24h**
- Solo chat_id autorizzati ricevono notifiche Telegram
- Input sanitizzato (HTML escape)
- Token Telegram in `config.yaml` (escluso da git)

---

## 🐛 Troubleshooting

**Il bot non invia messaggi:**
- Controlla che `telegram_token` in `config.yaml` sia corretto
- Verifica che i `chat_ids` siano corretti
- Il bot non invia messaggi a chat con cui non ha mai interagito: invia `/start` al bot prima

**Il login non funziona:**
- Utenti di default: `admin`/`admin123` e `ragazza`/`ragazza123`
- Se il DB è corrotto: elimina `data/reminder.db` e riavvia

**Docker: permission denied su data/logs:**
```bash
chmod 755 data/ logs/
```

