#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# droplet_setup.sh  —  Run ONCE on a fresh Ubuntu 22.04 / 24.04 Droplet
#
# Usage (from your Mac):
#   ssh root@YOUR_DROPLET_IP "bash -s" < deploy/droplet_setup.sh
#
# What it does:
#   1. Updates the system
#   2. Installs Python 3.11, pip, nginx, certbot
#   3. Creates a non-root deploy user (milapi)
#   4. Clones the repo from GitHub
#   5. Creates a Python venv and installs requirements
#   6. Installs the systemd service
#   7. Installs the nginx config
#   8. Opens firewall ports (80, 443, 22)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/abdulhaqiq/backend-zod.git"
APP_DIR="/opt/milapi"
APP_USER="milapi"
PYTHON="python3.11"

echo "━━━ [1/8] System update ━━━"
apt-get update -y && apt-get upgrade -y

echo "━━━ [2/8] Install dependencies ━━━"
apt-get install -y \
    python3.11 python3.11-venv python3.11-dev python3-pip \
    nginx certbot python3-certbot-nginx \
    git curl ufw build-essential libpq-dev

echo "━━━ [3/8] Create deploy user: ${APP_USER} ━━━"
id "${APP_USER}" &>/dev/null || useradd --system --shell /bin/bash --create-home "${APP_USER}"

echo "━━━ [4/8] Clone repo ━━━"
if [ -d "${APP_DIR}/.git" ]; then
    echo "  Repo already exists, skipping clone."
else
    git clone "${REPO_URL}" "${APP_DIR}"
fi
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo "━━━ [5/8] Python venv + requirements ━━━"
sudo -u "${APP_USER}" bash <<EOF
cd ${APP_DIR}
${PYTHON} -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt
EOF

echo "━━━ [6/8] .env file ━━━"
if [ ! -f "${APP_DIR}/.env" ]; then
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"
    chmod 600 "${APP_DIR}/.env"
    echo ""
    echo "  ⚠️  A blank .env was created at ${APP_DIR}/.env"
    echo "  Edit it now:  nano ${APP_DIR}/.env"
    echo ""
else
    echo "  .env already exists — skipping."
fi

echo "━━━ [7/8] systemd service ━━━"
cp "${APP_DIR}/deploy/mil-api.service" /etc/systemd/system/mil-api.service
systemctl daemon-reload
systemctl enable mil-api
systemctl restart mil-api
systemctl status mil-api --no-pager

echo "━━━ [8/8] nginx + firewall ━━━"
cp "${APP_DIR}/deploy/nginx.conf" /etc/nginx/sites-available/milapi
ln -sf /etc/nginx/sites-available/milapi /etc/nginx/sites-enabled/milapi
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
ufw status

echo ""
echo "✅  Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Fill in your secrets:     nano ${APP_DIR}/.env"
echo "  2. Restart the service:      systemctl restart mil-api"
echo "  3. Add your domain to nginx: nano /etc/nginx/sites-available/milapi"
echo "  4. Get SSL cert:             certbot --nginx -d your-domain.com"
echo ""
