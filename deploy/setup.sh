#!/usr/bin/env bash
# One-shot installer for an Ubuntu/Oracle Linux VM. Run as root (or with sudo).
# Usage: sudo bash setup.sh <path-to-repo-checkout-or-extracted-archive>
# Secrets are NOT in this script: afterwards edit /etc/euroled-admin.env and start the service.
set -euo pipefail
SRC="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
APP=/opt/euroled-admin

if command -v apt-get >/dev/null; then
  apt-get update -y && apt-get install -y python3 python3-venv rsync
  PY=python3
else
  dnf install -y python3.11 rsync
  PY=python3.11
fi

mkdir -p "$APP" /var/lib/euroled-admin
rsync -a --delete --exclude .git --exclude .env "$SRC"/bot "$SRC"/requirements.txt "$APP"/
$PY -m venv "$APP/venv"
"$APP/venv/bin/pip" install --no-cache-dir -r "$APP/requirements.txt"

if [ ! -f /etc/euroled-admin.env ]; then
  cat > /etc/euroled-admin.env <<'EOF'
TELEGRAM_BOT_TOKEN=
ALLOWED_USER_IDS=
ELEVEN_API_KEY=
ELEVEN_AGENT_ID=agent_9301m72jkp3kferr2jaxjra1zq3v
ELEVEN_BRANCH_ID=agtbrch_7601m72jkq8rec1tx1yw6t9r98k3
STATE_FILE=/var/lib/euroled-admin/state.json
EOF
  chmod 600 /etc/euroled-admin.env
fi

cat > /etc/systemd/system/euroled-admin.service <<EOF
[Unit]
Description=EUROLED voice agent admin bot
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/euroled-admin.env
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/bot/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable euroled-admin
echo "Installed. Fill /etc/euroled-admin.env, then: systemctl restart euroled-admin && journalctl -u euroled-admin -f"
