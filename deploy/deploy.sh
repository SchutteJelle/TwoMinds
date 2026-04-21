#!/usr/bin/env bash
# Deploy latest code to the server. Run on the server as root or via SSH.
set -euo pipefail

APP_DIR="/opt/twominds"
APP_USER="twominds"

echo "==> Pulling latest code"
sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only

echo "==> Installing dependencies"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> Restarting service"
systemctl restart twominds
systemctl is-active --quiet twominds && echo "==> twominds is running" || (echo "ERROR: service failed to start"; journalctl -u twominds -n 20 --no-pager; exit 1)
