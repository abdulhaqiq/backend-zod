#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh  —  Run from your Mac to push a new release to the Droplet
#
# Usage:
#   ./deploy/deploy.sh root@YOUR_DROPLET_IP
#   ./deploy/deploy.sh milapi@YOUR_DROPLET_IP   (after first setup)
#
# What it does:
#   1. git push to GitHub (origin main)
#   2. SSH into Droplet → git pull → pip install → alembic migrate → restart
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REMOTE="${1:-}"
APP_DIR="/opt/milapi"
SERVICE="mil-api"

if [ -z "${REMOTE}" ]; then
    echo "Usage: $0 <user@droplet-ip>"
    echo "  e.g. $0 root@143.198.x.x"
    exit 1
fi

# ── 1. Push local commits to GitHub ──────────────────────────────────────────
echo "▶ Pushing to GitHub..."
git -C "$(dirname "$0")/.." push origin main

# ── 2. SSH into Droplet and update ───────────────────────────────────────────
echo "▶ Deploying to ${REMOTE}..."
ssh -T "${REMOTE}" << ENDSSH
set -euo pipefail
cd ${APP_DIR}

echo "  → git pull"
git pull origin main

echo "  → pip install (new/changed packages only)"
source venv/bin/activate
pip install --quiet --upgrade -r requirements.txt

echo "  → alembic migrations"
alembic upgrade head

echo "  → restarting ${SERVICE}"
systemctl restart ${SERVICE}
systemctl status ${SERVICE} --no-pager --lines 5

echo ""
echo "✅  Deploy done!"
ENDSSH
