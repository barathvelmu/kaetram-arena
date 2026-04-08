#!/usr/bin/env python3
"""
mcp_game_server.py — Custom MCP server for Kaetram game automation.

Exposes structured game tools (observe, attack, navigate, etc.) so the AI agent
calls typed functions instead of writing raw JavaScript.  Internally manages a
Playwright browser, injects state_extractor.js, and handles login.

Environment variables (set via .mcp.json env block):
    KAETRAM_PORT          — Game server WebSocket port (9001, 9011, etc.)
    KAETRAM_USERNAME      — Login username (ClaudeBot0, ClaudeBot1, etc.)
    KAETRAM_EXTRACTOR     — Absolute path to state_extractor.js
    KAETRAM_SCREENSHOT_DIR — Directory for live screenshots
    KAETRAM_CLIENT_URL    — Game client URL (default: http://localhost:9000)
"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import Context, FastMCP
from playwright.async_api import async_playwright

# All debug output to stderr (stdout reserved for MCP JSON-RPC)
import time as _time

_MCP_START = _time.time()
_MCP_TOOL_COUNTS: dict[str, int] = {}
_MCP_ERROR_COUNTS: dict[str, int] = {}
_MCP_LOG_FILE = None

def _init_log_file():
    """Open a persistent log file for MCP diagnostics."""
    global _MCP_LOG_FILE
    screenshot_dir = os.environ.get("KAETRAM_SCREENSHOT_DIR", "/tmp")
    log_path = os.path.join(screenshot_dir, "mcp_server.log")
    try:
        _MCP_LOG_FILE = open(log_path, "a")
    except OSError:
        pass

def log(msg: str):
    elapsed = _time.time() - _MCP_START
    m, s = divmod(int(elapsed), 60)
    ts = _time.strftime("%H:%M:%S")
    line = f"[{ts} +{m:02d}:{s:02d}] {msg}"
    print(line, file=sys.stderr, flush=True)
    if _MCP_LOG_FILE:
        try:
            _MCP_LOG_FILE.write(line + "\n")
            _MCP_LOG_FILE.flush()
        except OSError:
            pass

def log_tool(name: str, success: bool = True, error: str = ""):
    _MCP_TOOL_COUNTS[name] = _MCP_TOOL_COUNTS.get(name, 0) + 1
    if not success:
        _MCP_ERROR_COUNTS[name] = _MCP_ERROR_COUNTS.get(name, 0) + 1
        log(f"[tool] {name} FAILED ({_MCP_ERROR_COUNTS[name]} errors): {error[:200]}")
    elif _MCP_TOOL_COUNTS[name] <= 3 or _MCP_TOOL_COUNTS[name] % 25 == 0:
        # Log first 3 calls of each tool + every 25th as heartbeat
        log(f"[tool] {name} #{_MCP_TOOL_COUNTS[name]}")
    # Periodic stats dump every 50 total calls
    total = sum(_MCP_TOOL_COUNTS.values())
    if total % 50 == 0:
        log_stats()

def log_stats():
    total = sum(_MCP_TOOL_COUNTS.values())
    errors = sum(_MCP_ERROR_COUNTS.values())
    top5 = sorted(_MCP_TOOL_COUNTS.items(), key=lambda x: -x[1])[:5]
    top5_str = ", ".join(f"{k}={v}" for k, v in top5)
    err_str = ""
    if _MCP_ERROR_COUNTS:
        err_detail = ", ".join(f"{k}={v}" for k, v in sorted(_MCP_ERROR_COUNTS.items(), key=lambda x: -x[1]))
        err_str = f" | errors: {err_detail}"
    log(f"[stats] {total} total calls, {errors} errors | top: {top5_str}{err_str}")

# Initialize log file on import
_init_log_file()


# ── Browser lifespan (lazy — yields immediately, launches browser on first use) ─

@asynccontextmanager
async def game_lifespan(server: FastMCP):
    """Yield immediately so MCP handshake completes fast. Browser launches lazily."""
    state = {
        "page": None, "browser": None, "pw": None,
        "logged_in": False, "_lock": asyncio.Lock(),
    }
    log("[mcp] Server ready (browser will launch on first tool call)")
    try:
        yield state
    finally:
        log_stats()
        if state["browser"]:
            log("[mcp] Shutting down browser")
            await state["browser"].close()
        if state["pw"]:
            await state["pw"].stop()
        log("[mcp] Server shutdown complete")


async def _ensure_browser(state: dict):
    """Launch browser if not yet started. Thread-safe via asyncio.Lock."""
    if state["page"] is not None:
        return state["page"]

    async with state["_lock"]:
        # Double-check after acquiring lock
        if state["page"] is not None:
            return state["page"]

        log("[mcp] Launching browser...")
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 720})

        # Inject state_extractor.js (survives page reloads/navigation)
        extractor_path = os.environ.get("KAETRAM_EXTRACTOR", "state_extractor.js")
        if os.path.exists(extractor_path):
            await context.add_init_script(path=extractor_path)
            log(f"[mcp] Injected {extractor_path}")

        # WebSocket port override for multi-agent isolation
        port = os.environ.get("KAETRAM_PORT", "")
        if port:
            await context.add_init_script(f"""(() => {{
                const PORT = '{port}';
                const _WS = window.WebSocket;
                window.WebSocket = function(url, protocols) {{
                    url = url.replace(/\\/\\/[^:/]+/, '//localhost');
                    url = url.replace(/:9001(?=\\/|$)/, ':' + PORT);
                    return protocols ? new _WS(url, protocols) : new _WS(url);
                }};
                window.WebSocket.prototype = _WS.prototype;
                window.WebSocket.CONNECTING = 0; window.WebSocket.OPEN = 1;
                window.WebSocket.CLOSING = 2; window.WebSocket.CLOSED = 3;
            }})()""")
            log(f"[mcp] WebSocket port override: {port}")

        page = await context.new_page()

        # Live screenshot hook (dashboard reads these)
        screenshot_dir = os.environ.get("KAETRAM_SCREENSHOT_DIR", "/tmp")
        os.makedirs(screenshot_dir, exist_ok=True)
        screenshot_path = os.path.join(screenshot_dir, "live_screen.png")

        async def on_console(msg):
            if msg.text == "LIVE_SCREENSHOT_TRIGGER":
                try:
                    await page.screenshot(path=screenshot_path, type="png")
                except Exception:
                    pass

        page.on("console", on_console)

        # Log page crashes and WebSocket closures
        page.on("crash", lambda: log("[mcp] PAGE CRASHED — browser tab died"))
        page.on("close", lambda: log("[mcp] PAGE CLOSED — browser tab was closed"))

        state["page"] = page
        state["browser"] = browser
        state["pw"] = pw
        log("[mcp] Browser ready")
        return page


# ── Server ────────────────────────────────────────────────────────────────────

mcp = FastMCP("kaetram", lifespan=game_lifespan)


async def _page(ctx: Context):
    """Get the Playwright page, launching browser if needed."""
    state = ctx.request_context.lifespan_context
    return await _ensure_browser(state)


# ── Login ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def login(ctx: Context) -> str:
    """Log into Kaetram. Call this FIRST before any other tool."""
    page = await _page(ctx)
    username = os.environ.get("KAETRAM_USERNAME", "ClaudeBot")
    client_url = os.environ.get("KAETRAM_CLIENT_URL", "http://localhost:9000")

    await page.goto(client_url)
    await page.wait_for_timeout(3000)
    await page.locator("#login-name-input").fill(username)
    await page.locator("#login-password-input").fill("password123")
    await page.locator("#login").click()

    # Wait for game to load — body.className transitions from 'intro' to 'game'
    # Login takes ~4-6s for server to respond and menu to fade out.
    game_ready = False
    for _attempt in range(12):
        await page.wait_for_timeout(1000)
        result = await page.evaluate("""() => {
            if (document.body.className === 'game') return 'in_game';
            // Still on intro — check if we need to register (no account yet)
            const lc = document.getElementById('load-character');
            if (lc && window.getComputedStyle(lc).opacity !== '0') return 'needs_login';
            return 'waiting';
        }""")
        log(f"[mcp] login attempt {_attempt+1}: {result}")
        if result == 'in_game':
            game_ready = True
            break
        if result == 'needs_login' and _attempt >= 5:
            # After 5s, still on login screen — account doesn't exist, register
            log(f"[mcp] Registering new account for {username}")
            await page.evaluate("""(username) => {
                document.getElementById('new-account').click();
                setTimeout(() => {
                    const set = (el, val) => {
                        if (!el) return;
                        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
                            .set.call(el, val);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                    };
                    set(document.getElementById('register-name-input'), username);
                    set(document.getElementById('register-password-input'), 'password123');
                    set(document.getElementById('register-password-confirmation-input'), 'password123');
                    set(document.getElementById('register-email-input'), username + '@test.com');
                    setTimeout(() => document.getElementById('play').click(), 500);
                }, 500);
            }""", username)
            # Wait for registration to complete
            for _r in range(10):
                await page.wait_for_timeout(1000)
                r2 = await page.evaluate("() => document.body.className")
                if r2 == 'game':
                    game_ready = True
                    break
            break

    if not game_ready:
        log(f"[mcp] Login failed for {username} — game did not load")
        log_tool("login", success=False, error="game did not load")
        return "Login FAILED — game did not load. The game client may not be connected to the server. Try login() again."

    # Dismiss any post-login modals (welcome screen, notifications)
    await page.wait_for_timeout(1000)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    warped = False

    ctx.request_context.lifespan_context["logged_in"] = True
    log(f"[mcp] Logged in as {username}")
    log_tool("login")
    msg = f"Logged in as {username}"
    if warped:
        msg += " (auto-warped to Mudwich)"
    return msg


# ── Observe ───────────────────────────────────────────────────────────────────

@mcp.tool()
async def observe(ctx: Context) -> str:
    """Observe the current game state.

    Returns game state JSON + ASCII map + stuck check. Call this before every
    decision and after every action.  Always returns the full, consistent state.
    """
    log_tool("observe")
    page = await _page(ctx)

    # Take screenshot for dashboard
    screenshot_dir = os.environ.get("KAETRAM_SCREENSHOT_DIR", "/tmp")
    try:
        await page.screenshot(
            path=os.path.join(screenshot_dir, "live_screen.png"), type="png"
        )
    except Exception:
        pass

    result = await page.evaluate("""() => {
        if (typeof window.__extractGameState !== 'function') {
            return 'ERROR: State extractor not loaded. Call login() first.';
        }
        // Always extract FRESH state — never use stale cache
        const gs = window.__extractGameState();
        const am = window.__generateAsciiMap();
        const sc = window.__stuckCheck ? window.__stuckCheck() : {};

        // Check freshness — warn if game object seems stale
        const age_ms = gs.timestamp ? (Date.now() / 1000 - gs.timestamp) * 1000 : 0;
        if (gs.error) {
            return 'ERROR: ' + gs.error + ' (game may not be loaded — try login() again)';
        }

        const asciiText = (am && !am.error) ? (am.ascii + '\\n\\n' + am.legendText) : '';
        const ps = gs.player_stats || {};
        const ents = gs.nearby_entities || [];
        const quests = gs.quests || [];
        const digest = {
            hp_pct: ps.max_hp ? Math.round(100 * ps.hp / ps.max_hp) : 0,
            threats: ents.filter(e => e.type === 3 && e.distance <= 3).length,
            nearest_mob: (ents.find(e => e.type === 3 && e.hp > 0) || {}).name || null,
            quest_active: quests.some(q => q.started && !q.finished),
            quest_npc_near: ents.some(e => e.quest_npc && e.distance <= 10),
            stuck: sc.stuck || false,
            nav_status: (gs.navigation || {}).status || 'idle',
        };
        return JSON.stringify(gs) + '\\n\\nASCII_MAP:\\n' + asciiText
               + '\\n\\nDIGEST:\\n' + JSON.stringify(digest)
               + '\\n\\nSTUCK_CHECK:\\n' + JSON.stringify(sc);
    }""")

    # Write game_state.json for dashboard (live state, no log parsing needed)
    try:
        gs_json = result.split("\n\nASCII_MAP:")[0] if "\n\nASCII_MAP:" in result else result
        if not gs_json.startswith("ERROR"):
            gs_path = os.path.join(screenshot_dir, "game_state.json")
            with open(gs_path, "w") as f:
                f.write(gs_json)
    except Exception:
        pass

    # Write game_state.json for dashboard (live state, no log parsing needed)
    try:
        gs_json = result.split("\n\nASCII_MAP:")[0] if "\n\nASCII_MAP:" in result else result
        if not gs_json.startswith("ERROR"):
            gs_path = os.path.join(screenshot_dir, "game_state.json")
            with open(gs_path, "w") as f:
                f.write(gs_json)
    except Exception:
        pass

    return result


# ── Combat ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def attack(ctx: Context, mob_name: str) -> str:
    """Attack the nearest alive mob matching the given name.

    Args:
        mob_name: Name of mob to attack (e.g. 'Rat', 'Snek', 'Goblin')
    """
    log_tool("attack")
    page = await _page(ctx)

    # Snapshot mob HP before attacking
    hp_before = await page.evaluate("""(name) => {
        const g = window.game;
        if (!g || !g.player) return null;
        const nl = name.toLowerCase();
        for (const e of Object.values(g.entities.entities || {})) {
            if (e.type === 3 && (e.hitPoints || 0) > 0 &&
                (e.name || '').toLowerCase().includes(nl))
                return e.hitPoints;
        }
        return null;
    }""", mob_name)

    result = await page.evaluate(
        "(name) => JSON.stringify(window.__attackMob(name))", mob_name
    )
    await page.wait_for_timeout(2500)

    # Post-attack state: check if mob died, damage dealt, player HP
    post = await page.evaluate("""() => {
        const p = window.game && window.game.player;
        if (!p) return {};
        const t = p.target;
        return {
            killed: !t || (t.hitPoints !== undefined && t.hitPoints <= 0),
            mob_hp: t ? (t.hitPoints || 0) : 0,
            mob_name: t ? (t.name || '') : null,
            player_hp: p.hitPoints || 0,
            player_max_hp: p.maxHitPoints || 0,
        };
    }""")
    # Add damage tracking
    if isinstance(post, dict) and hp_before is not None:
        post["hp_before"] = hp_before
        hp_after = post.get("mob_hp", 0)
        post["damage_dealt"] = max(0, hp_before - hp_after)
        if post["damage_dealt"] == 0 and not post.get("killed"):
            post["note"] = "Attack landed but game tick has not updated HP yet. Keep attacking — do not move."

    # Auto-loot on kill: scan for nearby items and walk to them
    auto_looted = {}
    if isinstance(post, dict) and post.get("killed"):
        await page.wait_for_timeout(500)  # brief delay for drop to spawn
        auto_looted = await page.evaluate("""() => {
            const game = window.game;
            if (!game || !game.player) return {};
            const player = game.player;
            const allEnts = game.entities.entities || {};
            // Snapshot inventory before
            let invBefore = {};
            try {
                const inv = game.menu.getInventory();
                if (inv && inv.getElement) {
                    for (let i = 0; i < 25; i++) {
                        const el = inv.getElement(i);
                        if (!el || !el.dataset?.key || inv.isEmpty(el)) continue;
                        const k = el.dataset.key;
                        invBefore[k] = (invBefore[k] || 0) + (el.count || parseInt(el.dataset?.count || '0') || 1);
                    }
                }
            } catch(e) {}
            // Find nearest lootable
            let nearest = null;
            let minDist = 999;
            for (const [inst, ent] of Object.entries(allEnts)) {
                if (ent.type !== 2 && ent.type !== 8) continue;
                const dist = Math.abs(ent.gridX - player.gridX) + Math.abs(ent.gridY - player.gridY);
                if (dist < minDist && dist <= 10) {
                    minDist = dist;
                    nearest = { instance: inst, type: ent.type, name: ent.name || 'Unknown', x: ent.gridX, y: ent.gridY, distance: dist };
                }
            }
            if (!nearest) return { no_drops: true };
            // Click to walk to item
            const coords = window.__tileToScreenCoords(nearest.x, nearest.y);
            if (coords && !coords.error) {
                player.disableAction = false;
                document.getElementById('canvas').dispatchEvent(new MouseEvent('click', {
                    clientX: coords.click_x, clientY: coords.click_y, bubbles: true
                }));
            }
            return { targeting: nearest, inv_before: invBefore };
        }""")

        if isinstance(auto_looted, dict) and auto_looted.get("targeting"):
            # Wait for walk + auto-pickup (scale with distance)
            dist = auto_looted["targeting"].get("distance", 3)
            wait_ms = min(max(1500, dist * 300), 5000)
            await page.wait_for_timeout(wait_ms)

            # Handle lootbag
            if auto_looted["targeting"].get("type") == 8:
                await page.evaluate("""() => {
                    const game = window.game;
                    if (!game || !game.socket) return;
                    for (let i = 0; i < 10; i++) {
                        try { game.socket.send(58, { opcode: 1, index: i }); } catch(e) {}
                    }
                }""")
                await page.wait_for_timeout(500)

            # Diff inventory
            inv_after = await page.evaluate("""() => {
                try {
                    const inv = window.game.menu.getInventory();
                    const items = {};
                    if (inv && inv.getElement) {
                        for (let i = 0; i < 25; i++) {
                            const el = inv.getElement(i);
                            if (!el || !el.dataset?.key || inv.isEmpty(el)) continue;
                            const k = el.dataset.key;
                            items[k] = (items[k] || 0) + (el.count || parseInt(el.dataset?.count || '0') || 1);
                        }
                    }
                    return items;
                } catch(e) { return {}; }
            }""")
            inv_before = auto_looted.get("inv_before", {})
            gained = {}
            for k, v in inv_after.items():
                diff = v - inv_before.get(k, 0)
                if diff > 0:
                    gained[k] = diff
            auto_looted = {"looted": gained if gained else "none", "target": auto_looted["targeting"].get("name", "?")}

    # Merge post-attack state into result
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
        if isinstance(parsed, dict):
            parsed["post_attack"] = post
            if auto_looted and not auto_looted.get("no_drops"):
                parsed["auto_loot"] = auto_looted
            return json.dumps(parsed)
    except Exception:
        pass
    return result


@mcp.tool()
async def set_attack_style(ctx: Context, style: str = "hack") -> str:
    """Set combat attack style.

    Args:
        style: 'hack' (strength+defense), 'chop' (strength), or 'defensive' (defense)
    """
    style_ids = {"hack": 6, "chop": 7, "defensive": 3}
    sid = style_ids.get(style.lower(), 6)
    page = await _page(ctx)
    await page.evaluate(f"() => window.game.player.setAttackStyle({sid})")
    return f"Set attack style to {style} (id={sid})"


# ── Navigation ────────────────────────────────────────────────────────────────

@mcp.tool()
async def navigate(ctx: Context, x: int, y: int) -> str:
    """Navigate to grid coordinates using BFS pathfinding.

    Auto-advances waypoints in background. Call observe() to check navigation.status.
    For distances > 100 tiles, warp to nearest town first.

    Args:
        x: Target grid X coordinate
        y: Target grid Y coordinate
    """
    log_tool("navigate")
    page = await _page(ctx)
    result = await page.evaluate(
        "([x,y]) => JSON.stringify(window.__navigateTo(x, y))", [x, y]
    )
    await page.wait_for_timeout(4000)

    # Warn if BFS failed and linear fallback is being used
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
        if isinstance(parsed, dict) and parsed.get("pathfinding") == "linear_fallback":
            parsed["warning"] = (
                "BFS pathfinding failed — using approximate straight-line route. "
                "High chance of getting stuck on walls. Consider warping closer first, "
                "or navigating in shorter hops (< 80 tiles)."
            )
            return json.dumps(parsed)
    except Exception:
        pass
    return result


@mcp.tool()
async def move(ctx: Context, x: int, y: int) -> str:
    """Move to a nearby tile (< 15 tiles). For longer distances use navigate().

    Args:
        x: Target grid X
        y: Target grid Y
    """
    page = await _page(ctx)
    result = await page.evaluate(
        "([x,y]) => JSON.stringify(window.__moveTo(x, y))", [x, y]
    )
    await page.wait_for_timeout(2000)
    return result


@mcp.tool()
async def warp(ctx: Context, location: str = "mudwich") -> str:
    """Fast travel to a town. Auto-waits up to 25s if combat cooldown is active.

    Args:
        location: 'mudwich', 'aynor', 'lakesworld', 'patsow', 'crullfield', or 'undersea'
    """
    log_tool("warp")
    warp_ids = {"mudwich": 0, "aynor": 1, "lakesworld": 2, "patsow": 3, "crullfield": 4, "undersea": 5}
    warp_id = warp_ids.get(location.lower(), 0)
    page = await _page(ctx)

    # Clear combat state + zero the cooldown timer so incoming hits don't keep resetting it
    await page.evaluate("""() => {
        window.__clearCombatState();
        window.__kaetramState.lastCombatTime = 0;
    }""")

    # Poll until cooldown expires (max ~25s) instead of failing immediately.
    # Handles: cooldown_remaining_seconds, has_target, and attackers cases.
    max_attempts = 6  # 6 attempts * ~5s sleep = 30s max wait
    for attempt in range(max_attempts):
        result_raw = await page.evaluate(
            "(id) => JSON.stringify(window.__safeWarp(id))", warp_id
        )
        result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
        is_combat_block = isinstance(result, dict) and (
            result.get("cooldown_remaining_seconds")
            or result.get("has_target")
            or result.get("attackers")
        )
        if is_combat_block:
            wait_secs = result.get("cooldown_remaining_seconds", 5)
            wait_ms = min(wait_secs * 1000 + 1000, 6000)
            await page.wait_for_timeout(wait_ms)
            # Re-clear combat + timer in case mobs re-engaged during wait
            await page.evaluate("""() => {
                window.__clearCombatState();
                window.__kaetramState.lastCombatTime = 0;
            }""")
            continue
        # Success or non-combat error — return immediately
        break

    await page.wait_for_timeout(3000)
    return result_raw


@mcp.tool()
async def cancel_nav(ctx: Context) -> str:
    """Cancel active navigation."""
    page = await _page(ctx)
    await page.evaluate("() => window.__navCancel()")
    return "Navigation cancelled"


# ── NPC / Quests ──────────────────────────────────────────────────────────────

@mcp.tool()
async def interact_npc(ctx: Context, npc_name: str) -> str:
    """Walk to an NPC, talk through ALL dialogue lines, and auto-accept quest if offered.

    This handles the full NPC interaction flow:
    1. Walk to NPC if not adjacent (targets orthogonal neighbor tile)
    2. Verify adjacency (Manhattan distance < 2, server requirement)
    3. Send talk packets repeatedly (NPCs have 1-10+ dialogue lines)
    4. Click quest-button if quest panel opens

    Args:
        npc_name: Name of the NPC (e.g. 'Forester', 'Blacksmith', 'Village Girl')
    """
    log_tool("interact_npc")
    page = await _page(ctx)

    # Snapshot quests BEFORE any interaction (to detect changes later)
    quests_before = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )

    # Step 1: Walk to NPC and get initial talk result
    result_raw = await page.evaluate(
        "(name) => JSON.stringify(window.__interactNPC(name))", npc_name
    )
    result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw

    if isinstance(result, dict) and result.get("error"):
        return result_raw

    instance_id = result.get("instance", "") if isinstance(result, dict) else ""
    talked = result.get("talked", False) if isinstance(result, dict) else False
    npc_pos = result.get("npc_pos", {}) if isinstance(result, dict) else {}
    player_start = result.get("player_pos", {}) if isinstance(result, dict) else {}

    # Step 2: If not adjacent, wait for walk + verify arrival
    if not talked:
        # Wait for pathfinding walk (check every 1s, up to 8s)
        arrived = False
        for wait_i in range(8):
            await page.wait_for_timeout(1000)
            pos_check = await page.evaluate("""(npcPos) => {
                const p = window.game && window.game.player;
                if (!p) return { px: 0, py: 0, manhattan: 999 };
                const manhattan = Math.abs(p.gridX - npcPos.x) + Math.abs(p.gridY - npcPos.y);
                return { px: p.gridX, py: p.gridY, manhattan: manhattan };
            }""", npc_pos)
            if pos_check.get("manhattan", 999) < 2:
                arrived = True
                break
        if not arrived:
            # Player never reached the NPC — return clear error
            final_pos = pos_check or {}
            return json.dumps({
                "npc": npc_name,
                "error": f"Could not reach {npc_name} — pathfinding failed or NPC too far",
                "instance": instance_id,
                "walked": True,
                "arrived": False,
                "player_start": player_start,
                "player_end": {"x": final_pos.get("px"), "y": final_pos.get("py")},
                "npc_pos": npc_pos,
                "final_distance": final_pos.get("manhattan", -1),
                "dialogue_lines": 0,
                "quest_opened": False,
                "hint": "NPC is unreachable from current position. Try warping closer or finding a different path.",
            })

    # Step 3: Click through all dialogue lines
    # Player is now adjacent — send talk packets and collect dialogue
    quest_opened = False
    dialogue_lines = []
    empty_count = 0
    for i in range(20):
        # Send talk packet using the JS helper (includes proper coordinates)
        await page.evaluate(
            "(id) => window.__talkToNPC(id)", instance_id
        )
        # Short wait for server response + bubble render
        await page.wait_for_timeout(800)

        # Check for dialogue bubble, quest panel, and chat messages
        panel_state = await page.evaluate("""() => {
            // Check speech bubbles
            const bubbles = document.querySelectorAll('.bubble');
            let bubbleText = null;
            for (const b of bubbles) {
                const t = b.textContent.trim();
                if (t) { bubbleText = t.slice(0, 200); break; }
            }
            // Check quest panel visibility
            const questBtn = document.getElementById('quest-button');
            const questPanel = document.getElementById('quest');
            let panelVisible = false;
            let questBtnText = null;
            if (questPanel) {
                const s = window.getComputedStyle(questPanel);
                panelVisible = s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            }
            if (questBtn) questBtnText = questBtn.textContent.trim().slice(0, 50);
            // Check recent chat for NPC speech (fallback if bubble missed)
            let recentChat = null;
            const chatLog = (window.__kaetramState || {}).chatLog || [];
            if (chatLog.length > 0) {
                const last = chatLog[chatLog.length - 1];
                if (last && (Date.now() / 1000 - (last.time || 0)) < 3) {
                    recentChat = last.text;
                }
            }
            return {
                bubble_text: bubbleText,
                chat_text: recentChat,
                quest_panel: panelVisible || !!(questBtn && questBtn.offsetParent),
                quest_btn_text: questBtnText,
            };
        }""")

        dialogue_text = panel_state.get("bubble_text") or panel_state.get("chat_text")
        if dialogue_text:
            # Avoid duplicate consecutive lines
            if not dialogue_lines or dialogue_lines[-1] != dialogue_text:
                dialogue_lines.append(dialogue_text)
            empty_count = 0
        else:
            empty_count += 1

        if panel_state.get("quest_panel"):
            quest_opened = True
            btn_text = panel_state.get("quest_btn_text", "")
            dialogue_lines.append(f"[Quest panel opened: {btn_text}]")
            # Click accept/complete button
            await page.evaluate(
                "() => { const btn = document.getElementById('quest-button'); if (btn) btn.click(); }"
            )
            await page.wait_for_timeout(500)
            break

        # Stop after 4 consecutive empty responses (dialogue exhausted)
        if empty_count >= 4 and i >= 3:
            break

    # Get final player position
    player_end = await page.evaluate("""() => {
        const p = window.game && window.game.player;
        return p ? { x: p.gridX, y: p.gridY } : {};
    }""")

    # Final check: did quests change even if we didn't see the panel?
    quests_after = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )
    quest_changed = quests_before != quests_after

    return json.dumps({
        "npc": npc_name,
        "instance": instance_id,
        "walked": not talked,
        "arrived": True,
        "player_start": player_start,
        "player_end": player_end,
        "npc_pos": npc_pos,
        "dialogue_lines": len(dialogue_lines),
        "dialogue": dialogue_lines,
        "quest_opened": quest_opened or quest_changed,
        "quest_accepted": quest_opened or quest_changed,
        "last_dialogue": dialogue_lines[-1] if dialogue_lines else None,
    })


@mcp.tool()
async def talk_npc(ctx: Context, instance_id: str) -> str:
    """Click through ALL remaining NPC dialogue lines until quest panel opens or dialogue ends.

    Player must be adjacent (Manhattan distance < 2) to the NPC.
    Auto-accepts quest if quest panel opens.

    Args:
        instance_id: NPC instance ID from game state (e.g. '1-33362128')
    """
    page = await _page(ctx)

    # Verify player is adjacent before sending any packets
    adjacency = await page.evaluate("""(id) => {
        const game = window.game;
        if (!game || !game.player) return { error: 'Game not loaded' };
        const entity = game.entities && game.entities.get ? game.entities.get(id) : null;
        if (!entity && game.entities && game.entities.entities) {
            // Fallback: search entities dict directly
            for (const inst in game.entities.entities) {
                if (inst === id) { entity = game.entities.entities[inst]; break; }
            }
        }
        if (!entity) return { error: 'NPC not found with instance ' + id };
        const p = game.player;
        const manhattan = Math.abs(p.gridX - entity.gridX) + Math.abs(p.gridY - entity.gridY);
        return {
            npc_name: entity.name || 'Unknown',
            npc_pos: { x: entity.gridX, y: entity.gridY },
            player_pos: { x: p.gridX, y: p.gridY },
            manhattan: manhattan,
            adjacent: manhattan < 2,
        };
    }""", instance_id)

    if isinstance(adjacency, dict) and adjacency.get("error"):
        return json.dumps(adjacency)

    if not adjacency.get("adjacent"):
        return json.dumps({
            "instance": instance_id,
            "error": f"Not adjacent to NPC (distance={adjacency.get('manhattan')}). Walk closer first.",
            "npc_name": adjacency.get("npc_name"),
            "npc_pos": adjacency.get("npc_pos"),
            "player_pos": adjacency.get("player_pos"),
            "dialogue_lines": 0,
            "quest_opened": False,
        })

    quests_before = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )

    quest_opened = False
    dialogue_lines = []
    empty_count = 0
    for i in range(20):
        await page.evaluate("(id) => window.__talkToNPC(id)", instance_id)
        await page.wait_for_timeout(800)

        panel_state = await page.evaluate("""() => {
            const bubbles = document.querySelectorAll('.bubble');
            let bubbleText = null;
            for (const b of bubbles) {
                const t = b.textContent.trim();
                if (t) { bubbleText = t.slice(0, 200); break; }
            }
            const questBtn = document.getElementById('quest-button');
            const questPanel = document.getElementById('quest');
            let panelVisible = false;
            let questBtnText = null;
            if (questPanel) {
                const s = window.getComputedStyle(questPanel);
                panelVisible = s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            }
            if (questBtn) questBtnText = questBtn.textContent.trim().slice(0, 50);
            let recentChat = null;
            const chatLog = (window.__kaetramState || {}).chatLog || [];
            if (chatLog.length > 0) {
                const last = chatLog[chatLog.length - 1];
                if (last && (Date.now() / 1000 - (last.time || 0)) < 3) {
                    recentChat = last.text;
                }
            }
            return {
                bubble_text: bubbleText,
                chat_text: recentChat,
                quest_panel: panelVisible || !!(questBtn && questBtn.offsetParent),
                quest_btn_text: questBtnText,
            };
        }""")

        dialogue_text = panel_state.get("bubble_text") or panel_state.get("chat_text")
        if dialogue_text:
            if not dialogue_lines or dialogue_lines[-1] != dialogue_text:
                dialogue_lines.append(dialogue_text)
            empty_count = 0
        else:
            empty_count += 1

        if panel_state.get("quest_panel"):
            quest_opened = True
            btn_text = panel_state.get("quest_btn_text", "")
            dialogue_lines.append(f"[Quest panel opened: {btn_text}]")
            await page.evaluate(
                "() => { const btn = document.getElementById('quest-button'); if (btn) btn.click(); }"
            )
            await page.wait_for_timeout(500)
            break

        if empty_count >= 4 and i >= 3:
            break

    quests_after = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )
    quest_changed = quests_before != quests_after

    return json.dumps({
        "instance": instance_id,
        "npc_name": adjacency.get("npc_name"),
        "dialogue_lines": len(dialogue_lines),
        "dialogue": dialogue_lines,
        "quest_opened": quest_opened or quest_changed,
        "quest_accepted": quest_opened or quest_changed,
        "last_dialogue": dialogue_lines[-1] if dialogue_lines else None,
    })


@mcp.tool()
async def accept_quest(ctx: Context) -> str:
    """Accept the quest shown in the quest panel. Usually not needed — interact_npc auto-accepts."""
    page = await _page(ctx)
    await page.evaluate(
        "() => { const btn = document.getElementById('quest-button'); if (btn) btn.click(); }"
    )
    await page.wait_for_timeout(1500)
    return "Quest accept clicked"


# ── Shop ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def buy_item(ctx: Context, npc_name: str, item_index: int, count: int = 1) -> str:
    """Buy an item from an NPC's shop. Must be adjacent to the NPC.

    First interact with the store NPC to open the shop, then purchase by item index.
    Use observe() to see nearby NPCs. Item indices start at 0 (first item in shop).

    Known shops:
      Forester: 0=Bronze Axe(1000g), 1=Iron Axe(5000g)
      Miner: 0=Coal(50g), 1=Copper Ore(150g), 2=Tin Ore(150g), 3=Bronze Ore(200g), 4=Gold Ore(500g)
      Babushka: 0=Blue Lily, 1=Tomato, 2-3=Mushrooms, 4=Egg, 5=Corn, 6=Raw Pork, 7=Raw Chicken
      Clerk (startshop): 0=Arrow(5g), 1=Knife(500g), 2=Flask(100g), 3=Mana Flask(85g), 4=Burger(450g)

    Args:
        npc_name: Store NPC name (e.g. 'Forester', 'Miner', 'Babushka', 'Clerk')
        item_index: Index of item in the shop (0-based)
        count: Number to buy (default 1)
    """
    log_tool("buy_item")
    page = await _page(ctx)

    # Snapshot gold + inventory before
    before = await page.evaluate("""() => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        if (!inv) return { error: 'Inventory not loaded' };
        const items = {};
        let gold = 0;
        for (let i = 0; i < 25; i++) {
            const el = inv.getElement(i);
            if (!el || !el.dataset?.key || inv.isEmpty(el)) continue;
            const k = el.dataset.key;
            const c = el.count || parseInt(el.dataset?.count || '0') || 1;
            items[k] = (items[k] || 0) + c;
            if (k === 'gold') gold += c;
        }
        return { items, gold };
    }""")

    if isinstance(before, dict) and before.get("error"):
        return json.dumps(before)

    # Step 1: Walk to + talk to NPC to trigger store open
    walk_result = await page.evaluate(
        "(name) => JSON.stringify(window.__interactNPC(name))", npc_name
    )
    walk = json.loads(walk_result) if isinstance(walk_result, str) else walk_result

    if isinstance(walk, dict) and walk.get("error"):
        return json.dumps({"error": f"Cannot find NPC '{npc_name}': {walk.get('error')}"})

    npc_pos = walk.get("npc_pos", {}) if isinstance(walk, dict) else {}

    # Wait for walk to NPC
    for wait_i in range(8):
        await page.wait_for_timeout(1000)
        pos = await page.evaluate("""(npcPos) => {
            const p = window.game && window.game.player;
            if (!p) return { manhattan: 999 };
            return { manhattan: Math.abs(p.gridX - npcPos.x) + Math.abs(p.gridY - npcPos.y) };
        }""", npc_pos)
        if pos.get("manhattan", 999) < 2:
            break

    # Step 2: Send NPC talk packet to trigger store open server-side
    instance_id = walk.get("instance", "") if isinstance(walk, dict) else ""
    if instance_id:
        await page.evaluate("(id) => window.__talkToNPC(id)", instance_id)
        await page.wait_for_timeout(1500)

    # Step 3: Get the store key from the open store
    store_key = await page.evaluate("""() => {
        const game = window.game;
        if (!game || !game.player) return null;
        return game.player.storeOpen || null;
    }""")

    if not store_key:
        return json.dumps({
            "error": f"No shop opened after talking to {npc_name}. Make sure you're adjacent and the NPC has a store.",
            "npc": npc_name,
        })

    # Step 4: Send buy packet — Packets.Store=42, Opcodes.Store.Buy=2
    buy_result = await page.evaluate("""([key, index, count]) => {
        try {
            window.game.socket.send(42, {
                opcode: 2,
                key: key,
                index: index,
                count: count
            });
            return { sent: true };
        } catch(e) {
            return { error: 'Failed to send buy packet: ' + e.message };
        }
    }""", [store_key, item_index, count])

    if isinstance(buy_result, dict) and buy_result.get("error"):
        return json.dumps(buy_result)

    await page.wait_for_timeout(1500)

    # Step 5: Diff inventory to confirm purchase
    after = await page.evaluate("""() => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        if (!inv) return { items: {}, gold: 0 };
        const items = {};
        let gold = 0;
        for (let i = 0; i < 25; i++) {
            const el = inv.getElement(i);
            if (!el || !el.dataset?.key || inv.isEmpty(el)) continue;
            const k = el.dataset.key;
            const c = el.count || parseInt(el.dataset?.count || '0') || 1;
            items[k] = (items[k] || 0) + c;
            if (k === 'gold') gold += c;
        }
        return { items, gold };
    }""")

    before_items = before.get("items", {}) if isinstance(before, dict) else {}
    after_items = after.get("items", {}) if isinstance(after, dict) else {}

    gained = {}
    spent = 0
    for k, v in after_items.items():
        diff = v - before_items.get(k, 0)
        if diff > 0 and k != "gold":
            gained[k] = diff
    gold_before = before.get("gold", 0) if isinstance(before, dict) else 0
    gold_after = after.get("gold", 0) if isinstance(after, dict) else 0
    spent = gold_before - gold_after

    if gained:
        return json.dumps({
            "bought": True, "store": store_key, "items_gained": gained,
            "gold_spent": spent, "gold_remaining": gold_after,
        })
    else:
        return json.dumps({
            "bought": False, "store": store_key, "item_index": item_index,
            "error": "Purchase may have failed — no new items in inventory. Check: enough gold? inventory full? valid item index?",
            "gold_before": gold_before, "gold_after": gold_after,
        })


# ── Inventory ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def eat_food(ctx: Context, slot: int) -> str:
    """Eat food from inventory to heal HP.

    Args:
        slot: Inventory slot number (0-24)
    """
    page = await _page(ctx)
    result = await page.evaluate(
        "(s) => JSON.stringify(window.__eatFood(s))", slot
    )
    await page.wait_for_timeout(1000)
    return result


@mcp.tool()
async def drop_item(ctx: Context, slot: int) -> str:
    """Drop an item from inventory to free space.

    Args:
        slot: Inventory slot number (0-24)
    """
    page = await _page(ctx)

    # Get item info and inventory count before drop
    before = await page.evaluate("""(idx) => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        if (!inv) return { error: 'Inventory not loaded' };
        const el = inv.getElement(idx);
        if (!el) return { error: 'No item in slot ' + idx };
        const key = (el.dataset && el.dataset.key) || 'unknown';
        let count = 0;
        for (let i = 0; i < 25; i++) {
            const e = inv.getElement(i);
            if (e && e.dataset?.key && !inv.isEmpty(e)) count++;
        }
        return { key: key, count: count };
    }""", slot)

    if isinstance(before, dict) and before.get("error"):
        return json.dumps(before)

    # Send container remove packet: Packets.Container=16, Opcodes.Container.Remove=2
    # The slot index tells the server which item to drop
    result = await page.evaluate("""(idx) => {
        try {
            // Method 1: Direct packet (most reliable)
            window.game.socket.send(16, [2, idx, 1]);
            return { sent: true };
        } catch(e) {
            return { error: 'Failed to send drop packet: ' + e.message };
        }
    }""", slot)

    await page.wait_for_timeout(1000)

    # Verify item was dropped
    after = await page.evaluate("""() => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        if (!inv) return -1;
        let count = 0;
        for (let i = 0; i < 25; i++) {
            const e = inv.getElement(i);
            if (e && e.dataset?.key && !inv.isEmpty(e)) count++;
        }
        return count;
    }""")

    item_key = before.get("key", "unknown") if isinstance(before, dict) else "unknown"
    count_before = before.get("count", -1) if isinstance(before, dict) else -1

    if isinstance(after, int) and after < count_before:
        return json.dumps({"dropped": True, "item": item_key, "slot": slot,
                           "inventory_before": count_before, "inventory_after": after})
    else:
        return json.dumps({"dropped": False, "item": item_key, "slot": slot,
                           "error": "Drop may have failed — inventory count unchanged",
                           "inventory_before": count_before, "inventory_after": after})


@mcp.tool()
async def equip_item(ctx: Context, slot: int) -> str:
    """Equip an item from inventory.

    Args:
        slot: Inventory slot number (0-24)
    """
    page = await _page(ctx)

    # Use the reliable __equipItem helper (sends Container.Select packet directly)
    result = await page.evaluate("(s) => window.__equipItem(s)", slot)

    if isinstance(result, dict) and result.get("error"):
        return json.dumps(result)

    await page.wait_for_timeout(1500)

    # Verify: did any equipment slot change?
    after = await page.evaluate("""() => {
        const p = window.game && window.game.player;
        if (!p || !p.equipments) return {};
        const slots = {};
        for (let i = 0; i < p.equipments.length; i++) {
            const eq = p.equipments[i];
            slots[i] = eq ? (eq.name || eq.key || 'none') : 'none';
        }
        return slots;
    }""")

    before = result.get("equipment_before", {}) if isinstance(result, dict) else {}
    item_key = result.get("item", "unknown") if isinstance(result, dict) else "unknown"

    changed_slots = {}
    if isinstance(before, dict) and isinstance(after, dict):
        for k in set(list(str(k) for k in before.keys()) + list(str(k) for k in after.keys())):
            if str(before.get(k, before.get(int(k) if k.isdigit() else k))) != str(after.get(k, after.get(int(k) if k.isdigit() else k))):
                changed_slots[k] = {"before": before.get(k, before.get(int(k) if k.isdigit() else k)),
                                    "after": after.get(k, after.get(int(k) if k.isdigit() else k))}

    if changed_slots:
        return json.dumps({
            "equipped": True, "slot": slot, "item": item_key,
            "changes": changed_slots,
        })
    else:
        return json.dumps({
            "equipped": False, "slot": slot, "item": item_key,
            "error": "Equip failed — no equipment slot changed. Stat/level requirement not met, or item already equipped.",
        })


# ── Recovery ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def clear_combat(ctx: Context) -> str:
    """Clear combat state and cooldown timer so you can warp."""
    page = await _page(ctx)
    result = await page.evaluate("""() => {
        const r = window.__clearCombatState();
        window.__kaetramState.lastCombatTime = 0;
        window.__kaetramState.lastCombat = null;
        return JSON.stringify(r);
    }""")
    return result


@mcp.tool()
async def stuck_reset(ctx: Context) -> str:
    """Reset stuck detection. Use when stuck check shows stuck=true."""
    page = await _page(ctx)
    await page.evaluate("() => window.__stuckReset()")
    return "Stuck state reset"



@mcp.tool()
async def click_tile(ctx: Context, x: int, y: int) -> str:
    """Click a specific grid tile (must be on screen). Fallback for edge cases.

    Args:
        x: Grid X coordinate
        y: Grid Y coordinate
    """
    page = await _page(ctx)
    result = await page.evaluate(
        "([x,y]) => JSON.stringify(window.__clickTile(x, y))", [x, y]
    )
    await page.wait_for_timeout(2000)
    return result


@mcp.tool()
async def respawn(ctx: Context) -> str:
    """Respawn after death, clear all combat state, and warp to Mudwich."""
    page = await _page(ctx)
    await page.evaluate(
        "() => { const btn = document.getElementById('respawn'); if (btn) btn.click(); }"
    )
    await page.wait_for_timeout(2000)
    # Clear stale combat state from before death (prevents warp cooldown trap)
    await page.evaluate("""() => {
        window.__clearCombatState();
        window.__kaetramState.lastCombatTime = 0;
        window.__kaetramState.lastCombat = null;
    }""")
    await page.wait_for_timeout(1000)
    result = await page.evaluate(
        "(id) => JSON.stringify(window.__safeWarp(id))", 0
    )
    await page.wait_for_timeout(3000)
    return "Respawned and combat cleared. " + result


# ── Gathering & Looting ──────────────────────────────────────────────────────


@mcp.tool()
async def gather(ctx: Context, resource_name: str) -> str:
    """Gather from a nearby resource (tree, rock, bush, fish spot).

    Finds the nearest non-exhausted resource matching the name, clicks on it
    to start gathering, waits for completion, and reports what was collected.

    Args:
        resource_name: Name of resource (e.g. 'Oak', 'Nisoc Rock', 'Tomato', 'Blueberry Bush', 'Blue Lily')
    """
    log_tool("gather")
    page = await _page(ctx)

    # Snapshot inventory before
    inv_before = await page.evaluate("""() => {
        try {
            const inv = window.game.menu.getInventory();
            const items = {};
            if (inv && inv.getElement) {
                for (let i = 0; i < 25; i++) {
                    const el = inv.getElement(i);
                    if (!el || !el.dataset?.key || inv.isEmpty(el)) continue;
                    const k = el.dataset.key;
                    items[k] = (items[k] || 0) + (el.count || parseInt(el.dataset?.count || '0') || 1);
                }
            }
            return items;
        } catch(e) { return {}; }
    }""")

    # Find nearest matching resource (types 10=tree, 11=rock, 12=foraging, 13=fishspot)
    resource = await page.evaluate("""(name) => {
        const gs = window.__extractGameState();
        if (!gs) return { error: 'Game not loaded' };
        const nameLower = name.toLowerCase();
        const resources = (gs.nearby_entities || []).filter(e =>
            [10, 11, 12, 13].includes(e.type) &&
            !e.exhausted &&
            (e.name || '').toLowerCase().includes(nameLower)
        );
        if (resources.length === 0) return { error: 'No resource matching "' + name + '" nearby. Try moving closer or check observe output for available resources.' };
        resources.sort((a, b) => a.distance - b.distance);
        return resources[0];
    }""", resource_name)

    if isinstance(resource, str):
        resource = json.loads(resource)
    if resource.get("error"):
        return json.dumps(resource)

    # Click on resource tile — triggers client pathfinding + Target.Object packet
    click_result = await page.evaluate(
        "([x,y]) => JSON.stringify(window.__clickTile(x, y))", [resource["x"], resource["y"]]
    )

    # Wait for walk + gathering animation (foraging ~3s, trees/rocks ~5s)
    resource_type = resource.get("type", 0)
    wait_ms = 4000 if resource_type == 12 else 7000
    await page.wait_for_timeout(wait_ms)

    # Snapshot inventory after
    inv_after = await page.evaluate("""() => {
        try {
            const inv = window.game.menu.getInventory();
            const items = {};
            if (inv && inv.getElement) {
                for (let i = 0; i < 25; i++) {
                    const el = inv.getElement(i);
                    if (!el || !el.dataset?.key || inv.isEmpty(el)) continue;
                    const k = el.dataset.key;
                    items[k] = (items[k] || 0) + (el.count || parseInt(el.dataset?.count || '0') || 1);
                }
            }
            return items;
        } catch(e) { return {}; }
    }""")

    # Diff inventory
    gained = {}
    for k, v in inv_after.items():
        diff = v - inv_before.get(k, 0)
        if diff > 0:
            gained[k] = diff

    return json.dumps({
        "resource": resource.get("name", resource_name),
        "position": {"x": resource["x"], "y": resource["y"]},
        "type": resource_type,
        "items_gained": gained if gained else "none (may need higher skill level or correct tool equipped)",
    })


@mcp.tool()
async def loot(ctx: Context) -> str:
    """Pick up nearby ground items and lootbag contents after combat.

    Walks to the nearest dropped item or lootbag. Single items (type 2) are
    auto-collected on walk-over. Lootbags (type 8) are opened and all items taken.
    """
    page = await _page(ctx)

    # Snapshot inventory before
    inv_before = await page.evaluate("""() => {
        try {
            const inv = window.game.menu.getInventory();
            const items = {};
            if (inv && inv.getElement) {
                for (let i = 0; i < 25; i++) {
                    const el = inv.getElement(i);
                    if (!el || !el.dataset?.key || inv.isEmpty(el)) continue;
                    const k = el.dataset.key;
                    items[k] = (items[k] || 0) + (el.count || parseInt(el.dataset?.count || '0') || 1);
                }
            }
            return items;
        } catch(e) { return {}; }
    }""")

    # Find nearest lootable (type 2 = item, type 8 = lootbag)
    result_raw = await page.evaluate("""() => {
        const game = window.game;
        if (!game || !game.player) return JSON.stringify({ error: 'Game not loaded' });
        const player = game.player;
        const allEnts = game.entities.entities || {};
        const lootable = [];
        for (const [inst, ent] of Object.entries(allEnts)) {
            if (ent.type !== 2 && ent.type !== 8) continue;
            const dist = Math.abs(ent.gridX - player.gridX) + Math.abs(ent.gridY - player.gridY);
            if (dist <= 15) {
                lootable.push({
                    instance: inst, type: ent.type,
                    name: ent.name || 'Unknown',
                    x: ent.gridX, y: ent.gridY, distance: dist
                });
            }
        }
        if (lootable.length === 0) return JSON.stringify({ found: 0 });
        lootable.sort((a, b) => a.distance - b.distance);
        const target = lootable[0];
        // Click on it to walk there
        const coords = window.__tileToScreenCoords(target.x, target.y);
        if (coords && !coords.error) {
            game.player.disableAction = false;
            document.getElementById('canvas').dispatchEvent(new MouseEvent('click', {
                clientX: coords.click_x, clientY: coords.click_y, bubbles: true
            }));
        }
        return JSON.stringify({ found: lootable.length, targeting: target });
    }""")
    result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw

    if result.get("found", 0) == 0:
        return json.dumps({"message": "No items or lootbags nearby to pick up"})

    # Wait for walk + auto-pickup
    await page.wait_for_timeout(3000)

    # If lootbag, try taking all items
    targeting = result.get("targeting", {})
    if targeting.get("type") == 8:
        await page.evaluate("""() => {
            const game = window.game;
            if (!game || !game.socket) return;
            // Packets.LootBag = 58, Opcodes.LootBag.Take = 1
            for (let i = 0; i < 10; i++) {
                try { game.socket.send(58, { opcode: 1, index: i }); } catch(e) {}
            }
        }""")
        await page.wait_for_timeout(1000)

    # Diff inventory
    inv_after = await page.evaluate("""() => {
        try {
            const inv = window.game.menu.getInventory();
            const items = {};
            if (inv && inv.getElement) {
                for (let i = 0; i < 25; i++) {
                    const el = inv.getElement(i);
                    if (!el || !el.dataset?.key || inv.isEmpty(el)) continue;
                    const k = el.dataset.key;
                    items[k] = (items[k] || 0) + (el.count || parseInt(el.dataset?.count || '0') || 1);
                }
            }
            return items;
        } catch(e) { return {}; }
    }""")

    gained = {}
    for k, v in inv_after.items():
        diff = v - inv_before.get(k, 0)
        if diff > 0:
            gained[k] = diff

    return json.dumps({
        "target": targeting.get("name", "unknown"),
        "target_type": "lootbag" if targeting.get("type") == 8 else "ground_item",
        "items_collected": gained if gained else "none (item may have despawned or inventory full)",
        "other_nearby": result.get("found", 0) - 1,
    })


@mcp.tool()
async def query_quest(ctx: Context, quest_name: str) -> str:
    """Look up detailed walkthrough for a specific quest.

    Returns step-by-step instructions, item requirements, NPC locations,
    boss stats, and crafting recipes for the requested quest.

    Args:
        quest_name: Quest name (e.g. 'Sorcery', 'Scavenger', 'Coder Glitch', 'Royal Drama')
    """
    import os
    walkthroughs_path = os.path.join(os.path.dirname(__file__), "prompts", "quest_walkthroughs.json")
    try:
        with open(walkthroughs_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return json.dumps({"error": "Quest walkthrough data not found"})

    # Fuzzy match quest name
    name_lower = quest_name.lower().strip()
    best_match = None
    best_score = 0
    for key, quest in data.items():
        key_lower = key.lower()
        # Exact match
        if name_lower == key_lower:
            best_match = quest
            break
        # Partial match
        score = sum(1 for w in name_lower.split() if w in key_lower)
        if score > best_score:
            best_score = score
            best_match = quest
    if not best_match:
        available = ", ".join(data.keys())
        return json.dumps({"error": f"No quest matching '{quest_name}'. Available: {available}"})

    return json.dumps(best_match, indent=2)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log("[mcp] Starting Kaetram MCP server")
    mcp.run(transport="stdio")
