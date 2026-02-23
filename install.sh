#!/usr/bin/env bash
# ============================================================
#  Reminder System — Script di installazione interattivo
#  Da eseguire sul CT Debian come root
#  Uso: bash <(curl -fsSL https://raw.githubusercontent.com/.../install.sh)
#   oppure copiare il file sul CT e: bash install.sh
# ============================================================
set -euo pipefail

# ── Colori ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

header()  { echo -e "\n${CYAN}${BOLD}==> $1${RESET}"; }
success() { echo -e "${GREEN}✔ $1${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $1${RESET}"; }
error()   { echo -e "${RED}✘ $1${RESET}"; exit 1; }
ask()     { echo -e "${BOLD}$1${RESET}"; }

# ── Banner ───────────────────────────────────────────────────
clear
echo -e "${CYAN}${BOLD}"
cat << 'EOF'
  ____                _           _
 |  _ \ ___ _ __ ___ (_)_ __   __| | ___ _ __
 | |_) / _ \ '_ ` _ \| | '_ \ / _` |/ _ \ '__|
 |  _ <  __/ | | | | | | | | | (_| |  __/ |
 |_| \_\___|_| |_| |_|_|_| |_|\__,_|\___|_|
  ____            _
 / ___| _   _ ___| |_ ___ _ __ ___
 \___ \| | | / __| __/ _ \ '_ ` _ \
  ___) | |_| \__ \ ||  __/ | | | | |
 |____/ \__, |___/\__\___|_| |_| |_|
        |___/
EOF
echo -e "${RESET}"
echo -e "  ${BOLD}Installazione interattiva per Debian LXC / CT${RESET}"
echo -e "  ──────────────────────────────────────────────"

# ── Controllo root ───────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "Questo script deve essere eseguito come root"

# ── Raccolta parametri ───────────────────────────────────────
header "Configurazione"

ask "\n📁 Directory di installazione [/opt/reminder]:"
read -r INSTALL_DIR
INSTALL_DIR="${INSTALL_DIR:-/opt/reminder}"

ask "🔌 Porta HTTP [8000]:"
read -r APP_PORT
APP_PORT="${APP_PORT:-8000}"

ask "🔑 Secret key sessione (lascia vuoto per generarne una automatica):"
read -r SECRET_KEY
if [[ -z "$SECRET_KEY" ]]; then
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || cat /proc/sys/kernel/random/uuid | tr -d '-')
    warn "Secret key generata automaticamente"
fi

ask "👤 Username admin [admin]:"
read -r ADMIN_USER
ADMIN_USER="${ADMIN_USER:-admin}"

ask "🔒 Password admin:"
read -rs ADMIN_PASS
echo
[[ -z "$ADMIN_PASS" ]] && error "La password non può essere vuota"

ask "🕐 Timezone [Europe/Rome]:"
read -r TIMEZONE
TIMEZONE="${TIMEZONE:-Europe/Rome}"

ask "\n🤖 Token bot Telegram (puoi inserirlo anche dopo dalla UI, premi INVIO per saltare):"
read -r TG_TOKEN

ask "💬 Chat ID Telegram (puoi inserirlo anche dopo dalla UI, premi INVIO per saltare):"
read -r TG_CHAT_ID

echo ""
echo -e "${BOLD}──────────────────────────────────────────────${RESET}"
echo -e "  Riepilogo configurazione:"
echo -e "  • Directory:  ${CYAN}${INSTALL_DIR}${RESET}"
echo -e "  • Porta:      ${CYAN}${APP_PORT}${RESET}"
echo -e "  • Admin:      ${CYAN}${ADMIN_USER}${RESET}"
echo -e "  • Timezone:   ${CYAN}${TIMEZONE}${RESET}"
echo -e "  • Bot TG:     ${CYAN}${TG_TOKEN:-(da configurare nella UI)}${RESET}"
echo -e "${BOLD}──────────────────────────────────────────────${RESET}"
ask "\nProcedere con l'installazione? [s/N]:"
read -r CONFIRM
[[ "${CONFIRM,,}" != "s" ]] && { warn "Installazione annullata."; exit 0; }

# ── Dipendenze sistema ───────────────────────────────────────
header "Installazione dipendenze di sistema"
# Fix locale per evitare warning perl/apt su CT Debian minimali
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    git curl gcc libffi-dev libssl-dev \
    sqlite3 locales > /dev/null
