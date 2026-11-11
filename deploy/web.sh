#!/usr/bin/env bash
# Opens ports and sets up HTTPS via Caddy on <ip>.sslip.io (no domain needed). Safe to re-run.
set -u
for p in 80 443 8080; do
  iptables -C INPUT -p tcp --dport $p -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport $p -j ACCEPT
done
command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null 2>&1 || true
if [ ! -x /usr/local/bin/caddy ]; then
  curl -fsSL -o /tmp/caddy.tgz https://github.com/caddyserver/caddy/releases/download/v2.8.4/caddy_2.8.4_linux_amd64.tar.gz &&
  tar xzf /tmp/caddy.tgz -C /usr/local/bin caddy && chmod +x /usr/local/bin/caddy || { echo "caddy download failed"; exit 1; }
fi
IP=$(curl -fsS -m 10 https://api.ipify.org) || exit 1
HOST="${IP//./-}.sslip.io"
mkdir -p /etc/caddy /var/lib/caddy
printf '%s {\n  reverse_proxy 127.0.0.1:8080\n}\n' "$HOST" > /etc/caddy/Caddyfile
echo "$HOST" > /var/lib/euroled-admin/host.txt
cat > /etc/systemd/system/caddy-euroled.service <<EOU
[Unit]
Description=Caddy HTTPS for EUROLED admin
After=network-online.target
[Service]
Environment=XDG_DATA_HOME=/var/lib/caddy HOME=/var/lib/caddy
ExecStart=/usr/local/bin/caddy run --config /etc/caddy/Caddyfile
Restart=always
[Install]
WantedBy=multi-user.target
EOU
systemctl daemon-reload
systemctl enable caddy-euroled >/dev/null 2>&1
systemctl restart caddy-euroled
