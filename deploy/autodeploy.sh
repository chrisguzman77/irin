#!/usr/bin/env bash
# On the Vultr server: redeploy whenever the `deploy` branch moves. `deploy` is
# advanced only by deploy/merge_gate.sh after the tests pass, so a broken push
# to dev never reaches the server. Run by irin-autodeploy.timer every 2 minutes;
# safe to run by hand. Logs to the journal (journalctl -u irin-autodeploy).
set -euo pipefail
cd /opt/irin
git fetch -q origin deploy
NEW="$(git rev-parse origin/deploy)"
[ "$(git rev-parse HEAD)" = "$NEW" ] && exit 0
git checkout -q -- deploy/docker-compose.yml 2>/dev/null || true   # never let a hand edit block the update
git checkout -q -B deploy "$NEW"
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
echo "deployed $(git log --oneline -1)"
