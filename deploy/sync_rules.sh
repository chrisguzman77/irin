#!/usr/bin/env bash
# Regenerates .cursor/rules/irin.mdc and AGENTS.md from CLAUDE.md (never edit the copies).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .cursor/rules
{
  printf -- '---\ndescription: Irin rules (generated from CLAUDE.md by deploy/sync_rules.sh; do not edit)\nalwaysApply: true\n---\n'
  cat CLAUDE.md
} > .cursor/rules/irin.mdc
{
  printf '<!-- generated from CLAUDE.md by deploy/sync_rules.sh; do not edit -->\n'
  cat CLAUDE.md
} > AGENTS.md
echo "synced .cursor/rules/irin.mdc and AGENTS.md from CLAUDE.md"
