#!/usr/bin/env bash
# One-shot installer for an Ubuntu/Oracle Linux VM. Run as root (or with sudo).
# Usage: sudo bash setup.sh <path-to-repo-checkout-or-extracted-archive>
# Secrets are NOT in this script: afterwards edit /etc/euroled-admin.env and start the service.
set -euo pipefail
SRC="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
APP=/opt/euroled-admin

if command -v apt-get >/dev/null; then
  if ! (command -v rsync && command -v git && python3 -c "import venv, ensurepip") >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y && apt-get install -y python3 python3-venv rsync git curl
  fi
  PY=python3
else
  dnf install -y python3.11 rsync git
  PY=python3.11
fi

mkdir -p "$APP" /var/lib/euroled-admin
rsync -a --delete --exclude .git --exclude .env "$SRC"/bot "$SRC"/requirements.txt "$APP"/
[ -x "$APP/venv/bin/python" ] || $PY -m venv "$APP/venv"
"$APP/venv/bin/pip" install --no-cache-dir -q -r "$APP/requirements.txt"

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

cat > /etc/systemd/system/euroled-sync.service <<'EOF2'
[Unit]
Description=EUROLED pull and deploy latest code
[Service]
Type=oneshot
ExecStart=/opt/euroled-src/deploy/sync.sh
EOF2
cat > /etc/systemd/system/euroled-sync.timer <<'EOF2'
[Unit]
Description=EUROLED auto-update every minute
[Timer]
OnBootSec=60
OnUnitActiveSec=60
[Install]
WantedBy=timers.target
EOF2

systemctl daemon-reload
systemctl enable euroled-admin
systemctl enable --now euroled-sync.timer
echo "Installed. Fill /etc/euroled-admin.env, then: systemctl restart euroled-admin && journalctl -u euroled-admin -f"
