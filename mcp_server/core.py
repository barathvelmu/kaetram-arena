"""Core MCP server setup: FastMCP instance, lifespan, browser management, logging.

This module owns the singleton ``mcp`` instance that tool modules decorate.
"""

import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time as _time
from contextlib import asynccontextmanager

from mcp.server.fastmcp import Context, FastMCP
from playwright.async_api import async_playwright

# ── Logging ──────────────────────────────────────────────────────────────────

_MCP_START = _time.time()
_MCP_TOOL_COUNTS: dict[str, int] = {}
_MCP_ERROR_COUNTS: dict[str, int] = {}
_MCP_LOG_FILE = None
_DIAGNOSTIC_OWNER_FILENAME = "diagnostic-mcp-owner.json"
_DIAGNOSTIC_BROWSER_OWNER_FILENAME = "diagnostic-browser-owner.json"
_DIAGNOSTIC_SESSION_RE = re.compile(r"[a-z0-9-]{8,80}")


def _init_log_file():
    """Open a persistent log file for MCP diagnostics."""
    global _MCP_LOG_FILE
    state_dir = os.environ.get("KAETRAM_STATE_DIR", "/tmp")
    log_path = os.path.join(state_dir, "mcp_server.log")
    try:
        os.makedirs(state_dir, exist_ok=True)
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


def _debug_enabled() -> bool:
    """Enable verbose per-call tool logging when KAETRAM_DEBUG=1. Temporary
    diagnostic aid for reachability tests — keep OFF in production."""
    return os.environ.get("KAETRAM_DEBUG", "0").lower() not in ("0", "false", "", "no")


def log_tool(name: str, success: bool = True, error: str = "", args: dict | None = None):
    _MCP_TOOL_COUNTS[name] = _MCP_TOOL_COUNTS.get(name, 0) + 1
    if not success:
        _MCP_ERROR_COUNTS[name] = _MCP_ERROR_COUNTS.get(name, 0) + 1
        log(f"[tool] {name} FAILED ({_MCP_ERROR_COUNTS[name]} errors): {error[:200]}")
    elif _debug_enabled():
        # KAETRAM_DEBUG=1 → log every call with args preview
        args_str = ""
        if args:
            try:
                args_str = " " + json.dumps(args, default=str)[:200]
            except (TypeError, ValueError):
                args_str = f" {args!r}"[:200]
        log(f"[tool] {name} #{_MCP_TOOL_COUNTS[name]}{args_str}")
    elif _MCP_TOOL_COUNTS[name] <= 3 or _MCP_TOOL_COUNTS[name] % 25 == 0:
        # Default: log first 3 calls of each tool + every 25th as heartbeat
        log(f"[tool] {name} #{_MCP_TOOL_COUNTS[name]}")
    # Periodic stats dump every 50 total calls
    total = sum(_MCP_TOOL_COUNTS.values())
    if total % 50 == 0:
        log_stats()


def log_tool_result(name: str, result: str | dict | None, *, max_preview: int = 300):
    """Log a tool's return payload when KAETRAM_DEBUG=1. Preview-truncates
    long payloads."""
    if not _debug_enabled() or result is None:
        return
    if isinstance(result, str):
        preview = result.replace("\n", " ")[:max_preview]
    else:
        try:
            preview = json.dumps(result, default=str)[:max_preview]
        except (TypeError, ValueError):
            preview = str(result)[:max_preview]
    log(f"[tool] {name} -> {preview}")


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


