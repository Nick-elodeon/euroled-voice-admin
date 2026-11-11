#!/usr/bin/env bash
# Auto-update: pulls origin/main every minute (systemd timer); redeploys only when the commit changed.
set -euo pipefail
SRC=/opt/euroled-src
cd "$SRC"
git fetch -q origin main
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && exit 0
git reset -q --hard origin/main
{ date -u +"%F %T deploy $(git rev-parse --short HEAD)"; bash "$SRC/deploy/setup.sh" "$SRC"; systemctl restart euroled-admin; } >> /var/log/euroled-sync.log 2>&1
