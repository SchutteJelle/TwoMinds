#!/usr/bin/env bash
# First-time server setup for TwoMinds on a fresh Hetzner Ubuntu server.
# Run once as root: bash setup.sh <git-repo-url>
set -euo pipefail

REPO_URL="${1:?Usage: $0 <git-repo-url>}"
APP_DIR="/opt/twominds"
APP_USER="twominds"

echo "==> Installing system packages"
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip nginx git

echo "==> Creating app user"
id "$APP_USER" &>/dev/null || useradd --system --shell /bin/bash --home "$APP_DIR" "$APP_USER"

echo "==> Cloning repository"
if [ -d "$APP_DIR/.git" ]; then
    echo "    Repository already exists, skipping clone"
else
    git clone "$REPO_URL" "$APP_DIR"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

echo "==> Creating Python virtualenv"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> Setting up .env"
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo ""
    echo "    !!! Edit $APP_DIR/.env and set ANTHROPIC_API_KEY before starting the service !!!"
fi

echo "==> Installing systemd service"
cp "$APP_DIR/deploy/twominds.service" /etc/systemd/system/twominds.service
systemctl daemon-reload
systemctl enable twominds

echo "==> Configuring nginx"
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/twominds
ln -sf /etc/nginx/sites-available/twominds /etc/nginx/sites-enabled/twominds
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo ""
echo "==> Done! Next steps:"
echo "    1. Edit $APP_DIR/.env and set ANTHROPIC_API_KEY"
echo "    2. Run: systemctl start twominds"
echo "    3. Check status: systemctl status twominds"
echo "    4. View logs: journalctl -u twominds -f"