# Genera il locale en_US.UTF-8 se non presente
locale-gen en_US.UTF-8 > /dev/null 2>&1 || true
success "Dipendenze installate"

# ── Crea directory ───────────────────────────────────────────
header "Creazione struttura directory"
mkdir -p "${INSTALL_DIR}"/{data,logs,data/backups}

# ── Copia/aggiorna sorgenti ──────────────────────────────────
header "Copia file progetto"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/backend/main.py" ]]; then
    # Evita cp su se stesso se INSTALL_DIR == SCRIPT_DIR
    if [[ "$(realpath "${SCRIPT_DIR}")" == "$(realpath "${INSTALL_DIR}")" ]]; then
        success "Sorgenti già in ${INSTALL_DIR}, nessuna copia necessaria"
    else
        cp -r "${SCRIPT_DIR}/." "${INSTALL_DIR}/"
        success "Sorgenti copiati da ${SCRIPT_DIR}"
    fi
else
    error "Esegui questo script dalla directory del progetto (dove si trova backend/main.py)"
fi

# ── Ambiente virtuale Python ─────────────────────────────────
header "Creazione ambiente virtuale Python"
python3 -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip -q
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" -q
success "Dipendenze Python installate"

# ── config.yaml ─────────────────────────────────────────────
header "Scrittura config.yaml"
cat > "${INSTALL_DIR}/config.yaml" << EOF
app_env: prod
timezone: ${TIMEZONE}
admin_username: ${ADMIN_USER}
admin_password: ${ADMIN_PASS}
telegram_token: "${TG_TOKEN}"
chat_ids: [${TG_CHAT_ID}]
polling_interval_sec: 2
EOF
chmod 600 "${INSTALL_DIR}/config.yaml"
success "config.yaml creato"

# ── .env ─────────────────────────────────────────────────────
cat > "${INSTALL_DIR}/.env" << EOF
SECRET_KEY=${SECRET_KEY}
DB_PATH=${INSTALL_DIR}/data/reminder.db
APP_PORT=${APP_PORT}
EOF
chmod 600 "${INSTALL_DIR}/.env"

# ── Systemd service ──────────────────────────────────────────
header "Installazione servizio systemd"
cat > /etc/systemd/system/reminder.service << EOF
[Unit]
Description=Reminder System
After=network.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/python -m uvicorn backend.main:app \\
    --host 0.0.0.0 \\
    --port ${APP_PORT} \\
    --no-access-log \\
    --log-level warning
Restart=always
RestartSec=5
StandardOutput=append:${INSTALL_DIR}/logs/app.log
StandardError=append:${INSTALL_DIR}/logs/app.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable reminder.service
systemctl start reminder.service
success "Servizio systemd installato e avviato"

# ── Logrotate ────────────────────────────────────────────────
cat > /etc/logrotate.d/reminder << EOF
${INSTALL_DIR}/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
EOF

# ── Verifica avvio ───────────────────────────────────────────
header "Verifica servizio"
sleep 3
if curl -sf "http://localhost:${APP_PORT}/health" > /dev/null 2>&1; then
    success "Servizio avviato correttamente"
else
    warn "Il servizio potrebbe ancora essere in avvio. Controlla con: journalctl -u reminder -f"
fi

# ── Riepilogo finale ─────────────────────────────────────────
IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${GREEN}${BOLD}"
echo -e "  ✅ Installazione completata!"
echo -e "${RESET}"
echo -e "  🌐 Interfaccia web:  ${CYAN}http://${IP}:${APP_PORT}${RESET}"
echo -e "  👤 Username:         ${CYAN}${ADMIN_USER}${RESET}"
echo -e "  📁 Directory:        ${CYAN}${INSTALL_DIR}${RESET}"
echo -e "  📋 Log:              ${CYAN}journalctl -u reminder -f${RESET}"
echo -e "  🔄 Riavvia:          ${CYAN}systemctl restart reminder${RESET}"
echo -e "  🛑 Ferma:            ${CYAN}systemctl stop reminder${RESET}"
if [[ -z "$TG_TOKEN" ]]; then
    echo ""
    warn "Ricordati di configurare il token Telegram dalla UI → ⚙️ Impostazioni"
fi
echo ""