def _publish_canonical_create_only(
    state_dir: str, filename: str, raw: bytes, *, label: str
) -> None:
    """Atomically link a fully synced private temp file into its final name."""

    final_path = os.path.join(state_dir, filename)
    temp_path = os.path.join(
        state_dir,
        f".{filename}.tmp-{os.getpid()}-{secrets.token_hex(8)}",
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temp_path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_path, final_path, follow_symlinks=False)
        directory_fd = os.open(state_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise RuntimeError(f"{label} ownership receipt creation failed") from exc
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def _publish_diagnostic_owner(state: dict) -> None:
    """Publish the exact detached MCP group before any browser can launch."""

    if os.environ.get("KAETRAM_DIAGNOSTIC_LANE") != "1":
        return
    state_dir = os.environ.get("KAETRAM_STATE_DIR")
    session_id = os.environ.get("KAETRAM_DIAGNOSTIC_SESSION_ID")
    pid = state.get("mcp_pid")
    process_group = state.get("mcp_process_group")
    nonce = state.get("mcp_instance_nonce")
    if (
        not isinstance(state_dir, str)
        or not os.path.isabs(state_dir)
        or os.path.islink(state_dir)
        or not os.path.isdir(state_dir)
        or not isinstance(session_id, str)
        or _DIAGNOSTIC_SESSION_RE.fullmatch(session_id) is None
        or type(pid) is not int
        or pid <= 0
        or process_group != pid
        or not isinstance(nonce, str)
        or re.fullmatch(r"[0-9a-f]{32}", nonce) is None
    ):
        raise RuntimeError("diagnostic MCP ownership identity is unsafe")
    payload = {
        "schema_version": "kaetram.diagnostic-mcp-owner.v1",
        "session_id": session_id,
        "mcp_pid": pid,
        "mcp_process_group": process_group,
        "mcp_instance_nonce": nonce,
    }
    raw = (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    _publish_canonical_create_only(
        state_dir,
        _DIAGNOSTIC_OWNER_FILENAME,
        raw,
        label="diagnostic MCP",
    )


def _diagnostic_browser_process_identity(session_id: str) -> tuple[int, int]:
    """Resolve the one detached Chromium leader carrying our unique launch tag."""

    token = f"--kaetram-diagnostic-session={session_id}"
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,pgid=,command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("diagnostic browser process discovery failed") from exc
    rows: list[tuple[int, int]] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3 or token not in fields[2].split():
            continue
        try:
            pid, process_group = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        if pid <= 0 or process_group <= 0:
            raise RuntimeError("diagnostic browser process identity is unsafe")
        rows.append((pid, process_group))
    groups = {process_group for _, process_group in rows}
    if len(groups) != 1:
        raise RuntimeError("diagnostic browser process group is not unique")
    process_group = next(iter(groups))
    if not any(pid == process_group for pid, _ in rows):
        raise RuntimeError("diagnostic browser group leader is not observable")
    return process_group, process_group


def _publish_diagnostic_browser_owner(state: dict) -> None:
    """Publish the browser group bound to the launch nonce and MCP owner."""

    if os.environ.get("KAETRAM_DIAGNOSTIC_LANE") != "1":
        return
    state_dir = os.environ.get("KAETRAM_STATE_DIR")
    session_id = os.environ.get("KAETRAM_DIAGNOSTIC_SESSION_ID")
    payload = {
        "schema_version": "kaetram.diagnostic-browser-owner.v1",
        "session_id": session_id,
        "mcp_pid": state.get("mcp_pid"),
        "mcp_process_group": state.get("mcp_process_group"),
        "mcp_instance_nonce": state.get("mcp_instance_nonce"),
        "browser_pid": state.get("browser_pid"),
        "browser_process_group": state.get("browser_process_group"),
        "browser_launch_nonce": state.get("browser_launch_nonce"),
        "browser_executable_sha256": state.get("browser_executable_sha256"),
    }
    if (
        not isinstance(state_dir, str)
        or not os.path.isabs(state_dir)
        or os.path.islink(state_dir)
        or not os.path.isdir(state_dir)
        or not isinstance(session_id, str)
        or _DIAGNOSTIC_SESSION_RE.fullmatch(session_id) is None
        or type(payload["mcp_pid"]) is not int
        or payload["mcp_pid"] <= 0
        or payload["mcp_process_group"] != payload["mcp_pid"]
        or not isinstance(payload["mcp_instance_nonce"], str)
        or re.fullmatch(r"[0-9a-f]{32}", payload["mcp_instance_nonce"]) is None
        or type(payload["browser_pid"]) is not int
        or payload["browser_pid"] <= 0
        or payload["browser_process_group"] != payload["browser_pid"]
        or not isinstance(payload["browser_launch_nonce"], str)
        or re.fullmatch(r"[0-9a-f]{32}", payload["browser_launch_nonce"]) is None
        or not isinstance(payload["browser_executable_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", payload["browser_executable_sha256"])
        is None
    ):
        raise RuntimeError("diagnostic browser ownership identity is unsafe")
    raw = (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    _publish_canonical_create_only(
        state_dir,
        _DIAGNOSTIC_BROWSER_OWNER_FILENAME,
        raw,
        label="diagnostic browser",
    )


# Initialize log file on import
_init_log_file()


# ── Browser lifespan (lazy — yields immediately, launches browser on first use) ─

@asynccontextmanager
async def game_lifespan(server: FastMCP):
    """Yield immediately so MCP handshake completes fast. Browser launches lazily."""
    state = {
        "page": None, "browser": None, "pw": None,
        "logged_in": False, "_lock": asyncio.Lock(),
        "_heartbeat_tasks": [],
        "mcp_instance_nonce": secrets.token_hex(16),
        "mcp_pid": os.getpid(),
        "mcp_process_group": os.getpgrp(),
        "browser_pid": None,
        "browser_process_group": None,
        "browser_launch_nonce": None,
        "browser_executable_sha256": None,
        "browser_version": None,
    }
    _publish_diagnostic_owner(state)
    log("[mcp] Server ready (browser will launch on first tool call)")
    try:
        yield state
    finally:
        log_stats()
        # Cancel heartbeat tasks first so they stop poking the (about-to-die) page.
        tasks = state.get("_heartbeat_tasks") or []
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
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

        diagnostic_loopback_only = os.environ.get(
            "KAETRAM_DIAGNOSTIC_LOOPBACK_ONLY", ""
        ).lower() in ("1", "true", "yes")
        port = os.environ.get("KAETRAM_PORT", "")
        if diagnostic_loopback_only and port != "9191":
            raise RuntimeError(
                "diagnostic browser policy requires KAETRAM_PORT=9191"
            )

        log("[mcp] Launching browser...")
        pw = await async_playwright().start()
        headed = os.environ.get("KAETRAM_HEADED", "").lower() in ("1", "true", "yes")
        # Frame the Xvfb capture on the game canvas, not on Chrome's chrome.
        # The strategy is to make Chrome fill the full Xvfb display (1280x810),
        # then have ffmpeg crop the top 90px of browser chrome back out when
        # building the HLS stream. Net visible frame = 1280x720 of pure game.
        # - (0,0) position + 1280x810 size → no off-screen overflow, no black padding.
        # - --disable-infobars + --hide-scrollbars kills the in-page UI noise.
        chrome_args = [
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--window-position=0,0",
            "--window-size=1280,810",
            "--disable-features=TranslateUI,BlinkGenPropertyTrees",
            "--disable-infobars",
            "--hide-scrollbars",
        ]
        if diagnostic_loopback_only:
            diagnostic_session_id = os.environ.get(
                "KAETRAM_DIAGNOSTIC_SESSION_ID", ""
            )
            if _DIAGNOSTIC_SESSION_RE.fullmatch(diagnostic_session_id) is None:
                raise RuntimeError("diagnostic browser session identity is unsafe")
            chrome_args.extend(
                [
                    "--disable-background-networking",
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                    f"--kaetram-diagnostic-session={diagnostic_session_id}",
                ]
            )
        # Pass DISPLAY through so headed Chromium can attach to the per-agent
        # Xvfb display when orchestrate.py sets DISPLAY=:99+N. In pure
        # headless mode DISPLAY is ignored.
        launch_env = {**os.environ}
        if headed and "DISPLAY" not in launch_env:
            launch_env["DISPLAY"] = ":0"
        browser = await pw.chromium.launch(
            headless=not headed,
            args=chrome_args,
            env=launch_env,
        )
        state["browser"] = browser
        state["pw"] = pw
        browser_launch_nonce = secrets.token_hex(16)
        executable_path = pw.chromium.executable_path
        executable_sha256 = None
        try:
            digest = hashlib.sha256()
            with open(executable_path, "rb") as executable:
                for chunk in iter(lambda: executable.read(1024 * 1024), b""):
                    digest.update(chunk)
            executable_sha256 = digest.hexdigest()
        except OSError:
            pass
        state["browser_launch_nonce"] = browser_launch_nonce
        state["browser_executable_sha256"] = executable_sha256
        state["browser_version"] = browser.version
        if diagnostic_loopback_only:
            browser_pid, browser_group = _diagnostic_browser_process_identity(
                diagnostic_session_id
            )
            state["browser_pid"] = browser_pid
            state["browser_process_group"] = browser_group
            _publish_diagnostic_browser_owner(state)
        context_options = {"viewport": {"width": 1280, "height": 720}}
        if diagnostic_loopback_only:
            context_options["service_workers"] = "block"
        context = await browser.new_context(**context_options)

        if diagnostic_loopback_only:
            await context.add_init_script(
                f"Object.defineProperty(window, '__kaetramDiagnosticNonce', "
                f"{{value: {json.dumps(browser_launch_nonce)}, writable: false}});"
            )

        if diagnostic_loopback_only:
            async def route_loopback_only(route):
                from ipaddress import ip_address
                from urllib.parse import urlsplit

                parsed = urlsplit(route.request.url)
                if parsed.scheme in ("data", "blob", "about"):
                    await route.continue_()
                    return
                try:
                    host = ip_address(parsed.hostname or "")
                except ValueError:
                    await route.abort()
                    return
                if (
                    parsed.scheme in ("http", "https")
                    and host.is_loopback
                    and parsed.port == 9000
                ):
                    await route.continue_()
                    return
                await route.abort()

            await context.route("**/*", route_loopback_only)
            await context.add_init_script("""(() => {
                const _WS = window.WebSocket;
                window.WebSocket = function(url, protocols) {
                    const parsed = new URL(url, window.location.href);
                    if (parsed.protocol !== 'ws:' || parsed.username || parsed.password) {
                        throw new Error('diagnostic blocked invalid WebSocket URL');
                    }
                    // The attested client bundle is built with 0.0.0.0:9001.
                    // Pin both coordinates here so it can reach only the
                    // registered diagnostic lane, independent of init-script
                    // ordering or the client bundle's configured endpoint.
                    parsed.hostname = '127.0.0.1';
                    parsed.port = '9191';
                    return protocols ? new _WS(parsed.href, protocols) : new _WS(parsed.href);
                };
                window.WebSocket.prototype = _WS.prototype;
                window.WebSocket.CONNECTING = 0; window.WebSocket.OPEN = 1;
                window.WebSocket.CLOSING = 2; window.WebSocket.CLOSED = 3;
            })()""")
            log("[mcp] Diagnostic loopback-only browser policy enabled")

        # Inject state_extractor.js (survives page reloads/navigation)
        extractor_path = os.environ.get("KAETRAM_EXTRACTOR", "state_extractor.js")
        if os.path.exists(extractor_path):
            await context.add_init_script(path=extractor_path)
            log(f"[mcp] Injected {extractor_path}")

        # WebSocket port override for multi-agent isolation
        if port and not diagnostic_loopback_only:
            await context.add_init_script(f"""(() => {{
                const PORT = '{port}';
                const _WS = window.WebSocket;
                window.WebSocket = function(url, protocols) {{
                    // Rewrite whatever port the client emits (today always
                    // :9001 from .env.defaults; defensive against future
                    // Kaetram default-port changes). Hostname is preserved so
                    // dual-VM setups (game server on one host, agent browser
                    // on another) connect correctly.
                    url = url.replace(/:\\d+(?=\\/|$)/, ':' + PORT);
                    return protocols ? new _WS(url, protocols) : new _WS(url);
                }};
                window.WebSocket.prototype = _WS.prototype;
                window.WebSocket.CONNECTING = 0; window.WebSocket.OPEN = 1;
                window.WebSocket.CLOSING = 2; window.WebSocket.CLOSED = 3;
            }})()""")
            log(f"[mcp] WebSocket port override: {port}")

        page = await context.new_page()

        async def on_console(msg):
            if "[debug_test]" in msg.text or "[debug_npc]" in msg.text:
                log(f"[browser] {msg.text}")

        page.on("console", on_console)

        # Log page crashes and WebSocket closures
        page.on("crash", lambda: log("[mcp] PAGE CRASHED — browser tab died"))
        page.on("close", lambda: log("[mcp] PAGE CLOSED — browser tab was closed"))

        state["page"] = page

        # Start the dashboard heartbeats once. They run for the lifetime of
        # the MCP server and are best-effort — never crash the agent.
        # Handles are tracked in state["_heartbeat_tasks"] so the lifespan
        # finally block can cancel them cleanly on shutdown.
        heartbeats_disabled = os.environ.get(
            "KAETRAM_DISABLE_HEARTBEATS", ""
        ).lower() in ("1", "true", "yes")
        if heartbeats_disabled:
            log("[mcp] Dashboard heartbeats disabled by launch contract")
        elif not state.get("_heartbeats_started"):
            try:
                from mcp_server.state_heartbeat import (
                    state_heartbeat_loop, activity_heartbeat_loop,
                )
                state["_heartbeat_tasks"].extend([
                    asyncio.create_task(state_heartbeat_loop(state)),
                    asyncio.create_task(activity_heartbeat_loop(state)),
                ])
                state["_heartbeats_started"] = True
                log("[mcp] Dashboard heartbeats started")
            except Exception as e:
                log(f"[mcp] heartbeat start failed: {e}")

        log("[mcp] Browser ready")
        return page


async def _page_in_game(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """() => (
                    document.body &&
                    document.body.className === 'game' &&
                    typeof window.__extractGameState === 'function' &&
                    !!(window.game && window.game.player)
                )"""
            )
        )
    except Exception:
        return False


async def get_page(ctx: Context, ensure_logged_in: bool = True):
    """Get the Playwright page, launching browser if needed."""
    from mcp_server.login import login_impl

    state = ctx.request_context.lifespan_context
    page = await _ensure_browser(state)
    if not ensure_logged_in:
        return page

    if state.get("logged_in") and await _page_in_game(page):
        return page

    login_result = await login_impl(ctx, page)
    if "FAILED" in login_result.upper():
        raise RuntimeError(login_result)
    return page


# ── FastMCP instance ─────────────────────────────────────────────────────────

mcp = FastMCP("kaetram", lifespan=game_lifespan)
