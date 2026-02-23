# Reminder System — Blueprint Finale

## 1. Panoramica
Sistema web self-hosted per gestione promemoria con invio notifiche Telegram, multiutente (tu + tua ragazza), conferma interattiva e scheduler modulare.

- **Stack:** Python + FastAPI, APScheduler, SQLite, HTML + HTMX
- **Modalità bot:** polling Telegram
- **Deployment:** Docker su container Proxmox
- **Backup:** automatico giornaliero
- **Logs:** rotazione FIFO max 10 MB

---

## 2. Stack Tecnologico Dettagliato

| Componente | Scelta | Motivazione |
|------------|-------|-------------|
Backend | Python + FastAPI | leggero, rapido, supporta API + scheduler |
Scheduler | APScheduler | gestione job, retry, ricorrenze |
DB | SQLite | sufficiente per 2 utenti, semplice da gestire |
Frontend | HTML + HTMX | leggero, zero complessità JS |
Bot | Telegram polling | semplice, nessun HTTPS richiesto |
Deployment | Docker container | facile backup, restart, upgrade |

---

## 3. Architettura

```
Browser → Frontend HTMX → FastAPI backend → APScheduler (thread separato)
                                  ↘ SQLite (DB persistente)
                                  ↘ Telegram Bot Polling
```

### Scheduler Thread Separato
- Controlla reminder imminenti
- Invio Telegram
- Aggiorna DB (last_sent_at, status)
- Gestisce reminder ricorrenti

---

## 4. Database Schema (SQLite)

### users
| Campo | Tipo | Note |
|-------|------|------|
id | INTEGER PRIMARY KEY AUTOINCREMENT | unico utente |
username | TEXT UNIQUE | login |
password_hash | TEXT | hash sicuro (bcrypt/argon2) |
timezone | TEXT | timezone utente |
created_at | TIMESTAMP | default now |

---

### reminders
| Campo | Tipo | Note |
|-------|------|------|
id | INTEGER PRIMARY KEY AUTOINCREMENT | leggibile |
user_id | INTEGER | FK → users.id |
message | TEXT | max 500 caratteri |
next_execution | TIMESTAMP | UTC |
recurrence_json | TEXT | json ricorrenze |
status | TEXT | pending / sent / completed / paused / deleted |
created_at | TIMESTAMP | default now |
deleted_at | TIMESTAMP | soft delete |
last_sent_at | TIMESTAMP | anti-duplicazione invii |

---

### executions
| Campo | Tipo | Note |
|-------|------|------|
id | INTEGER PRIMARY KEY AUTOINCREMENT | |
reminder_id | INTEGER | FK → reminders.id |
sent_at | TIMESTAMP | quando inviato |
confirmed | BOOLEAN | conferma utente |
confirmed_at | TIMESTAMP | quando confermato |

---

### logs
| Campo | Tipo | Note |
|-------|------|------|
id | INTEGER PRIMARY KEY AUTOINCREMENT | |
type | TEXT | INFO / WARN / ERROR |
message | TEXT | |
created_at | TIMESTAMP | default now |

**Rotazione log**: max 10MB, elimina più vecchi fino a liberare 5MB

---

## 5. Regole Scheduler / Reminder

- Precisione controllo reminder: ogni 5 secondi  
- Polling Telegram: ogni 2 secondi  
- Reminder non confermati → reinvia ogni ora **infinito** finché conferma non ricevuta  
- Ricorrenze perse → backlog → invio tutti i reminder mancati  
- Job aggiornati live quando reminder modificato (no restart necessario)  
- Reminder in pausa → scheduler ignora  
- Soft delete → eliminazione logica (deleted_at)  
- Anti-duplicazione invii → controllo last_sent_at

---

## 6. Telegram Bot

- Polling bot con token e chat_id autorizzati  
- Accetta solo chat_id autorizzati  
- Pulsante conferma ✔  
- Callback aggiornamento DB

---

## 7. Frontend UX

- Dashboard reminder ordinata per **prossima esecuzione crescente**  
- Stato reminder: colore (pending, sent, completed, paused, deleted)  
- Orologio live in alto con timezone modificabile  
- Modifica / elimina / pausa reminder  
- Conferma eliminazione tramite popup

---

## 8. Sicurezza

- Login con username + password  
- Cookie session con timeout 24h  
- Hash password sicuro (bcrypt/argon2)  
- Escape HTML e filtraggio script input

---

## 9. Backup automatico

- Backup giornaliero della cartella /data/reminder.db  
- Mantieni ultimi 7 backup

---

## 10. Configurazione centrale

```yaml
# config.yaml
telegram_token: "BOT_TOKEN"
chat_id: 12345678
polling_interval_sec: 2
scheduler_interval_sec: 5
log_max_size_mb: 10
log_cleanup_mb: 5
timezone_default: "Europe/Rome"
```

---

## 11. API Endpoints

| Metodo | Endpoint | Funzione |
|--------|----------|----------|
POST | /login | login user |
POST | /reminders | crea reminder |
GET | /reminders | lista reminder |
PUT | /reminders/{id} | modifica reminder |
DELETE | /reminders/{id} | soft delete |
POST | /confirm/{execution_id} | conferma reminder |

---

## 12. Struttura cartelle progetto

```text
reminder_project/
├── backend/            # FastAPI
├── scheduler/          # APScheduler jobs
├── frontend/           # HTML + HTMX
├── bot/                # Telegram polling
├── data/               # SQLite DB e backup
├── logs/               # log rotante
├── docker/             # Dockerfile e compose
├── config.yaml         # configurazione centrale
└── README.md           # istruzioni progetto
```

---

## 13. Modalità Dev / Prod

- Dev: log dettagliati, reload automatico  
- Prod: log puliti, stabile, performance ottimizzata  
- Configurabile da variabile ENV

---

## 14. Flussi Operativi

### Creazione reminder
1. Dashboard → crea reminder  
2. Backend valida input  
3. Salva su DB  
4. Scheduler registra job

### Invio reminder
1. Scheduler legge next_execution  
2. Invia messaggio Telegram con pulsante ✔  
3. Aggiorna last_sent_at + status

### Conferma utente
1. Click ✔ sul bot  
2. Backend riceve callback → aggiorna execution + status  
3. Scheduler smette di reinviare

### Reminder non confermati
1. Scheduler secondario ogni ora invia messaggi sollecito  
2. Ripete **finché non confermato**

### Modifica / eliminazione / pausa
- Modifica → aggiorna job live  
- Pausa → scheduler ignora job  
- Soft delete → set deleted_at + status deleted

---

## 15. UX e Regole Finali

- Messaggi max 500 caratteri  
- Ordinamento dashboard → prossima esecuzione crescente  
- Reminder completati → verde  
- Reminder pending → giallo  
- Reminder non confermati → rosso  
- Eliminazione → conferma popup