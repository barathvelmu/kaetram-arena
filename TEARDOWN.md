# Teardown & Storage Restore Guide

Documents what this project installs on the dev machine and how to fully
uninstall it to reclaim disk space.

> Targets the Debian GCP VM (`gcp-vm`, see top-level `~/CLAUDE.md`). nvm is
> the system-wide Node manager — there is no Homebrew here. If you forked this
> onto a macOS laptop, swap `nvm install/uninstall` for whatever you used
> there (likely `brew install nvm`).

## What Was Installed

| Thing | Location | Size (approx) |
|---|---|---|
| nvm | `~/.nvm` | ~10 MB |
| Node.js 20 (via nvm) | `~/.nvm/versions/node/v20.x.x/` | ~200 MB |
| Kaetram-Open repo | `~/projects/Kaetram-Open/` | ~100 MB |
| Kaetram node_modules | `~/projects/Kaetram-Open/node_modules/` + workspace `packages/*/node_modules/` | ~800 MB–1.5 GB |
| Kaetram build output | `~/projects/Kaetram-Open/packages/*/dist/` | ~200 MB |
| Python deps (websockets, mcp, transformers, …) | system site-packages or project venv | varies |
| **Total** | | **~1.3–2 GB** |

---

## Teardown Steps

### 1. Stop all agent / game processes

Use the project's own kill script — never broad `pkill` patterns (see
`feedback_no_pkill_tmux` memory).

```bash
cd ~/projects/kaetram-agent && scripts/nuke-agents.sh
```

### 2. Remove Kaetram-Open

```bash
rm -rf ~/projects/Kaetram-Open
```

### 3. Remove Node.js 20 installed via nvm

```bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm uninstall 20
```

### 4. Remove nvm itself

```bash
rm -rf ~/.nvm
# Also strip the NVM_DIR / nvm.sh source lines from ~/.zshrc if you
# don't plan to reinstall.
```

### 5. Remove Python deps (only if you used a project venv)

```bash
rm -rf ~/projects/kaetram-agent/.venv
```

If you `pip install`'d into system Python, uninstall packages individually
rather than blanket-removing — system tools may depend on them.

### 6. Clean up runtime state in this repo

```bash
cd ~/projects/kaetram-agent

# Per-sandbox runtime state (logs, .mcp.json, screenshots)
rm -rf sandbox_*/

# Raw session logs + dataset frames (large; gitignored)
rm -rf dataset/raw/agent_*/runs/
rm -rf dataset/*/frames/ 2>/dev/null || true

# HLS livestream segments
rm -rf /tmp/hls/agent_*
```

---

## Verify Storage Reclaimed

```bash
ls ~/projects/Kaetram-Open 2>/dev/null && echo "STILL EXISTS" || echo "removed ✓"
ls ~/.nvm 2>/dev/null && echo "STILL EXISTS" || echo "removed ✓"
node --version 2>/dev/null || echo "node removed ✓"
df -h ~
```

---

## Keep But Trim (recommended)

If you want to keep Kaetram-Open available but reclaim ~1 GB, just delete
`node_modules` and `dist`. `yarn install && yarn build` restores them.

```bash
find ~/projects/Kaetram-Open -name node_modules -type d -prune -exec rm -rf {} +
find ~/projects/Kaetram-Open -name dist         -type d -prune -exec rm -rf {} +
```

To restore:
```bash
. "$HOME/.nvm/nvm.sh" && nvm use 20
cd ~/projects/Kaetram-Open && yarn install && yarn build
```

---

## Re-setup From Scratch

```bash
# 1. Install nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
. "$HOME/.nvm/nvm.sh"

# 2. Install Node 20 (uWS.js requires 16/18/20 — NOT 24/25)
nvm install 20 && nvm use 20

# 3. Clone and build Kaetram
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/Kaetram/Kaetram-Open.git
cd Kaetram-Open
printf "ACCEPT_LICENSE=true\nSKIP_DATABASE=true\nTUTORIAL_ENABLED=false\n" > .env
yarn install && yarn build

# 4. Project Python deps
cd ~/projects/kaetram-agent
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt   # if present; else install per-script as needed

# 5. Smoke test
scripts/start-kaetram.sh
```
