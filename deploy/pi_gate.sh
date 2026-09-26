#!/usr/bin/env bash
# deploy/pi_gate.sh — the dev -> main gate, run ON THE PI from the repo:
#     bash deploy/pi_gate.sh          # test the current checkout
#     bash deploy/pi_gate.sh --pull   # git pull dev first
# Runs the backend suite (IRIN_HW=mock) and the hardware suite and prints the exact
# counts for Chris. DATASOURCE=replay is forced because the Pi's .env is live
# (nightscout) and the backend reads .env on import: without it ~26 tests that assume
# replay fail and the suite polls the real Nightscout. Never touches the running service.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python

if [ "${1:-}" = "--pull" ]; then
  git checkout -q dev && git pull -q --ff-only || { echo "pull failed"; exit 1; }
fi
echo "commit: $(git rev-parse --short HEAD) on $(git branch --show-current) ($(git log -1 --format=%s | cut -c1-70))"
echo "host:   $(uname -m), python $($PY --version 2>&1 | cut -d' ' -f2)"

summary() { grep -E "^(=+ )?[0-9]+ (passed|failed)|no tests ran" | tail -1 | sed -E 's/=+//g; s/^ +| +$//g'; }
b=$(cd backend && IRIN_HW=mock DATASOURCE=replay ../$PY -m pytest -q -p no:cacheprovider 2>&1)
h=$(cd hardware && ../$PY -m pytest -q -p no:cacheprovider 2>&1)
echo "backend:  $(echo "$b" | summary)"
echo "$b" | grep -E "^(FAILED|ERROR) " | sed 's/^/  /'
echo "hardware: $(echo "$h" | summary)"
echo "$h" | grep -E "^(FAILED|ERROR) " | sed 's/^/  /'
echo "$b$h" | grep -qE "^(FAILED|ERROR) | [0-9]+ failed| [0-9]+ error" && { echo "GATE: FAIL"; exit 1; }
echo "GATE: PASS"
