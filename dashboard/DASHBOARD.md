# DASHBOARD.md — Developer Reference

> Read this before modifying any dashboard code. This is the dashboard subsystem of the Kaetram AI Agent project — a live observability dashboard for monitoring autonomous game-playing agents and SFT data collection.

## Architecture

```
dashboard/
├── server.py        # Entry point: ThreadedHTTPServer (:8080), WebSocketRelay (:8081, auto-restart, permessage-deflate), ScreenshotWatcher
├── handler.py       # HTTP routing (HTTP/1.1 keep-alive); HLS, /static/, /ingest/, /screenshots/ endpoints
├── api.py           # APIMixin — all /api/* endpoints (mixed into DashboardHandler)
├── parsers.py       # Session log parsers (Claude/Codex/Gemini/OpenCode); incremental, offset-tracked
├── _log_tail.py     # Offset-tracking JSONL tail helper used by parsers (modeled on activity_heartbeat_loop)
├── game_state.py    # Game state extraction: MongoDB (authoritative) + game_state.json (live, 30 s window) + log fallback
├── db.py            # MongoDB queries: player_info, skills, equipment, inventory, quests, achievements
├── constants.py     # Shared config: ports, paths, TTL constants, GZIP_MIN_BYTES, sanitize(), process checks
├── static/          # Vendored static assets (e.g. hls.min.js)
└── templates/
    └── index.html   # Single-page dashboard (all tabs, JS, CSS in one file)
```

**Two data planes per agent:**

1. **HLS livestream** — Each agent runs Xvfb + `ffmpeg x11grab` (managed by
   `orchestrate.py`) writing segments to `/tmp/hls/agent_N/{stream.m3u8,
   seg_*.ts}`. The dashboard serves these under `/hls/agent_N/*`. Decoupled
   from `observe()` cadence — tiles keep streaming during 60 s+ thinking
   turns. Per-agent card thumbnails use independent hls.js instances; the
   currently-selected agent's card video is paused (its stream plays in the
   hero `<video>`) so we never double-decode the same URL on the client.

2. **WebSocket state push (:8081, permessage-deflate)** — `mcp_server.state_heartbeat`
   POSTs a **slim** projection of `window.__latestGameState`
   (`{player_stats, player_position, current_target, nearby_count, last_xp_event}`)
   to `/ingest/state` every 300 ms, and tails the active session log to
   `/ingest/activity` every 1 s. The relay rebroadcasts these as typed messages
   (`{type: state | activity | screenshot | heartbeat | restart}`).
   `ScreenshotWatcher` glob-discovers agents and sends `notify_screenshot` on
   file-mtime change as a fallback channel. The state push is the hot path —
   slimming + deflate cuts WAN bandwidth ~95% vs the prior 38 KB / 3.3 Hz dump.

**Persistent merge:** `game_state.json` (live, written by `observe()`) +
MongoDB (authoritative for quests/skills/equipment/inventory). If
`game_state.json` is stale (>30 s), falls back to DB-only, then log parsing
as last resort. The 30 s window (down from 120 s) means restarts surface in
the UI within seconds rather than holding pre-restart state for 2 minutes.

**Process model:** Python `http.server` with HTTP/1.1 keep-alive and threaded
request handling. No framework. The WebSocket relay auto-restarts on crash;
`ScreenshotWatcher` recovers from callback exceptions. Both run in the relay
thread.

**Restart UX (instant):** `POST /api/restart-run` invalidates every server
cache that could mask the new run (`_agents_cache`, `_dataset_stats_cache`,
`_sft_stats_cache`, `_eval_cache`, `_eval_live_cache`, `_ss_cache`) and then
broadcasts `{type:"restart"}` over the WS relay. Every connected tab clears
local state, drops the hero HLS, and re-fetches `/api/agents` + `/api/live`
within one RTT — no waiting for the next 8 s polling tick.

## Tabs

