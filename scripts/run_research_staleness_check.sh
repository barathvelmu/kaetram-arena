#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Optional per-machine notification env. Keep secrets out of git.
if [[ -f "${HOME}/.kaetram_notify_env" ]]; then
  # shellcheck disable=SC1090
  source "${HOME}/.kaetram_notify_env"
fi

cd "$ROOT"
python3 scripts/check_research_staleness.py --notify
