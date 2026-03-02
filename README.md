# 🔔 Reminder System

Self-hosted web system for managing reminders with Telegram notifications, interactive confirmation and automatic scheduler.

---

## 🖥️ Installation on Proxmox (Debian CT) — recommended method

### Method A — Automatic (from Proxmox)

Copy the project to the Proxmox node, then run:

```bash
bash create_ct.sh
```

The script will ask you for:
- CT ID, hostname, root password
- Storage, disk size, RAM, CPU
- Static IP or DHCP + gateway
- App port

It will then create the CT, copy the project and automatically run `install.sh` inside the CT.

---

### Method B — Manual (inside an existing CT)

If you already have a Debian 12 CT ready, copy the project folder into the CT and run:

```bash
bash /path/to/project/install.sh
```

The script will ask you for:
- Installation directory
- HTTP port
- Secret key (or auto-generated)
- Admin username and password
- Timezone
- Telegram token and Chat ID (optional, configurable from the UI)

At the end it installs the **systemd** service (`reminder.service`) with automatic startup on boot.

---

### Useful commands after installation

```bash
# Service status
systemctl status reminder

# Live logs
journalctl -u reminder -f

# Restart
systemctl restart reminder

# Application log
tail -f /opt/reminder/logs/app.log
```

---

## 📋 Tech Stack

| Component | Technology |
|---|---|
| Backend | Python 3.11 + FastAPI |
| Scheduler | APScheduler |
| Database | SQLite |
| Frontend | HTML + HTMX |
| Bot | Telegram (polling) |
| Deploy | Docker on Proxmox/Debian |

---

## 🚀 Initial Setup

### 1. Configure `config.yaml`

```yaml
telegram_token: "YOUR_BOT_TOKEN"
chat_ids:
  - 12345678       # your Telegram chat_id
  - 87654321       # another chat_id
timezone_default: "Europe/Rome"
```

