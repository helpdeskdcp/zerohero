#!/usr/bin/env bash
# ============================================================================
# Chanakya AI — one-key installer for Debian/Ubuntu VPS
#
# What it does:
#   1. Installs system packages (python3, venv, nginx, sqlite3, ufw)
#   2. Creates a dedicated 'chanakya' system user
#   3. Deploys this project to /opt/chanakya-app
#   4. Builds a Python venv and installs backend dependencies
#   5. Installs + enables a systemd service for the FastAPI backend
#   6. Installs + enables an Nginx reverse proxy (with WebSocket support)
#   7. Opens the firewall for SSH/HTTP/HTTPS (ufw)
#   8. Optionally issues a free HTTPS cert via certbot if you pass a domain
#
# Usage (run as root, from inside the extracted project folder):
#   sudo bash install.sh                     # bind on server IP, HTTP only
#   sudo bash install.sh trading.example.com # also provisions HTTPS via certbot
# ============================================================================
set -euo pipefail

SERVER_NAME="${1:-_}"
APP_DIR="/opt/chanakya-app"
APP_USER="chanakya"
LOG_DIR="/var/log/chanakya-app"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

c_green() { echo -e "\033[0;32m$1\033[0m"; }
c_yellow() { echo -e "\033[0;33m$1\033[0m"; }
c_red() { echo -e "\033[0;31m$1\033[0m"; }

if [[ $EUID -ne 0 ]]; then
  c_red "Run this as root:  sudo bash install.sh"
  exit 1
fi

echo "============================================================"
echo " Chanakya AI — installing to ${APP_DIR}"
echo " Server name: ${SERVER_NAME}"
echo "============================================================"

# ---------------------------------------------------------------- 1. Packages
c_yellow "[1/8] Installing system packages…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  nginx sqlite3 curl ufw ca-certificates rsync

# ---------------------------------------------------------------- 2. System user
c_yellow "[2/8] Creating system user '${APP_USER}'…"
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "${APP_USER}"
fi

# ---------------------------------------------------------------- 3. Deploy files
c_yellow "[3/8] Deploying application files…"
mkdir -p "${APP_DIR}"
rsync -a --delete \
  --exclude 'backend/venv' \
  --exclude 'backend/data/*.db*' \
  --exclude '.git' \
  "${SRC_DIR}/" "${APP_DIR}/" 2>/dev/null || cp -r "${SRC_DIR}/." "${APP_DIR}/"

mkdir -p "${APP_DIR}/backend/data"
mkdir -p "${LOG_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" "${LOG_DIR}"
chmod 600 "${APP_DIR}/backend/.env" 2>/dev/null || true

# ---------------------------------------------------------------- 4. Python venv
c_yellow "[4/8] Building Python virtual environment…"
sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/backend/venv"
sudo -u "${APP_USER}" "${APP_DIR}/backend/venv/bin/pip" install --upgrade pip -q
sudo -u "${APP_USER}" "${APP_DIR}/backend/venv/bin/pip" install -q -r "${APP_DIR}/backend/requirements.txt"

# ---------------------------------------------------------------- 5. systemd service
c_yellow "[5/8] Installing systemd service…"
cp "${APP_DIR}/scripts/chanakya-app.service" /etc/systemd/system/chanakya-app.service
systemctl daemon-reload
systemctl enable chanakya-app
systemctl restart chanakya-app
sleep 2
if systemctl is-active --quiet chanakya-app; then
  c_green "   backend service is running."
else
  c_red "   backend service failed to start — check: journalctl -u chanakya-app -n 50"
fi

# ---------------------------------------------------------------- 6. Nginx
c_yellow "[6/8] Configuring Nginx reverse proxy…"
sed "s/__SERVER_NAME__/${SERVER_NAME}/" "${APP_DIR}/scripts/nginx-chanakya-app.conf" \
  > /etc/nginx/sites-available/chanakya-app
ln -sf /etc/nginx/sites-available/chanakya-app /etc/nginx/sites-enabled/chanakya-app
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx || systemctl restart nginx

# ---------------------------------------------------------------- 7. Firewall
c_yellow "[7/8] Configuring firewall (ufw)…"
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 'Nginx Full' >/dev/null 2>&1 || true
if ! ufw status | grep -q "Status: active"; then
  ufw --force enable
fi

# ---------------------------------------------------------------- 8. HTTPS (optional)
if [[ "${SERVER_NAME}" != "_" ]]; then
  c_yellow "[8/8] Issuing HTTPS certificate for ${SERVER_NAME} via certbot…"
  apt-get install -y --no-install-recommends certbot python3-certbot-nginx
  certbot --nginx -d "${SERVER_NAME}" --non-interactive --agree-tos \
    -m "admin@${SERVER_NAME}" --redirect || \
    c_red "   certbot failed — you can re-run: certbot --nginx -d ${SERVER_NAME}"
else
  c_yellow "[8/8] Skipping HTTPS (no domain passed). Re-run with a domain to enable it:"
  echo "         sudo bash install.sh yourdomain.com"
fi

echo ""
c_green "============================================================"
c_green " Chanakya AI is live."
c_green "============================================================"
IP=$(curl -s -4 ifconfig.me || hostname -I | awk '{print $1}')
if [[ "${SERVER_NAME}" != "_" ]]; then
  echo "   URL:            https://${SERVER_NAME}"
else
  echo "   URL:            http://${IP}"
fi
echo "   Service:        systemctl status chanakya-app"
echo "   Logs:           journalctl -u chanakya-app -f"
echo "   App files:      ${APP_DIR}"
echo "   Env file:       ${APP_DIR}/backend/.env  (chmod 600, keep private)"
echo "   Database:       ${APP_DIR}/backend/data/chanakya.db"
echo ""
echo "   PAPER MODE ONLY — no live orders are ever placed by this app."
echo "============================================================"
