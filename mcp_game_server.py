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
import re
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

        # WebSocket port override for multi-agent isolation
        port = os.environ.get("KAETRAM_PORT", "")
        if port:
            await context.add_init_script(f"""(() => {{
                const PORT = '{port}';
                const _WS = window.WebSocket;
                window.WebSocket = function(url, protocols) {{
                    try {{
                        const parsed = new URL(url, window.location.href);
                        if (parsed.port === '9001') parsed.port = PORT;
                        url = parsed.toString();
                    }} catch (_e) {{
                        url = url.replace(/:9001(?=\\/|$)/, ':' + PORT);
                    }}
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
        screenshot_path = os.path.join(screenshot_dir, "live_screen.jpg")

        async def on_console(msg):
            if msg.text == "LIVE_SCREENSHOT_TRIGGER":
                try:
                    await page.screenshot(path=screenshot_path, type="jpeg", quality=70)
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


_PRODUCTION_SKILL_ALIASES = {
    "cook": "cooking",
    "cooking": "cooking",
    "craft": "crafting",
    "crafting": "crafting",
    "smith": "smithing",
    "smithing": "smithing",
    "smelt": "smelting",
    "smelting": "smelting",
    "brew": "alchemy",
    "alchemy": "alchemy",
    "fletch": "fletching",
    "fletching": "fletching",
    "chisel": "chiseling",
    "chiseling": "chiseling",
}


async def _page_in_game(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """() => (
                    document.body &&
                    document.body.className === 'game' &&
                    !!(window.game && window.game.player)
                )"""
            )
        )
    except Exception:
        return False


async def _ensure_state_extractor(page) -> None:
    ready = await page.evaluate(
        "() => typeof window.__extractGameState === 'function'"
    )
    if ready:
        return

    extractor_path = os.environ.get("KAETRAM_EXTRACTOR", "state_extractor.js")
    if not os.path.exists(extractor_path):
        raise RuntimeError(f"state extractor not found at {extractor_path}")
    await page.add_script_tag(path=extractor_path)
    log(f"[mcp] Injected {extractor_path}")


async def _current_intro_state(page) -> dict:
    try:
        return await page.evaluate(
            """() => ({
                bodyClass: document.body ? document.body.className : null,
                loadCharVisible: (() => {
                    const el = document.getElementById('load-character');
                    return !!el && window.getComputedStyle(el).opacity !== '0';
                })(),
                loginVisible: !!document.querySelector('#login-name-input'),
                registerVisible: (() => {
                    const el = document.getElementById('create-character');
                    return !!el && window.getComputedStyle(el).opacity !== '0';
                })(),
                errors: Array.from(document.querySelectorAll('.validation-error, .error-message, [class*="error"]'))
                    .map(e => e.textContent && e.textContent.trim())
                    .filter(Boolean)
                    .slice(0, 10),
                hasGame: !!(window.game && window.game.player),
                socketConnected: !!(window.game && window.game.socket && window.game.socket.connected),
                playerName: window.game && window.game.player && window.game.player.name,
                gridX: window.game && window.game.player && window.game.player.gridX,
            })"""
        )
    except Exception as exc:
        return {"error": f"failed to collect intro state: {exc}"}


async def _wait_for_game_loaded(page, *, timeout_ms: int) -> None:
    await page.wait_for_function(
        "() => document.body && document.body.className === 'game'",
        timeout=timeout_ms,
    )
    await page.wait_for_function(
        "() => !!(window.game && window.game.player && window.game.player.name "
        "&& typeof window.game.player.gridX === 'number' && window.game.player.gridX > 0)",
        timeout=timeout_ms,
    )


async def _login_impl(ctx: Context, page) -> str:
    username = os.environ.get("KAETRAM_USERNAME", "ClaudeBot")
    password = os.environ.get("KAETRAM_PASSWORD", "password123")
    client_url = os.environ.get("KAETRAM_CLIENT_URL", "http://localhost:9000")

    await page.goto(client_url, wait_until="domcontentloaded")
    await page.wait_for_function(
        "() => { const b = document.querySelector('#login'); return !!b && !b.disabled; }",
        timeout=60_000,
    )
    await page.locator("#login-name-input").fill(username)
    await page.locator("#login-password-input").fill(password)
    await page.locator("#login").click()

    game_ready = False
    try:
        await _wait_for_game_loaded(page, timeout_ms=30_000)
        game_ready = True
    except Exception:
        state = await _current_intro_state(page)
        log(f"[mcp] login primary path stalled for {username}: {state}")

        # Keep the register fallback for local ad-hoc use, but only after the
        # normal login path has demonstrably failed.
        if state.get("bodyClass") != "game":
            log(f"[mcp] Registering new account for {username}")
            try:
                await page.locator("#new-account").click()
                await page.wait_for_function(
                    """() => {
                        const el = document.getElementById('create-character');
                        return !!el && window.getComputedStyle(el).opacity !== '0';
                    }""",
                    timeout=10_000,
                )
                await page.locator("#register-name-input").fill(username)
                await page.locator("#register-password-input").fill(password)
                await page.locator("#register-password-confirmation-input").fill(password)
                await page.locator("#register-email-input").fill(f"{username}@test.com")
                await page.locator("#play").click()
                await _wait_for_game_loaded(page, timeout_ms=30_000)
                game_ready = True
            except Exception:
                state = await _current_intro_state(page)
                log(f"[mcp] register fallback stalled for {username}: {state}")

    if not game_ready:
        log(f"[mcp] Login failed for {username} — game did not load")
        log_tool("login", success=False, error="game did not load")
        ctx.request_context.lifespan_context["logged_in"] = False
        return (
            "Login FAILED — game did not load. "
            "The game client may not be connected to the server."
        )

    await page.wait_for_timeout(1000)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)

    await _ensure_state_extractor(page)
    ctx.request_context.lifespan_context["logged_in"] = True
    log(f"[mcp] Logged in as {username}")
    log_tool("login")
    return f"Logged in as {username}"


def _normalize_production_skill(skill: str) -> str:
    return _PRODUCTION_SKILL_ALIASES.get((skill or "").strip().lower(), "")


async def _page(ctx: Context, ensure_logged_in: bool = True):
    """Get the Playwright page, launching browser if needed."""
    state = ctx.request_context.lifespan_context
    page = await _ensure_browser(state)
    if not ensure_logged_in:
        return page

    if state.get("logged_in") and await _page_in_game(page):
        await _ensure_state_extractor(page)
        return page

    login_result = await _login_impl(ctx, page)
    if "FAILED" in login_result.upper():
        raise RuntimeError(login_result)
    return page


_QUEST_WALKTHROUGHS_PATH = os.path.join(
    os.path.dirname(__file__), "prompts", "quest_walkthroughs.json"
)


def _load_quest_walkthroughs() -> dict:
    with open(_QUEST_WALKTHROUGHS_PATH) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Quest walkthrough data must be a JSON object")
    return data


def _normalize_quest_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return re.sub(r"\s+", " ", cleaned)


def _resolve_quest_name(query: str, data: dict) -> tuple[str | None, dict | None]:
    norm_query = _normalize_quest_name(query)
    if not norm_query:
        return None, {"error": "Quest name is empty"}

    canonical = {}
    for key, quest in data.items():
        if not isinstance(quest, dict):
            continue
        names = {key}
        display_name = quest.get("name")
        if isinstance(display_name, str) and display_name.strip():
            names.add(display_name)
        canonical[key] = {_normalize_quest_name(name) for name in names if name}

    exact_matches = [
        key for key, normalized_names in canonical.items() if norm_query in normalized_names
    ]
    if len(exact_matches) == 1:
        return exact_matches[0], None
    if len(exact_matches) > 1:
        return None, {
            "error": f"Ambiguous quest name '{query}'",
            "matches": sorted(exact_matches),
        }

    substring_matches = [
        key
        for key, normalized_names in canonical.items()
        if any(norm_query in normalized_name for normalized_name in normalized_names)
    ]
    if len(substring_matches) == 1:
        return substring_matches[0], None
    if len(substring_matches) > 1:
        return None, {
            "error": f"Ambiguous quest name '{query}'",
            "matches": sorted(substring_matches),
        }

    query_tokens = set(norm_query.split())
    scored: list[tuple[int, str]] = []
    for key, normalized_names in canonical.items():
        best_score = 0
        for normalized_name in normalized_names:
            name_tokens = set(normalized_name.split())
            best_score = max(best_score, len(query_tokens & name_tokens))
        if best_score > 0:
            scored.append((best_score, key))

    if not scored:
        return None, {
            "error": f"No quest matching '{query}'",
            "available": sorted(data.keys()),
        }

    scored.sort(key=lambda item: (-item[0], item[1]))
    top_score = scored[0][0]
    top_matches = sorted([key for score, key in scored if score == top_score])
    if len(top_matches) > 1:
        return None, {
            "error": f"Ambiguous quest name '{query}'",
            "matches": top_matches,
        }

    return top_matches[0], None


def _build_quest_query_response(matched_name: str, quest: dict) -> dict:
    ordered = {
        "name": quest.get("name", matched_name),
        "matched_name": matched_name,
        "status": quest.get("status", "unknown"),
        "phase": quest.get("phase"),
        "order": quest.get("order"),
        "blocked_reason": quest.get("blocked_reason"),
        "requirements": quest.get("requirements", {}),
        "unlocks": quest.get("unlocks", {}),
        "actual_rewards": quest.get("actual_rewards", []),
        "reward_caveats": quest.get("reward_caveats", []),
        "known_mismatches": quest.get("known_mismatches", []),
    }
    for key in (
        "npc",
        "stages",
        "prereqs",
        "stage_summary",
        "walkthrough",
        "items_needed",
        "item_sources",
        "crafting_chain",
        "boss",
        "tips",
    ):
        if key in quest:
            ordered[key] = quest[key]
    if ordered["status"] == "blocked":
        ordered["skip_recommended"] = True
    return ordered


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
            path=os.path.join(screenshot_dir, "live_screen.jpg"), type="jpeg", quality=70
        )
    except Exception:
        pass

    result = await page.evaluate("""() => {
        if (typeof window.__extractGameState !== 'function') {
            return 'ERROR: State extractor not loaded. Session is not ready yet.';
        }
        // Always extract FRESH state — never use stale cache
        const gs = window.__extractGameState();
        const am = window.__generateAsciiMap();
        const sc = window.__stuckCheck ? window.__stuckCheck() : {};

        // Check freshness — warn if game object seems stale
        const age_ms = gs.timestamp ? (Date.now() / 1000 - gs.timestamp) * 1000 : 0;
        if (gs.error) {
            return 'ERROR: ' + gs.error + ' (game may not be ready yet — retry observe)';
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
            # Auto-loot is intentionally limited to ground items (type=2) which
            # auto-pickup on walk-over. Lootbags (type=8) require the two-step
            # popup flow — leave those for an explicit loot() call so the
            # agent can reason about popup open/take as distinct steps.
            if auto_looted["targeting"].get("type") == 8:
                auto_looted = {
                    "lootbag_nearby": auto_looted["targeting"],
                    "note": "Lootbag dropped — call loot() to walk to it and open the popup, then loot() again to take items.",
                }
            else:
                # Wait for walk + auto-pickup (scale with distance)
                dist = auto_looted["targeting"].get("distance", 3)
                wait_ms = min(max(1500, dist * 300), 5000)
                await page.wait_for_timeout(wait_ms)

                # Diff inventory to surface what was picked up
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
        style: 'hack' (strength+defense), 'chop' (accuracy+defense), or 'defensive' (defense)
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
    normalized = (location or "").lower()
    if normalized not in warp_ids:
        return json.dumps({
            "error": f"Unknown warp location '{location}'",
            "allowed": sorted(warp_ids),
        })
    warp_id = warp_ids[normalized]
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

    # Hardcoded NPC→store key mapping (avoids unreliable client-side store.key read)
    NPC_STORE_KEYS = {
        "forester": "forester",
        "miner": "miner",
        "yet another miner": "miner",
        "sorcerer": "sorcerer",
        "fisherman": "fishingstore",
        "babushka": "ingredientsstore",
        "kosmetics vendor": "cosmetics",
        "clerk": "startshop",
    }
    store_key = NPC_STORE_KEYS.get(npc_name.lower())
    if not store_key:
        return json.dumps({
            "error": f"Unknown store NPC '{npc_name}'. Known: {', '.join(NPC_STORE_KEYS.keys())}",
            "npc": npc_name,
        })

    # Step 1: Find the store NPC and, if needed, walk to an orthogonal
    # neighbor tile. Avoid the generic interact helper here because it auto-
    # talks when already adjacent, which can race store open/close behavior.
    walk = await page.evaluate("""(name) => {
        const game = window.game;
        if (!game || !game.player || !game.entities) return { error: 'Game not loaded' };
        const p = game.player;
        const px = p.gridX, py = p.gridY;
        const needle = (name || '').toLowerCase();
        let best = null;
        let bestDist = Infinity;

        for (const [inst, ent] of Object.entries(game.entities.entities || {})) {
            if (!ent || ent.type !== 1 || inst === p.instance) continue;
            const entName = (ent.name || ent.key || '').toLowerCase();
            if (!entName.includes(needle)) continue;
            const dist = Math.abs(ent.gridX - px) + Math.abs(ent.gridY - py);
            if (dist < bestDist) {
                best = { instance: inst, x: ent.gridX, y: ent.gridY, name: ent.name || ent.key || name };
                bestDist = dist;
            }
        }

        if (!best) return { error: 'No NPC matching \"' + name + '\" found nearby' };

        const manhattan = Math.abs(best.x - px) + Math.abs(best.y - py);
        let walkTarget = null;
        if (manhattan >= 2) {
            const neighbors = [
                { x: best.x, y: best.y - 1 },
                { x: best.x, y: best.y + 1 },
                { x: best.x - 1, y: best.y },
                { x: best.x + 1, y: best.y },
            ];
            walkTarget = neighbors[0];
            let bestNeighborDist = Infinity;
            for (const neighbor of neighbors) {
                const dist = Math.abs(neighbor.x - px) + Math.abs(neighbor.y - py);
                if (dist < bestNeighborDist) {
                    bestNeighborDist = dist;
                    walkTarget = neighbor;
                }
            }
            p.disableAction = false;
            p.go(walkTarget.x, walkTarget.y);
        }

        return {
            instance: best.instance,
            npc_pos: { x: best.x, y: best.y },
            player_pos: { x: px, y: py },
            manhattan: manhattan,
            walk_target: walkTarget,
        };
    }""", npc_name)

    if isinstance(walk, dict) and walk.get("error"):
        return json.dumps({"error": f"Cannot find NPC '{npc_name}': {walk.get('error')}"})

    npc_pos = walk.get("npc_pos", {}) if isinstance(walk, dict) else {}

    # Wait for arrival
    for wait_i in range(8):
        await page.wait_for_timeout(1000)
        pos = await page.evaluate("""(npcPos) => {
            const p = window.game && window.game.player;
            if (!p) return { manhattan: 999 };
            return { manhattan: Math.abs(p.gridX - npcPos.x) + Math.abs(p.gridY - npcPos.y) };
        }""", npc_pos)
        if pos.get("manhattan", 999) < 2:
            break

    # Step 2: Stop movement, then talk once to open store server-side
    instance_id = walk.get("instance", "") if isinstance(walk, dict) else ""
    if instance_id:
        # Stop player movement to prevent storeOpen being cleared
        await page.evaluate("""() => {
            const p = window.game && window.game.player;
            if (p && p.stop) p.stop(true);
        }""")
        await page.wait_for_timeout(200)
        await page.evaluate("(id) => window.__talkToNPC(id)", instance_id)
        await page.wait_for_timeout(1000)

        # Store opening is not instantaneous on the isolated lane. Poll for the
        # menu to become active before sending the buy packet so we do not turn
        # a valid purchase into a silent no-op.
        store_open = False
        for _ in range(10):
            store_state = await page.evaluate("""() => {
                try {
                    const store = window.game && window.game.menu && window.game.menu.getStore
                        ? window.game.menu.getStore()
                        : null;
                    return {
                        visible: !!(store && store.isVisible && store.isVisible()),
                        key: store && store.key ? store.key : null,
                    };
                } catch(e) {
                    return { visible: false, key: null };
                }
            }""")
            if store_state.get("visible"):
                store_open = True
                break
            await page.wait_for_timeout(300)
        if not store_open:
            return json.dumps({
                "bought": False,
                "store": store_key,
                "item_index": item_index,
                "error": f"Store UI for '{npc_name}' never opened",
            })

    # Step 3: Send buy packet — Packets.Store=40, Opcodes.Store.Buy=2
    buy_result = await page.evaluate("""([key, index, count]) => {
        try {
            window.game.socket.send(40, {
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

    # Step 4: Diff inventory to confirm purchase. The isolated lane can take a
    # few round-trips to update both inventory and currency, so poll instead of
    # snapshotting once.
    after = {}
    for _ in range(10):
        await page.wait_for_timeout(500)
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
        gold_before = before.get("gold", 0) if isinstance(before, dict) else 0
        gold_after = after.get("gold", 0) if isinstance(after, dict) else 0

        gained = {}
        for k, v in after_items.items():
            diff = v - before_items.get(k, 0)
            if diff > 0 and k != "gold":
                gained[k] = diff
        if gained or gold_after < gold_before:
            break

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
        const empty = !el || !el.dataset?.key || (inv.isEmpty && inv.isEmpty(el));
        if (empty) return { error: 'No item in slot ' + idx };
        const key = (el.dataset && el.dataset.key) || 'unknown';
        const slotCount = el.count || parseInt(el.dataset?.count || '0') || 1;
        let occupied = 0;
        for (let i = 0; i < 25; i++) {
            const e = inv.getElement(i);
            if (e && e.dataset?.key && !inv.isEmpty(e)) occupied++;
        }
        return { key: key, slot_count: slotCount, occupied_count: occupied };
    }""", slot)

    if isinstance(before, dict) and before.get("error"):
        return json.dumps(before)

    # Send container remove packet: Packets.Container=21, Opcodes.Container.Remove=2
    result = await page.evaluate("""(idx) => {
        try {
            window.game.socket.send(21, {
                opcode: 2,
                type: 1,
                fromIndex: idx,
                value: 1
            });
            return { sent: true };
        } catch(e) {
            return { error: 'Failed to send drop packet: ' + e.message };
        }
    }""", slot)

    if isinstance(result, dict) and result.get("error"):
        return json.dumps(result)

    await page.wait_for_timeout(1000)

    # Verify item was dropped.
    after = await page.evaluate("""(idx) => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        if (!inv) return { occupied_count: -1, slot_key: null, slot_count: 0 };
        const el = inv.getElement(idx);
        const empty = !el || !el.dataset?.key || (inv.isEmpty && inv.isEmpty(el));
        let occupied = 0;
        for (let i = 0; i < 25; i++) {
            const e = inv.getElement(i);
            if (e && e.dataset?.key && !inv.isEmpty(e)) occupied++;
        }
        return {
            occupied_count: occupied,
            slot_key: empty ? null : el.dataset.key,
            slot_count: empty ? 0 : (el.count || parseInt(el.dataset?.count || '0') || 1)
        };
    }""", slot)

    item_key = before.get("key", "unknown") if isinstance(before, dict) else "unknown"
    occupied_before = before.get("occupied_count", -1) if isinstance(before, dict) else -1
    slot_count_before = before.get("slot_count", 0) if isinstance(before, dict) else 0
    occupied_after = after.get("occupied_count", -1) if isinstance(after, dict) else -1
    slot_count_after = after.get("slot_count", 0) if isinstance(after, dict) else 0
    slot_key_after = after.get("slot_key") if isinstance(after, dict) else None

    dropped = (
        occupied_after < occupied_before
        or slot_count_after < slot_count_before
        or slot_key_after != item_key
    )

    if dropped:
        return json.dumps({
            "dropped": True,
            "item": item_key,
            "slot": slot,
            "inventory_before": occupied_before,
            "inventory_after": occupied_after,
            "slot_count_before": slot_count_before,
            "slot_count_after": slot_count_after,
        })

    return json.dumps({
        "dropped": False,
        "item": item_key,
        "slot": slot,
        "error": "Drop may have failed — inventory count unchanged",
        "inventory_before": occupied_before,
        "inventory_after": occupied_after,
        "slot_count_before": slot_count_before,
        "slot_count_after": slot_count_after,
    })


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
        for (const [slotId, eq] of Object.entries(p.equipments)) {
            slots[slotId] = eq ? (eq.name || eq.key || 'none') : 'none';
        }
        return slots;
    }""")

    before = result.get("equipment_before", {}) if isinstance(result, dict) else {}
    item_key = result.get("item", "unknown") if isinstance(result, dict) else "unknown"

    # Normalize all keys to strings for comparison
    before_norm = {str(k): str(v) for k, v in before.items()} if isinstance(before, dict) else {}
    after_norm = {str(k): str(v) for k, v in after.items()} if isinstance(after, dict) else {}
    changed_slots = {}
    for k in set(list(before_norm.keys()) + list(after_norm.keys())):
        if before_norm.get(k) != after_norm.get(k):
            changed_slots[k] = {"before": before_norm.get(k, "none"), "after": after_norm.get(k, "none")}

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
    """Pick up ground items or take contents from an open lootbag popup.

    State-aware. One call per state transition:
      - If lootbag_popup.open=true (see observe): takes all items in the popup.
      - Else if a lootbag (type=8) is nearby: walks to it. Popup should open
        server-side on arrival; call loot() again to empty it.
      - Else if a ground item (type=2) is nearby: walks to it. Server auto-adds
        to inventory on walk-over.
      - Else: reports nothing nearby.

    Check observe()'s lootbag_popup field to see which state you're in.
    """
    log_tool("loot")
    page = await _page(ctx)

    _INV_SNAPSHOT = """() => {
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
    }"""

    inv_before = await page.evaluate(_INV_SNAPSHOT)

    # ── State B: popup already open → take all items via direct Take packets ──
    popup = await page.evaluate("() => window.__lootbagPopupState()")
    if isinstance(popup, dict) and popup.get("open"):
        take_result = await page.evaluate("() => window.__takeAllFromLootbag()")
        # Each Take packet round-trips; give the server time to process + push
        # inventory updates + close the bag if empty.
        await page.wait_for_timeout(1500)
        inv_after = await page.evaluate(_INV_SNAPSHOT)
        gained = {k: v - inv_before.get(k, 0) for k, v in inv_after.items() if v - inv_before.get(k, 0) > 0}
        popup_after = await page.evaluate("() => window.__lootbagPopupState()")
        return json.dumps({
            "state": "taken" if gained else "take_attempted_no_gain",
            "items_collected": gained or "none",
            "packets_sent": take_result.get("sent", 0) if isinstance(take_result, dict) else 0,
            "popup_still_open": bool(popup_after.get("open", False)) if isinstance(popup_after, dict) else False,
            "remaining_item_count": popup_after.get("item_count", 0) if isinstance(popup_after, dict) else 0,
            "note": "Inventory may be full if items_collected < packets_sent" if gained and take_result.get("sent", 0) > len(gained) else None,
        })

    # ── Find nearest lootable (type=2 ground item or type=8 lootbag) ──
    nearest = await page.evaluate("""() => {
        const game = window.game;
        if (!game || !game.player) return null;
        const player = game.player;
        const allEnts = game.entities.entities || {};
        let best = null;
        for (const [inst, ent] of Object.entries(allEnts)) {
            if (ent.type !== 2 && ent.type !== 8) continue;
            const dist = Math.abs(ent.gridX - player.gridX) + Math.abs(ent.gridY - player.gridY);
            if (dist > 15) continue;
            if (!best || dist < best.distance) {
                best = {
                    instance: inst, type: ent.type,
                    name: ent.name || 'Unknown',
                    x: ent.gridX, y: ent.gridY, distance: dist,
                };
            }
        }
        return best;
    }""")

    if not nearest:
        return json.dumps({
            "state": "nothing_nearby",
            "message": "No ground items (type=2) or lootbags (type=8) within 15 tiles.",
        })

    # Walk to the target tile. For lootbags, Movement.Stop's getEntityAt fallback
    # sets targetInstance = lootbag.instance, which triggers server.handleMovementStop
    # → entity.open(this) → popup shows. For ground items, walk-over auto-adds.
    await page.evaluate(
        "([x,y]) => window.__navigateTo(x, y)",
        [nearest["x"], nearest["y"]],
    )
    wait_ms = min(max(1500, int(nearest["distance"]) * 300), 5000)
    await page.wait_for_timeout(wait_ms)

    if nearest["type"] == 8:
        # Poll for popup to appear (server round-trip on open)
        popup_after = {"open": False}
        for _ in range(10):
            popup_after = await page.evaluate("() => window.__lootbagPopupState()")
            if isinstance(popup_after, dict) and popup_after.get("open"):
                break
            await page.wait_for_timeout(300)

        if isinstance(popup_after, dict) and popup_after.get("open"):
            return json.dumps({
                "state": "popup_opened",
                "target": nearest,
                "popup_item_count": popup_after.get("item_count", 0),
                "inventory_free_slots": popup_after.get("inventory_free_slots", 0),
                "next_step": "Call loot() again to take all items from the popup.",
            })
        return json.dumps({
            "state": "walking_to_bag",
            "target": nearest,
            "note": "Popup did not open within ~3s. Agent may not have stopped on the bag tile (pathing off). Call loot() again if still near.",
        })

    # Ground item: walk-over auto-pickup completed (or didn't)
    inv_after = await page.evaluate(_INV_SNAPSHOT)
    gained = {k: v - inv_before.get(k, 0) for k, v in inv_after.items() if v - inv_before.get(k, 0) > 0}
    return json.dumps({
        "state": "picked_up" if gained else "walked_but_nothing",
        "target": nearest,
        "items_collected": gained or "none",
        "note": None if gained else "Item may have despawned, been taken by another player, or inventory was full.",
    })


@mcp.tool()
async def craft_item(ctx: Context, skill: str, recipe_key: str, count: int = 1) -> str:
    """Open the relevant production interface and craft a recipe by key.

    Supports Crafting, Cooking, Smithing, Smelting, Alchemy, Fletching, and
    Chiseling. Station-based skills auto-walk to the nearest matching station
    on the current map. Fletching and Chiseling auto-use `knife` / `chisel`
    from inventory.
    """
    page = await _page(ctx)
    skill_name = _normalize_production_skill(skill)
    if not skill_name:
        return json.dumps({
            "error": f"Unknown production skill '{skill}'",
            "allowed": sorted(set(_PRODUCTION_SKILL_ALIASES.values())),
        })

    key = (recipe_key or "").strip().lower()
    if not key:
        return json.dumps({"error": "Recipe key is empty"})

    craft_count = max(1, min(int(count or 1), 25))
    inv_before = await page.evaluate(
        "() => window.__inventorySnapshot ? window.__inventorySnapshot() : {}"
    )

    open_result = await page.evaluate(
        "(skillName) => window.__openProductionInterface(skillName)", skill_name
    )
    if isinstance(open_result, str):
        open_result = json.loads(open_result)
    if open_result.get("error"):
        return json.dumps(open_result)

    if open_result.get("needs_move"):
        adjacent = open_result.get("adjacent") or {}
        target = open_result.get("target") or {}
        await page.evaluate(
            "([x,y]) => window.__navigateTo(x, y)", [adjacent.get("x"), adjacent.get("y")]
        )
        arrived = False
        final_pos = {}
        for _ in range(15):
            await page.wait_for_timeout(1000)
            final_pos = await page.evaluate("""([x,y]) => {
                const p = window.game && window.game.player;
                if (!p) return { distance: 999, player_pos: null };
                return {
                    distance: Math.abs(p.gridX - x) + Math.abs(p.gridY - y),
                    player_pos: { x: p.gridX, y: p.gridY }
                };
            }""", [adjacent.get("x"), adjacent.get("y")])
            if final_pos.get("distance", 999) <= 1:
                arrived = True
                break

        if not arrived:
            return json.dumps({
                "error": f"Could not reach {skill_name} station",
                "skill": skill_name,
                "target": target,
                "adjacent": adjacent,
                "player": final_pos.get("player_pos"),
            })

        open_result = await page.evaluate(
            "(skillName) => window.__openProductionInterface(skillName)", skill_name
        )
        if isinstance(open_result, str):
            open_result = json.loads(open_result)
        if open_result.get("error"):
            return json.dumps(open_result)

    crafting_state = {}
    interface_ready = False
    for _ in range(10):
        await page.wait_for_timeout(500)
        crafting_state = await page.evaluate(
            "() => window.__getCraftingState ? window.__getCraftingState() : ({ visible: false })"
        )
        if crafting_state.get("skill") != skill_name:
            continue
        if crafting_state.get("visible") or open_result.get("via") == "inventory_item":
            interface_ready = True
            break

    if not interface_ready:
        return json.dumps({
            "error": f"Could not open {skill_name} interface",
            "skill": skill_name,
            "open_result": open_result,
            "state": crafting_state,
        })

    select_result = await page.evaluate(
        "(recipe) => window.__selectCraftRecipe(recipe)", key
    )
    if isinstance(select_result, str):
        select_result = json.loads(select_result)
    if select_result.get("error"):
        return json.dumps(select_result)

    await page.wait_for_timeout(700)
    selected_state = await page.evaluate(
        "() => window.__getCraftingState ? window.__getCraftingState() : ({ visible: false })"
    )
    if selected_state.get("selected_key") != key:
        return json.dumps({
            "error": f"Recipe '{key}' is not available in the open {skill_name} interface",
            "skill": skill_name,
            "selected_state": selected_state,
        })

    craft_result = await page.evaluate(
        "([recipe, amount]) => window.__confirmCraftRecipe(recipe, amount)",
        [key, craft_count],
    )
    if isinstance(craft_result, str):
        craft_result = json.loads(craft_result)
    if craft_result.get("error"):
        return json.dumps(craft_result)

    await page.wait_for_timeout(2500)
    inv_after = await page.evaluate(
        "() => window.__inventorySnapshot ? window.__inventorySnapshot() : {}"
    )
    inventory_delta = {}
    keys = set(inv_before) | set(inv_after)
    for item_key in keys:
        diff = inv_after.get(item_key, 0) - inv_before.get(item_key, 0)
        if diff != 0:
            inventory_delta[item_key] = diff

    return json.dumps({
        "crafted": True,
        "skill": skill_name,
        "recipe_key": key,
        "count": craft_count,
        "opened_via": open_result.get("via"),
        "target": open_result.get("target"),
        "selected_name": selected_state.get("selected_name"),
        "inventory_delta": inventory_delta,
    })


@mcp.tool()
async def query_quest(ctx: Context, quest_name: str) -> str:
    """Look up detailed walkthrough for a specific quest.

    Returns quest status, requirements, unlocks, reward caveats, walkthrough,
    and boss or recipe notes for the requested quest.

    Args:
        quest_name: Exact or near-exact quest name (e.g. 'Sorcery and Stuff',
            'Scavenger', 'Royal Drama'). Generic names like 'coder' are
            intentionally rejected as ambiguous.
    """
    try:
        data = _load_quest_walkthroughs()
    except FileNotFoundError:
        return json.dumps({"error": "Quest walkthrough data not found"})
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Quest walkthrough data is invalid JSON: {exc}"})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    matched_name, err = _resolve_quest_name(quest_name, data)
    if err:
        return json.dumps(err, indent=2)

    quest = data.get(matched_name)
    if not isinstance(quest, dict):
        return json.dumps({"error": f"Quest data for '{matched_name}' is malformed"})

    return json.dumps(_build_quest_query_response(matched_name, quest), indent=2)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log("[mcp] Starting Kaetram MCP server")
    mcp.run(transport="stdio")