To get the token: talk to [@BotFather](https://t.me/BotFather) on Telegram.  
To get your chat_id: talk to [@userinfobot](https://t.me/userinfobot).

### 2. Change default passwords

Edit `backend/auth.py` in the `create_default_users()` function:
- User `admin` → password `admin123`
- User `ragazza` → password `ragazza123`

> ⚠️ **CHANGE THESE PASSWORDS BEFORE DEPLOYING!**

---

## 💻 Dev mode (local)

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
python -m backend.main
```

The system will be available at: http://localhost:8000

---

## 🐳 Docker Deploy (Proxmox/Debian)

### Prerequisites
```bash
# On Debian/Ubuntu
apt-get update && apt-get install -y docker.io docker-compose
```

### Start

```bash
# 1. Copy the project to the server
scp -r reminder_project/ user@server:/opt/reminder_project/

# 2. Enter the folder
cd /opt/reminder_project

# 3. Create the .env file
cp .env.example .env
# Edit .env with a random secret key

# 4. Start with Docker Compose (from the docker/ folder)
cd docker
docker compose up -d --build

# 5. Check logs
docker compose logs -f
```

The system will be available at: http://SERVER_IP:8000

### Useful Docker commands

```bash
# Stop the container
docker compose down

# Restart
docker compose restart

# Update (after code changes)
docker compose up -d --build

# Live logs
docker compose logs -f reminder_app

# Access the container
docker exec -it reminder_system bash
```

---

## 📁 Project Structure

```
reminder_project/
├── backend/
│   ├── main.py          # Main FastAPI app + scheduler/bot startup
│   ├── database.py      # SQLite schema and connection
│   ├── models.py        # Pydantic models
│   ├── auth.py          # Authentication + session management
│   └── routers/
│       ├── reminders.py # Reminder CRUD (returns HTML for HTMX)
│       └── confirm.py   # Reminder confirmation
├── scheduler/
│   ├── scheduler.py     # APScheduler main
│   ├── jobs.py          # Send and resend reminder logic
│   ├── backup.py        # Daily DB backup
│   └── log_manager.py   # Logging with FIFO rotation
├── bot/
│   └── bot.py           # Telegram bot polling
├── frontend/
│   ├── index.html       # Main dashboard (Jinja2 + HTMX)
│   ├── partials/
│   │   └── reminders_list.html  # Reminder list HTML fragment
│   └── static/
│       └── style.css    # Dark theme style
├── data/                # SQLite DB and backups (persistent, not in git)
├── logs/                # Application logs (not in git)
├── docker/
│   ├── Dockerfile       # Python 3.11-slim image
│   └── docker-compose.yml
├── config.yaml          # ⚙️ Central configuration
├── requirements.txt     # Python dependencies
└── README.md
```

---

## 🤖 Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | List all available commands with usage examples |
| `/reminders` | Show all active reminders (pending, sent, paused) |
| `/ricordami <when> di <what>` | Create a new reminder |

### `/ricordami` — supported time formats

Time uses **24h format**: `9` = 09:00, `21` = 21:00.
Both `tra` and `fra` are accepted interchangeably.

#### One-shot

| Format | Default time | Example |
|--------|-------------|---------|
| `oggi alle HH[:MM]` | — | `/ricordami oggi alle 18:30 di comprare il pane` |
| `domani alle HH[:MM]` | — | `/ricordami domani alle 9 di contattare Mario` |
| `dopodomani alle HH[:MM]` | — | `/ricordami dopodomani alle 14 di riunione` |
| `stasera [alle HH[:MM]]` | 21:00 | `/ricordami stasera di chiamare Mario` |
| `oggi pomeriggio [alle HH[:MM]]` | 15:00 | `/ricordami oggi pomeriggio di fare la spesa` |
| `stamattina / stamani [alle HH[:MM]]` | 09:00 | `/ricordami stamattina alle 10 di riunione` |
| `stanotte [alle HH[:MM]]` | 23:00 | `/ricordami stanotte di prendere la medicina` |
| `domani mattina [alle HH[:MM]]` | 09:00 | `/ricordami domani mattina di mandare l'email` |
| `domani pomeriggio [alle HH[:MM]]` | 15:00 | `/ricordami domani pomeriggio alle 14 di appuntamento` |
| `domani sera [alle HH[:MM]]` | 21:00 | `/ricordami domani sera di cena con amici` |
| `tra/fra X minuti` | — | `/ricordami tra 30 minuti di controllare il forno` |
| `tra/fra mezz'ora` | — | `/ricordami fra mezz'ora di uscire` |
| `tra/fra X ore` | — | `/ricordami tra 2 ore di chiamare il medico` |
| `tra/fra X giorni` | — | `/ricordami fra 3 giorni di pagare la bolletta` |
| `<weekday> alle HH[:MM]` | — | `/ricordami venerdì alle 20 di cena` |
| `[il] DD mese [YYYY] [alle HH[:MM]]` | — | `/ricordami il 15 aprile alle 9 di visita medica` |

#### Recurring (`ogni …`)

| Format | Example |
|--------|---------|
| `ogni giorno alle HH[:MM]` | `/ricordami ogni giorno alle 8 di fare colazione` |
| `ogni X giorni alle HH[:MM]` | `/ricordami ogni 3 giorni alle 7 di controllare server` |
| `ogni <weekday> alle HH[:MM]` | `/ricordami ogni venerdì alle 9 di chiamare il cliente` |
| `ogni settimana [il <weekday>] alle HH[:MM]` | `/ricordami ogni settimana il lunedì alle 10 di riunione` |
| `ogni inizio mese [alle HH[:MM]]` | `/ricordami ogni inizio mese alle 9 di controllare le spese` |
| `ogni fine mese [alle HH[:MM]]` | `/ricordami ogni fine mese alle 18 di rendiconto` |
| `ogni DD del mese [alle HH[:MM]]` | `/ricordami ogni 18 del mese alle 10 di pagare l'affitto` |
| `ogni mese [il DD] [alle HH[:MM]]` | `/ricordami ogni mese il 5 alle 9 di bolletta` |
| `ogni X mesi [il DD] [alle HH[:MM]]` | `/ricordami ogni 3 mesi il 1 alle 9 di revisione trimestrale` |
| `ogni anno [il DD mese] [alle HH[:MM]]` | `/ricordami ogni anno il 15 marzo alle 9 di visita medica` |

The `di` separator between time and message is optional.
Reminders created via bot are immediately visible in the web dashboard.

---

## 🔌 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/login` | User login |
| POST | `/logout` | Logout |
| GET | `/reminders` | Reminder list (HTML fragment) |
| POST | `/reminders` | Create reminder |
| PUT | `/reminders/{id}` | Edit reminder |
| DELETE | `/reminders/{id}` | Soft delete |
| POST | `/confirm/{execution_id}` | Confirm via web |
| POST | `/confirm/bot/{execution_id}` | Confirm via bot |
| GET | `/health` | Healthcheck |

---

## 🔁 Supported Recurrences

The `recurrence_json` field accepts:

```json
{"type": "minutely", "interval": N}   // every N minutes  (1–59)
{"type": "hourly",   "interval": N}   // every N hours    (1–23)
{"type": "daily",    "interval": N}   // every N days     (1–6)
{"type": "weekly",   "interval": N}   // every N weeks    (1–3)
{"type": "monthly",  "interval": N}   // every N months   (1–11)
```

> Once the upper limit of each type is exceeded, the next recurrence type takes over (e.g. 60 minutes → hourly, 24 hours → daily, 7 days → weekly, etc.).

---

## 🗄️ Database Schema

- **users**: credentials and timezone
- **reminders**: messages, next execution, status, recurrence
- **executions**: send and confirmation history
- **logs**: application logs with rotation

---

## ⚙️ Scheduler Rules

- Reminder check: every **5 seconds**
- Telegram polling: every **2 seconds**
- Unconfirmed reminders: **resent every hour, indefinitely**
- DB backup: every **24 hours**, keeps last **7 backups**
- Logs: FIFO rotation, max **10 MB**, cleanup at **5 MB**

---

## 🔒 Security

- Passwords hashed with **bcrypt**
- Sessions with secure cookie, **24h** timeout
- Only authorized chat_ids receive Telegram notifications
- Sanitized input (HTML escape)
- Telegram token in `config.yaml` (excluded from git)

---

## 🐛 Troubleshooting

**The bot doesn't send messages:**
- Check that `telegram_token` in `config.yaml` is correct
- Verify that `chat_ids` are correct
- The bot cannot message chats it has never interacted with: send `/start` to the bot first

**Login doesn't work:**
- Default users: `admin`/`admin123` and `ragazza`/`ragazza123`
- If the DB is corrupted: delete `data/reminder.db` and restart

**Docker: permission denied on data/logs:**
```bash
chmod 755 data/ logs/
```
