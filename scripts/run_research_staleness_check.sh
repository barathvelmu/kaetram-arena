#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Optional per-machine notification env. Keep secrets out of git.
if [[ -f "${HOME}/.kaetram_notify_env" ]]; then
  # shellcheck disable=SC1090
  source "${HOME}/.kaetram_notify_env"
fi

cd "$ROOT"

# Run staleness check. If stale, auto-compile with Claude Code instead of just emailing.
if ! python3 scripts/check_research_staleness.py 2>/dev/null; then
  echo "[research] stale — running claude compile-research..."
  if command -v claude &>/dev/null; then
    claude -p "/compile-research" --allowedTools "Read,Edit,Write,Bash,Glob,Grep" \
      && echo "[research] compile-research done" \
      || echo "[research] compile-research failed"
  else
    # claude not in PATH for cron — try common install locations
    CLAUDE_BIN="${HOME}/.local/bin/claude"
    if [[ -x "$CLAUDE_BIN" ]]; then
      "$CLAUDE_BIN" -p "/compile-research" --allowedTools "Read,Edit,Write,Bash,Glob,Grep" \
        && echo "[research] compile-research done" \
        || echo "[research] compile-research failed"
    else
      echo "[research] claude CLI not found, falling back to email nudge"
      python3 scripts/check_research_staleness.py --notify
    fi
  fi
else
  echo "[research] OK — docs are fresh"
fi
