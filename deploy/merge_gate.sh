#!/usr/bin/env bash
# Merge one branch into dev safely, from any session or worktree on Chris's Mac.
#   bash deploy/merge_gate.sh <branch>      e.g. bash deploy/merge_gate.sh justin/b2-watch
# What it does, in a scratch worktree (never the caller's checkout):
#   1. fetch; start from origin/dev; merge origin/<branch> --no-ff (a conflict stops here)
#   2. refuse if the merge touches a shared file (those go through Chris's main session)
#   3. run the tests for what changed: backend, relay, cloud (the ones that exist here)
#   4. push to dev (never --force); if dev moved meanwhile, start over (up to 3 times)
#   5. fast-forward the `deploy` branch to that commit: the server auto-deploys `deploy`
# Exit 0 = merged and pushed; anything else = nothing was pushed.
set -euo pipefail

BRANCH="${1:?usage: merge_gate.sh <branch>}"
REPO="$(git rev-parse --path-format=absolute --git-common-dir)/.."
REPO="$(cd "$REPO" && pwd)"
PY="$REPO/.venv/bin/python"
SHARED='^(backend/app/contracts\.py|deploy/|\.env\.example|docs/|demo/|relay/README\.md|cloud/README\.md|cloud/ingest\.py|CLAUDE\.md|\.claude/)'

for attempt in 1 2 3; do
  git -C "$REPO" fetch -q origin
  git -C "$REPO" rev-parse -q --verify "origin/$BRANCH" >/dev/null || { echo "no branch origin/$BRANCH (push it first)"; exit 2; }
  TMP="$(mktemp -d /tmp/irin-gate.XXXXXX)"
  trap 'git -C "$REPO" worktree remove --force "$TMP" >/dev/null 2>&1 || true' EXIT
  git -C "$REPO" worktree add -q --detach "$TMP" origin/dev
  cd "$TMP"
  BASE="$(git rev-parse HEAD)"
  if git merge-base --is-ancestor "origin/$BRANCH" HEAD; then echo "already in dev: nothing to do"; exit 0; fi
  git merge -q --no-ff "origin/$BRANCH" -m "Merge $BRANCH into dev (merge_gate)" || { echo "CONFLICT merging $BRANCH into dev: merge origin/dev into your branch, resolve, push, rerun"; exit 3; }
  CHANGED="$(git diff --name-only "$BASE" HEAD)"
  if echo "$CHANGED" | grep -Eq "$SHARED"; then
    echo "REFUSED: touches shared files; ask Chris's main session to merge:"; echo "$CHANGED" | grep -E "$SHARED"; exit 4
  fi
  if echo "$CHANGED" | grep -q '^backend/\|^ml/models/\|^hardware/'; then
    (cd backend && IRIN_HW=mock "$PY" -m pytest -q -x 2>&1 | tail -3) | tee /tmp/irin-gate-backend.log
    grep -q " passed" /tmp/irin-gate-backend.log && ! grep -q "failed\|error" /tmp/irin-gate-backend.log || { echo "backend tests FAILED: not pushed"; exit 5; }
  fi
  if echo "$CHANGED" | grep -q '^relay/'; then
    (cd relay && "$PY" -m pytest -q -x 2>&1 | tail -3) | tee /tmp/irin-gate-relay.log
    ! grep -q "failed\|error" /tmp/irin-gate-relay.log || { echo "relay tests FAILED: not pushed"; exit 5; }
  fi
  if echo "$CHANGED" | grep -q '^cloud/'; then
    (cd cloud && TIGER_URI="${TIGER_URI_TEST:-postgresql://postgres:postgres@127.0.0.1:5432/irin}" "$PY" -m pytest -q -x 2>&1 | tail -3) | tee /tmp/irin-gate-cloud.log
    ! grep -q "failed\|error" /tmp/irin-gate-cloud.log || { echo "cloud tests FAILED: not pushed"; exit 5; }
  fi
  if echo "$CHANGED" | grep -q '^frontend/app/src/' && ! echo "$CHANGED" | grep -q '^frontend/app/dist/'; then
    echo "REFUSED: frontend/app/src changed but dist was not rebuilt (npm run build, commit dist, push, rerun)"; exit 6
  fi
  if git push -q origin HEAD:dev 2>/dev/null; then
    git push -q origin HEAD:deploy 2>/dev/null || git push -q origin "+HEAD:deploy"  # deploy only ever follows a gated dev
    echo "MERGED $BRANCH -> dev $(git rev-parse --short HEAD); deploy advanced"
    exit 0
  fi
  echo "dev moved during the gate (attempt $attempt); retrying"
  cd "$REPO"; git worktree remove --force "$TMP" >/dev/null 2>&1 || true; trap - EXIT
done
echo "gave up after 3 attempts: dev keeps moving; rerun in a minute"; exit 7