| Tab | Endpoint(s) | What it shows |
|-----|-------------|---------------|
| Overview | `/api/game-state`, `/api/live`, `/api/agents` | Live screenshot, quest progress, inventory, HP/level, activity summary |
| Activity | `/api/activity` (initial + on agent-switch) + WS push | Full event log — tool calls, reasoning, results (expandable). Live: WS push triggers a debounced re-fetch on each new event burst. |
| Sessions | `/api/sessions`, `/api/dataset-stats`, `/api/sft-stats` | Session history, cost/turns/duration, SFT pipeline stats |
| Prompt | `/api/prompt`, `/api/session-log` | System prompt viewer, personality grid, game knowledge, CLAUDE.md |
| Eval | `/api/eval/latest`, `/api/eval/live` | Eval comparison: r9-sft vs base, live split-screen + results (Glass's delta, action distributions) |

> The legacy "World" tab (entity table + screenshot gallery) was removed; the
> Overview tab plus the live HLS hero already give a richer real-time view.

## API Endpoints

| Endpoint | Method | Params | Returns |
|----------|--------|--------|---------|
| `/api/game-state` | GET | `?agent=N` | Merged MongoDB + live game state JSON |
| `/api/agents` | GET | — | All active agents: harness, model, personality, screenshot age, session stats |
| `/api/activity` | GET | `?agent=N` | Latest session activity feed (incremental parser; full {events, turn, cost, tokens, model} dict) |
| `/api/sessions` | GET | `?agent=N` | Past session list with cost, turns, model, duration |
| `/api/session-detail` | GET | `?name=X&log_dir=Y` | Full parsed session log (events, thinking, tool calls) |
| `/api/live` | GET | — | Mode (single/multi/none), agent count, port status, game server health |
| `/api/dataset-stats` | GET | — | Raw session count + total size |
| `/api/sft-stats` | GET | — | Extracted turns count + Qwen SFT train/val record counts |
| `/api/prompt` | GET | — | System prompt, game knowledge, personality files |
| `/api/session-log` | GET | — | session_log.md content |
| `/api/eval/latest` | GET | — | Eval comparison results (models vs base) |
| `/api/eval/live` | GET | — | Live eval sandbox status (TTL fast-path: skips fingerprint glob inside cache window) |
| `/api/raw` | GET | `?file=X` | Raw file viewer (game_state, session_log, claude_md, state_extractor, orchestrate) |
| `/api/screenshots` | GET | — | Screenshot list (50 most recent) — kept for ad-hoc tooling, not consumed by the UI |
| `/api/restart-run` | POST | body: `{hours, grinder, completionist, explorer_tinkerer, harness}` | Kicks off restart-agent.sh; invalidates server caches and WS-broadcasts `{type:"restart"}` |
| `/hls/agent_N/stream.m3u8` | GET | — | Per-agent HLS playlist served from `/tmp/hls/agent_N/` (allowlisted segments only) |
| `/hls/agent_N/seg_*.ts` | GET | — | HLS segment files |
| `/static/*` | GET | — | Vendored frontend assets (e.g. `hls.min.js`) |
| `/screenshots/agent_N/<file>` | GET | — | Latest screenshot for an agent |
| `/stream/agent_N` | GET | — | MJPEG fallback (legacy; live agents use HLS, only `/stream/eval_*` is wired) |
| `/ingest/state` | POST | `?agent=N` (body: slim state JSON) | Loopback from `mcp_server.state_heartbeat`; rebroadcast as `{type:state}` |
| `/ingest/activity` | POST | `?agent=N` (body: events list) | Loopback from `mcp_server.state_heartbeat`; rebroadcast as `{type:activity}` |
| `/report.json` | GET | — | Auto-generated export report (regenerates if >5min stale) |

## Caching Strategy

Everything is cached to avoid redundant work on the 8-vCPU VM:

| Cache | TTL | Why |
|-------|-----|-----|
| `_agents_cache` | 15 s (`AGENTS_CACHE_TTL`) | Avoids re-parsing logs + port probing on every dashboard poll. Includes `hls_age` and `hls_available`. Invalidated on restart. |
| `_ss_cache` (ss -tlnp) | 5 s | Avoids forking subprocess per request. Invalidated on restart. |
| MongoDB player state | 3 s | DB only saves on autosave/logout — more frequent queries waste cycles. |
| Session log parser | offset-tracked, persistent across calls (LRU 25 files) | Incremental tail: O(new bytes), not O(file size). On a 4 MB log, cold = ~250 ms, warm = ~0.3 ms. |
| `live_session_stats` | offset-tracked, persistent (LRU 25 files) | Same incremental pattern — used by the heavy `/api/agents` path. |
| Dataset / SFT stats | 30 s mtime-bucketed | Avoids walking `dataset/extracted/` on every poll. Invalidated on restart. |
| Eval-live | 1 s TTL fast-path; fingerprint glob only past TTL | Eval tab polls 2 s; skipping the glob inside TTL trims most of the work. |
| Eval results | mtime-keyed | Invalidated on restart. |
| HTML template | import-time | Loaded once, never re-read (restart dashboard after template edits). |
| Gzip threshold | `GZIP_MIN_BYTES` (4KB) | Small payloads skip gzip overhead. |

## Frontend Refresh Model

Two intervals + WS push:

- **`refreshFast` every 3 s** — `/api/live`, `/api/game-state`. In-place updates of mission/inventory/status/run timer. **Skipped while `document.hidden`.**
- **`refreshSlow` every 8 s** — `/api/agents` (rebuilds `agentList`), `/api/sessions`, eval-tab indicator, then `refreshActivity()`. **Skipped while `document.hidden`.**
- **`refreshActivity` (WS-driven)** — On each `{type:"activity"}` push the frontend debounces a single `/api/activity` fetch (200 ms). The 8 s steady poll for activity has been retired; `refreshSlow` only re-fetches activity as a safety net. Backfill on agent switch / initial load uses the same helper.
- **`onStateNotification`** — In-place HP/level overlay update; no DOM destruction.
- **`onRestartNotification`** — Clears `agentList`, `liveState`, feed counts; tears down hero HLS; force-runs `refreshFast()` + `refreshSlow()`.

On `visibilitychange` to visible, both refresh loops fire once immediately so a returning user doesn't see a stale snapshot.

## Management Scripts

```bash
./scripts/start-dashboard.sh       # Kill existing + start on :8080 (nohup, logs to /tmp/dashboard.log)
./scripts/stop-dashboard.sh        # Graceful stop (SIGTERM then SIGKILL)
./scripts/restart-dashboard.sh     # Stop + start (use after template/code changes)
```

The orchestrator (`orchestrate.py`) auto-starts the dashboard if not running. Manual scripts are for dev iteration.

## Editing Guide

### Changing the HTML template
1. Edit `templates/index.html`
2. Run `./scripts/restart-dashboard.sh` — template is cached at import time, not re-read at runtime

### Adding a new API endpoint
1. Add the handler method to `api.py` in the `APIMixin` class
2. Add the route to `handler.py` in `do_GET()` path dispatch
3. Use `self._send_json(data)` for JSON responses (auto-gzips >4KB, sets CORS + Content-Length headers)
4. Sanitize any user-visible text through `sanitize()` from constants.py to strip API keys

### Adding a new dashboard tab
1. Add tab button + content div in `templates/index.html`
2. Add JS fetch logic in the `<script>` section of the same file
3. Wire up API endpoint if new data is needed (see above)
4. Restart dashboard

### Adding a new WS message type
1. Add `notify_<type>(...)` to `WebSocketRelay` in `server.py` (mirror existing `notify_state/screenshot/activity/heartbeat/restart`).
2. Call it from wherever the event originates (handler, ingest, watcher).
3. Add a `case '<type>'` in the frontend `ws.onmessage` switch in `templates/index.html`.

### Parsing a new harness log format
1. Add detection logic in `parsers.py` → `parse_session_log()` (auto-detect by reading first lines)
2. Write `_init_<harness>_acc()`, `_consume_<harness>_obj(state, obj)`, `_finalize_<harness>_acc(state)` matching the Claude/Codex pattern. The `_incremental_parse` driver handles offset tracking + rotation.
3. Add tool summary mappings in `_kaetram_tool_summary()` if tool call format differs

## Gotchas

- **Template is cached at import time.** After editing `templates/index.html`, you MUST restart the dashboard. Hot reload does not exist.
- **MongoDB typo is intentional.** The database is named `kaetram_devlopment` (missing 'e') — this is how Kaetram-Open ships it. Do not "fix" it.
- **Username lookup uses metadata.json.** Each agent sandbox has `/tmp/kaetram_agent_N/metadata.json` with the actual username (supports Codex/Gemini agents that may not be "claudebotN").
- **HTTP/1.1 keep-alive requires Content-Length on every body-bearing response.** The audit is done; if you add a new response path, set `Content-Length` (or use 204) — otherwise the persistent connection stalls. The MJPEG path opts out via `Connection: close`.
- **The state heartbeat ships only a slim projection.** If you add a UI panel that needs `inventory`/`quests`/`achievements`/`nearby_entities`/ASCII map at near-real-time, fetch via `/api/game-state` (already on the 3 s refreshFast loop). Don't re-fatten the WS payload — it goes to every connected tab × 3.3 Hz.
- **Activity events go through `/api/activity`, not parsed in the frontend.** The WS `{type:"activity"}` push is a *trigger*: the frontend debounces a single fetch. Raw JSONL log lines on the wire would require duplicating the parser in JS.
- **Exit code 144 from zsh on kill is normal.** The stop script sends SIGTERM — zsh reports 128+16=144. Not an error.
- **No framework.** This is raw `http.server` + `BaseHTTPRequestHandler`. No Flask, no FastAPI, no middleware. Adding one would be overengineering for a dev-only dashboard.
- **All state is file-based or MongoDB.** No in-memory session state, no Redis. Dashboard is stateless — any instance can serve any request.
- **Sanitize before serving.** `constants.sanitize()` redacts API keys, tokens, and long secret-like strings. Always wrap user-visible text content through it. Log content, prompt content, raw files — all must be sanitized.
- **MJPEG streams block the thread.** Each `/stream/agent_N` request holds a thread for the duration of the stream. The threaded server handles this fine, but don't add synchronous work to the stream loop. (HLS is preferred for new code — MJPEG is a fallback only `/stream/eval_*` still uses for eval thumbnails.)
- **HLS segment serving is allowlisted.** `send_hls_file` only serves `stream.m3u8` and `seg_*.ts` from `/tmp/hls/agent_N/`. Adding new file types requires editing the allowlist in `handler.py`.
- **`/ingest/*` is loopback-only.** Endpoints accept POSTs from `127.0.0.1` only; the heartbeat best-effort POSTs and silently drops on failure (the dashboard is a soft dependency for the agent). 2 MB body cap (`INGEST_MAX_BYTES`).
- **No hardcoded `MAX_AGENTS` for live discovery.** Agent discovery is glob-based — adding a 4th agent doesn't require dashboard config changes. (`MAX_AGENTS` is still consulted by `/api/live` and the screenshot list.)
- **Selected-agent card video is paused.** `_bindCardVideo` early-outs when `agentId === selectedAgent` and hides that card's `<video>`. The stream plays in the hero. Don't accidentally remove the early-out — both decoders running is wasted client CPU on the user's laptop.
- **Grid rebuild destroys all `_cardHls` instances.** Before `container.innerHTML = html` we tear down every cached hls.js instance because innerHTML detaches the `<video>` elements they reference. The MutationObserver-driven `bindAllCardVideos` rebinds ~200 ms later.

## Port Reference

| Port | Service | Notes |
|------|---------|-------|
| 8080 | Dashboard HTTP | HTTP/1.1 keep-alive. UI + `/hls/agent_N/*` + `/ingest/{state,activity}` + `/static/*` |
| 8081 | Dashboard WebSocket | permessage-deflate. State, activity, screenshot, heartbeat, restart broadcast (typed messages) |
| 9000 | Kaetram game client | Shared static files (all agents) |
| 9001 + N×10 | Game server WS | Per-agent (agent 0-8); today 9001 / 9011 / 9021 |
| 9061, 9071 | Eval game servers | r9-sft / base |
| 9191 | E2E test-lane game server | db `kaetram_e2e` |
