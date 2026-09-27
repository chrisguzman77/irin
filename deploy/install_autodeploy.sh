#!/usr/bin/env bash
# One-time, on the Vultr server as root: install the timer that runs
# deploy/autodeploy.sh every 2 minutes (redeploy when the deploy branch moves).
set -euo pipefail
cat > /etc/systemd/system/irin-autodeploy.service <<'EOF'
[Unit]
Description=Irin: redeploy when the deploy branch moves
[Service]
Type=oneshot
ExecStart=/bin/bash /opt/irin/deploy/autodeploy.sh
EOF
cat > /etc/systemd/system/irin-autodeploy.timer <<'EOF'
[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now irin-autodeploy.timer
systemctl list-timers irin-autodeploy.timer --no-pager
echo "installed: the server now follows the deploy branch (journalctl -u irin-autodeploy -n 20)"
