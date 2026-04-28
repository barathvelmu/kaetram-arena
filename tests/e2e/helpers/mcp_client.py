"""Minimal MCP client for tests.

Spawns `mcp_game_server.py` as a stdio subprocess (same way play_qwen.py does)
and exposes `.call_tool(name, args)` + helpers to parse results. Designed for
direct-assertion tool tests — no LLM in the loop.

Parameters are wired to match the arena helper of the same name so test code
is portable if we ever unify.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python3"
MCP_SERVER = PROJECT_DIR / "mcp_game_server.py"
STATE_EXTRACTOR = PROJECT_DIR / "state_extractor.js"


# ── Live-suite warm-session pool ────────────────────────────────────────────
#
# When KAETRAM_LIVE_SUITE=1, mcp_session() borrows from this pool instead of
# spawning a fresh MCP subprocess + Chromium per test. Each entry holds a
# logged-in session that lives until its module's autouse fixture in
# tests/e2e/quests/reachability/conftest.py drains it on module teardown.
#
# Pool key = lowercased username. Reachability conftest pins test_username
# to module scope in live mode, so each test file ends up with a single
# pool entry. Production / data-collection runs don't set the env var, so
# the pool is unused and the legacy cold path is unchanged.
_warm_pool: dict[str, dict[str, Any]] = {}


def _live_mode() -> bool:
    return os.environ.get("KAETRAM_LIVE_SUITE", "").lower() in {"1", "true", "yes"}


async def _drain_warm_pool() -> None:
    """Close every warm session and reset the pool. Call from a module/session
    teardown fixture when running in live mode."""
    drained = list(_warm_pool.keys())
    if drained:
        _harness_log(f"drain_warm_pool: closing {len(drained)} session(s) [{', '.join(drained)}]")
    while _warm_pool:
        username, entry = _warm_pool.popitem()
        for closer in ("session", "transport"):
            obj = entry.get(closer)
            if obj is None:
                continue
            try:
                await obj.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 — drain is best-effort
                pass
    if drained:
        _harness_log("drain_warm_pool: done")


def _timing_enabled() -> bool:
    return os.environ.get("KAETRAM_TEST_TIMING", "1").lower() not in {"0", "false", "no"}


def _harness_log(message: str) -> None:
    if _timing_enabled():
        print(f"[harness] {message}", file=sys.stderr, flush=True)


async def _wait_for_tcp(host: str, port: int, *, timeout_s: float = 20.0, poll_s: float = 0.5, label: str = "endpoint") -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_err: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as exc:
            last_err = exc
            await asyncio.sleep(poll_s)
    raise RuntimeError(f"{label} {host}:{port} not reachable within {timeout_s:.1f}s: {last_err}")


async def _wait_for_client_url(client_url: str, *, timeout_s: float = 20.0) -> None:
    parsed = urlparse(client_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError(f"Unsupported KAETRAM_CLIENT_URL for readiness check: {client_url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    await _wait_for_tcp(parsed.hostname, port, timeout_s=timeout_s, label="Game client")


async def send_chat_command_via_browser(
    *,
    username: str,
    message: str,
    password: str = "test",
    client_url: str = "http://localhost:9000",
) -> None:
    """Temporary browser session for harness-only setup actions.

    Used by tests that need to trigger an existing server command path without
    widening the MCP tool surface.
    """
    from playwright.async_api import async_playwright

    game_ws_host = os.environ.get("GAME_WS_HOST", "localhost")
    # KAETRAM_PORT is the canonical name (set by tests/e2e/conftest.py to the
    # isolated test lane :9191). GAME_WS_PORT is a legacy fallback. Default
    # 9001 only applies when neither is set — that's the data-collection
    # lane, which uses kaetram_devlopment, not kaetram_e2e — so getting the
    # default here means seeded test players will fail to log in.
    game_ws_port_raw = (
        os.environ.get("KAETRAM_PORT")
        or os.environ.get("GAME_WS_PORT")
        or "9001"
    )
    try:
        game_ws_port = int(game_ws_port_raw)
    except ValueError:
        game_ws_port = 9001

    await _wait_for_tcp(game_ws_host, game_ws_port, label="Game server")
    await _wait_for_client_url(client_url)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()
        try:
            await page.goto(client_url)
            try:
                await page.wait_for_function(
                    """() => (
                        document.body?.className === 'game' ||
                        !!document.getElementById('login-name-input')
                    )""",
                    timeout=10000,
                )
            except Exception:
                pass
            await page.locator("#login-name-input").fill(username)
            await page.locator("#login-password-input").fill(password)
            await page.locator("#login").click()

            game_ready = False
            for _ in range(18):
                await page.wait_for_timeout(1000)
                result = await page.evaluate("""() => ({
                    game: !!(document.body && document.body.className === 'game'),
                    loginVisible: !!document.getElementById('load-character'),
                })""")
                if result.get("game"):
                    game_ready = True
                    break

            if not game_ready:
                raise RuntimeError(
                    f"Temporary browser session failed to log in as {username} "
                    f"before sending chat command {message!r}"
                )

            await page.wait_for_timeout(1000)
            send_result = await page.evaluate(
                """(text) => {
                    try {
                        const game = window.game;
                        if (!game || !game.socket) return { error: 'Game socket not ready' };
                        // Packets.Chat = 19 on the current tree.
                        game.socket.send(19, [text]);
                        return { sent: true, text };
                    } catch (e) {
                        return { error: String(e) };
                    }
                }""",
                message,
            )
            if isinstance(send_result, dict) and send_result.get("error"):
                raise RuntimeError(
                    f"Temporary browser session failed to send chat command {message!r}: {send_result}"
                )

            await page.wait_for_timeout(1200)
        finally:
            await context.close()
            await browser.close()


class McpSession:
    """Thin handle returned by `mcp_session()`. Type-only stub kept at module
    level so arena tests that annotate `session: McpSession` resolve. The
    real implementation is the local `_Handle` class inside `mcp_session()`;
    duck-typing means an instance of either class satisfies this annotation.
    """

    async def call_tool(self, name: str, args: dict[str, Any] | None = None) -> "ToolResult":
        ...

    async def list_tools(self) -> list[str]:
        ...


@dataclass
class ToolResult:
    """Parsed CallToolResult. Mirrors arena's helper — `.text` for raw body,
    `.json()` for the trailing JSON blob (after the leading `tool_name: ` prefix).
    """
    is_error: bool
    text: str

    def json(self) -> dict | None:
        """Parse the tool's JSON payload.

        Tool results come in one of three shapes:
          1. `tool_name: {json}`              — attack, observe, most tools
          2. `tool_name: {json}\\n\\nSTUCK_CHECK:\\n{json}` — observe
          3. `{json}`                         — some tools (errors, warp)

        The old logic split on the first `": "` and grabbed everything after,
        which broke when the JSON itself contained `": "` before the first
        brace (e.g. `{"error":"..."}`). Only strip the prefix when it looks
        like an identifier followed by `: {`.
        """
        import re
        body = self.text
        m = re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*:\s+", body)
        if m:
            body = body[m.end():]
        for sep in ("\n\nASCII_MAP:", "\n\nDIGEST:", "\n\nSTUCK_CHECK:"):
            if sep in body:
                body = body.split(sep)[0]
                break
        try:
            return json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return None

    def observe_stuck_check(self) -> dict | None:
        """Parse the STUCK_CHECK trailer that observe() emits."""
        marker = "\n\nSTUCK_CHECK:\n"
        if marker not in self.text:
            return None
        try:
            return json.loads(self.text.split(marker, 1)[1])
        except (ValueError, json.JSONDecodeError):
            return None


def _ts() -> float:
    return time.perf_counter()


async def _reseed_and_reconnect(entry: dict[str, Any], username: str) -> None:
    """Live-suite reconnect for a pooled MCP session.

    1. Close the current in-game session via `__test_close_session` — this
       navigates the browser to about:blank, which tears down the WS;
       Kaetram's server runs `handleClose() -> save() -> removePlayer()`
       (see `Kaetram-Open/.../player.ts:404`) and frees the player slot.
    2. Re-write Mongo from the recorded `seed_player` kwargs. The autosave
       just clobbered our intended state, so we replay the seed before the
       server reads from Mongo on the next login.
    3. Re-run the login flow via `__test_login`. login_impl already retries
       on the "already logged in" race (player slot still held), so we
       don't need a separate poll here.
    4. Verify the session is back to a healthy observe before returning.

    Raises on any step that doesn't complete cleanly. The caller in
    `mcp_session()` catches and falls back to a cold boot.
    """
    from bench.seed import get_last_seed_kwargs, seed_player

    handle = entry["handle"]
    started_at = _ts()
    phase_at = started_at

    def _mark(phase: str) -> None:
        nonlocal phase_at
        now = _ts()
        _harness_log(
            f"reseed[{username}] {phase}: +{now - phase_at:.2f}s "
            f"(total {now - started_at:.2f}s)"
        )
        phase_at = now

    _harness_log(f"reseed[{username}] start (warm pool reuse)")

    close_result = await handle.call_tool("__test_close_session", {})
    if close_result.is_error:
        raise RuntimeError(f"__test_close_session error: {close_result.text!r}")
    _mark("close_session_done")

    last_kwargs = get_last_seed_kwargs(username)
    if last_kwargs is None:
        raise RuntimeError(
            f"no recorded seed for {username!r}; live-suite mode requires the "
            f"test (or its fixture) to call seed_player(...) before mcp_session()"
        )
    # `seed_player` records the kwargs again on this call — idempotent.
    seed_player(username, **last_kwargs)
    _mark("reseed_mongo_done")

    login_result = await handle.call_tool("__test_login", {})
    if login_result.is_error or "FAILED" in login_result.text.upper():
        raise RuntimeError(f"__test_login failed: {login_result.text!r}")
    _mark("login_done")

    # Healthy-observe gate, mirrors the cold-path warmup loop.
    last_observe: ToolResult | None = None
    last_payload: dict | None = None
    healthy_attempts = 0
    for _ in range(20):
        healthy_attempts += 1
        last_observe = await handle.call_tool("observe", {})
        last_payload = last_observe.json()
        if (
            not last_observe.is_error
            and isinstance(last_payload, dict)
            and isinstance(last_payload.get("pos"), dict)
            and "x" in last_payload["pos"]
            and "y" in last_payload["pos"]
            and isinstance(last_payload.get("inventory"), list)
        ):
            _mark(f"healthy_observe attempts={healthy_attempts}")
            return
        await asyncio.sleep(0.5)
    raise RuntimeError(
        f"observe did not become healthy after relogin; last_observe="
        f"{(last_observe.text[:300] if last_observe else None)!r}"
    )


@asynccontextmanager
async def mcp_session(
    *,
    username: str,
    password: str = "test",
    client_url: str = "http://localhost:9000",
    server_port: str = "",
    headed: bool = False,
    state_dir: str | None = None,
    extra_env: dict[str, str] | None = None,
):
    """Spawn mcp_game_server as a stdio MCP subprocess scoped to `username`.

    Yields an object with `.call_tool(name, args) -> ToolResult` and
    `.list_tools() -> list[str]`. Cleanup closes the MCP session + kills
    the browser.

    Live-suite mode (`KAETRAM_LIVE_SUITE=1`): keeps the MCP subprocess +
    Chromium alive across tests in a module. On enter for an already-pooled
    username, closes the prior in-game session, replays the most recent
    `seed_player(...)` kwargs (the server's autosave on disconnect clobbers
    the seed; we restore it before the next login reads from Mongo), then
    runs the login flow against the warm browser. On exit, leaves the
    session in the pool — the reachability conftest drains it at module
    teardown via `_drain_warm_pool()`.

    On any reseed-reconnect failure the pool entry is torn down and the
    cold path runs, so a single bad test cannot poison the rest of the
    module.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    live = _live_mode()
    pool_key = username.lower()

    # ── Warm-pool fast path: borrow + reseed-reconnect ───────────────────
    if live and pool_key in _warm_pool:
        _harness_log(f"mcp_session[{username}] warm-pool hit")
        entry = _warm_pool[pool_key]
        try:
            await _reseed_and_reconnect(entry, username)
        except Exception as exc:  # noqa: BLE001 — fall back to cold boot on any failure
            _harness_log(
                f"mcp_session[{username}] live reseed-reconnect failed "
                f"({exc!r}); tearing down warm entry and falling back to cold path"
            )
            for closer in ("session", "transport"):
                obj = entry.get(closer)
                if obj is None:
                    continue
                try:
                    await obj.__aexit__(None, None, None)
                except Exception:
                    pass
            _warm_pool.pop(pool_key, None)
        else:
            try:
                yield entry["handle"]
            finally:
                # Leave the warm session in the pool; module teardown drains.
                pass
            return

    started_at = time.perf_counter()
    phase_at = started_at

    def mark(phase: str) -> None:
        nonlocal phase_at
        now = time.perf_counter()
        _harness_log(
            f"mcp_session[{username}] {phase}: +{now - phase_at:.2f}s "
            f"(total {now - started_at:.2f}s)"
        )
        phase_at = now

    game_ws_host = os.environ.get("GAME_WS_HOST", "localhost")
    # KAETRAM_PORT is the canonical name (set by tests/e2e/conftest.py to the
    # isolated test lane :9191). GAME_WS_PORT is a legacy fallback. Default
    # 9001 only applies when neither is set — that's the data-collection
    # lane, which uses kaetram_devlopment, not kaetram_e2e — so getting the
    # default here means seeded test players will fail to log in.
    game_ws_port_raw = (
        os.environ.get("KAETRAM_PORT")
        or os.environ.get("GAME_WS_PORT")
        or "9001"
    )
    try:
        game_ws_port = int(game_ws_port_raw)
    except ValueError:
        game_ws_port = 9001

    # Block before any test code runs until both the game socket and the web
    # client URL are reachable.
    await _wait_for_tcp(game_ws_host, game_ws_port, label="Game server")
    await _wait_for_client_url(client_url)
    mark("endpoints_ready")

    if state_dir is None:
        worker = os.environ.get("PYTEST_XDIST_WORKER", "single")
        state_dir = f"/tmp/kaetram_test_state/{worker}/{username}"

    # If the caller didn't pin server_port, preserve any KAETRAM_PORT already
    # in the inherited environment (conftest sets it to the isolated test
    # lane). An empty server_port would otherwise blank the inherited value
    # and the MCP subprocess would fall back to the data-collection lane.
    resolved_server_port = server_port or str(game_ws_port)
    env = {
        **os.environ,
        "KAETRAM_USERNAME": username,
        "KAETRAM_PASSWORD": password,
        "KAETRAM_CLIENT_URL": client_url,
        "KAETRAM_PORT": resolved_server_port,
        "KAETRAM_EXTRACTOR": str(STATE_EXTRACTOR),
        "KAETRAM_STATE_DIR": state_dir,
        # Respect KAETRAM_HEADED from the environment (set by server.js when
        # tests are launched from the dashboard with headed=true) unless the
        # caller explicitly requested headless.
        "KAETRAM_HEADED": "1" if headed else os.environ.get("KAETRAM_HEADED", "0"),
        # Conditionally register __test_close_session / __test_login in the
        # MCP subprocess. Always set in test runs so the tools are available
        # whether or not the suite is in live mode (cheap to register;
        # production agents don't go through this code path so model-visible
        # tool surface is unchanged for them).
        "KAETRAM_TEST_LANE": "1",
        **(extra_env or {}),
    }

    params = StdioServerParameters(
        command=str(VENV_PYTHON),
        args=[str(MCP_SERVER)],
        env=env,
    )
    transport = stdio_client(params)
    read, write = await transport.__aenter__()
    session = ClientSession(read, write, read_timeout_seconds=timedelta(seconds=120))
    await session.__aenter__()
    await session.initialize()
    mark("mcp_initialized")

    class _Handle:
        async def call_tool(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
            result = await session.call_tool(name, args or {})
            parts: list[str] = []
            for block in result.content or []:
                if hasattr(block, "text"):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            return ToolResult(is_error=bool(result.isError), text="\n".join(parts))

        async def list_tools(self) -> list[str]:
            res = await session.list_tools()
            return [t.name for t in res.tools]

    handle = _Handle()

    # Warm the browser + login path and require a usable observe result before
    # yielding to the test body. This prevents tests from racing the initial
    # MCP/browser/game bootstrap and failing in the first few seconds.
    last_observe: ToolResult | None = None
    last_payload: dict | None = None
    warmup_attempts = 0
    for _ in range(30):
        warmup_attempts += 1
        last_observe = await handle.call_tool("observe", {})
        last_payload = last_observe.json()
        if (
            not last_observe.is_error
            and isinstance(last_payload, dict)
            and isinstance(last_payload.get("pos"), dict)
            and "x" in last_payload["pos"]
            and "y" in last_payload["pos"]
            and isinstance(last_payload.get("inventory"), list)
        ):
            break
        await asyncio.sleep(0.5)
    else:
        text = last_observe.text[:500] if last_observe else "no observe response"
        raise RuntimeError(
            f"MCP session did not become ready after warmup observe retries. "
            f"last_observe={text!r} payload={last_payload!r}"
        )
    mark(f"warmup_ready attempts={warmup_attempts}")

    if live:
        # Register the freshly-created warm session in the pool so the next
        # test in this module can reuse it via the reseed-reconnect path.
        # Leave session/transport open; module teardown drains the pool.
        _harness_log(f"mcp_session[{username}] cold boot complete; registering in warm pool")
        _warm_pool[pool_key] = {
            "session": session,
            "transport": transport,
            "handle": handle,
        }
        try:
            yield handle
        finally:
            # Live mode: keep open. _drain_warm_pool() handles teardown.
            pass
        return

    try:
        yield handle
    finally:
        try: await session.__aexit__(None, None, None)
        except Exception: pass
        try: await transport.__aexit__(None, None, None)
        except Exception: pass
