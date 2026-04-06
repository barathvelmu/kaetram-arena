#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Optional per-machine notification env. Keep secrets out of git.
if [[ -f "${HOME}/.kaetram_notify_env" ]]; then
  # shellcheck disable=SC1090
  source "${HOME}/.kaetram_notify_env"
fi

cd "$ROOT"

CLAUDE_FLAGS="-p /compile-research --allowedTools Read,Edit,Write,Bash,Glob,Grep --model claude-opus-4-6"

auto_commit() {
  git add research/ session_log.md 2>/dev/null || true
  if ! git diff --staged --quiet; then
    git commit -m "[auto] compile-research $(date -u '+%Y-%m-%d %H:%M UTC')"
    if git pull --rebase --autostash origin main && git push origin main; then
      echo "[research] changes pushed"
    else
      echo "[research] push failed after rebase attempt"
      python3 scripts/check_research_staleness.py --notify || true
      return 1
    fi
  else
    echo "[research] compile-research ran — no file changes"
  fi
}

# Run staleness check. If stale, auto-compile with Claude Code instead of just emailing.
if ! python3 scripts/check_research_staleness.py 2>/dev/null; then
  echo "[research] stale — running claude compile-research (opus-4-6)..."
  if command -v claude &>/dev/null; then
    # shellcheck disable=SC2086
    claude $CLAUDE_FLAGS && auto_commit || echo "[research] compile-research failed"
  else
    # claude not in PATH for cron — try common install locations
    CLAUDE_BIN="${HOME}/.local/bin/claude"
    if [[ -x "$CLAUDE_BIN" ]]; then
      # shellcheck disable=SC2086
      "$CLAUDE_BIN" $CLAUDE_FLAGS && auto_commit || echo "[research] compile-research failed"
    else
      echo "[research] claude CLI not found, falling back to email nudge"
      python3 scripts/check_research_staleness.py --notify
    fi
  fi
else
  echo "[research] OK — docs are fresh"
fi
