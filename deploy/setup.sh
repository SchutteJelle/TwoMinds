#!/usr/bin/env bash
# First-time server setup for TwoMinds on a fresh Hetzner Ubuntu server.
# Run once as root: bash setup.sh <server-ip> <git-repo-url>
#
# Uses sslip.io for automatic SSL without a domain name.
# Your app will be available at https://<ip-with-dashes>.sslip.io
# e.g. IP 1.2.3.4 => https://1-2-3-4.sslip.io
set -euo pipefail

SERVER_IP="${1:?Usage: $0 <server-ip> <git-repo-url>}"
REPO_URL="${2:?Usage: $0 <server-ip> <git-repo-url>}"

# Convert dots to dashes for sslip.io (e.g. 1.2.3.4 -> 1-2-3-4.sslip.io)
SSLIP_DOMAIN="${SERVER_IP//./-}.sslip.io"
APP_DIR="/opt/twominds"
APP_USER="twominds"

echo "==> sslip.io domain: $SSLIP_DOMAIN"

echo "==> Installing system packages"
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git

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
sed "s/YOUR_SSLIP_DOMAIN/$SSLIP_DOMAIN/g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/twominds
ln -sf /etc/nginx/sites-available/twominds /etc/nginx/sites-enabled/twominds
rm -f /etc/nginx/sites-enabled/default
# Temporary HTTP-only config so certbot can complete its challenge
sed -i '/listen 443/,/^}/d; /return 301/d' /etc/nginx/sites-available/twominds
nginx -t
systemctl reload nginx

echo "==> Obtaining SSL certificate via Let's Encrypt"
certbot --nginx -d "$SSLIP_DOMAIN" --non-interactive --agree-tos -m "admin@$SSLIP_DOMAIN"

echo ""
echo "==> Done! App will be available at: https://$SSLIP_DOMAIN"
echo ""
echo "    Next steps:"
echo "    1. Edit $APP_DIR/.env and set ANTHROPIC_API_KEY"
echo "    2. Run: systemctl start twominds"
echo "    3. Check status: systemctl status twominds"
echo "    4. View logs: journalctl -u twominds -f"
