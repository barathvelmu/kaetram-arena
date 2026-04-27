#!/usr/bin/env bash
# Start Kaetram game server (requires Node 20 — uWS.js incompatible with Node 24)
set -euo pipefail

# ── --help / -h guard (auto-injected) ────────────────────────────────────────
for _arg in "$@"; do
  case "$_arg" in
    -h|--help)
      awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
      exit 0
      ;;
  esac
done

NVM_SH="$HOME/.nvm/nvm.sh"
[ -f "$NVM_SH" ] || NVM_SH="$(brew --prefix nvm 2>/dev/null)/nvm.sh"
source "$NVM_SH"
nvm use 20
cd ~/projects/Kaetram-Open
export ACCEPT_LICENSE=true
export SKIP_DATABASE=false
yarn start
