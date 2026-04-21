"""Playwright browser helpers for the isolated Kaetram lane.

Each test opens its own browser context against :19100 and logs in as a
pre-seeded player. `login_seeded_player` returns the Page handle once the
game has fully initialized (`window.game.player.gridX` is set).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

DEFAULT_CLIENT_URL = "http://127.0.0.1:19100"
DEFAULT_PASSWORD = "test"
LOGIN_TIMEOUT_MS = 60_000


async def _debug_shot(page: Page, label: str) -> None:
    base = os.environ.get("KAETRAM_DEBUG_SCREENSHOTS")
    if not base:
        return
    out = Path(base)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{label}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
        print(f"[debug] screenshot {label} → {path}")
    except Exception as exc:
        print(f"[debug] screenshot {label} failed: {exc}")


async def login_seeded_player(
    page: Page,
    username: str,
    *,
    password: str = DEFAULT_PASSWORD,
    client_url: str = DEFAULT_CLIENT_URL,
) -> Page:
    await page.goto(client_url, wait_until="domcontentloaded")
    await _debug_shot(page, f"01_post_goto_{username}")

    # The login button is enabled once `App.ready()` runs
    # (packages/client/src/app.ts:206). It's actually enabled immediately on
    # this build, but other builds have gated it behind map loading.
    await page.wait_for_function(
        "() => { const b = document.querySelector('#login'); return !!b && !b.disabled; }",
        timeout=LOGIN_TIMEOUT_MS,
    )
    await _debug_shot(page, f"02_login_enabled_{username}")

    await page.locator("#login-name-input").fill(username)
    await page.locator("#login-password-input").fill(password)
    await _debug_shot(page, f"03_fields_filled_{username}")
    await page.locator("#login").click()
    await page.wait_for_timeout(2000)
    await _debug_shot(page, f"04_post_click_{username}")

    try:
        # Success signal: body class flips from 'intro' to 'game' once the
        # world has loaded for the logged-in player. `#health` is always in the
        # DOM so it's not a reliable discriminator. This mirrors the check in
        # `~/projects/kaetram-agent/mcp_game_server.py:229`.
        await page.wait_for_function(
            "() => document.body.className === 'game'",
            timeout=LOGIN_TIMEOUT_MS,
        )
        await page.wait_for_function(
            "() => !!(window.game && window.game.player && window.game.player.name "
            "&& typeof window.game.player.gridX === 'number' && window.game.player.gridX > 0)",
            timeout=LOGIN_TIMEOUT_MS,
        )
    except Exception:
        await _debug_shot(page, f"99_timeout_{username}")
        state = await page.evaluate(
            """() => ({
                bodyClass: document.body ? document.body.className : null,
                loadCharVisible: (() => {
                    const el = document.getElementById('load-character');
                    if (!el) return false;
                    return window.getComputedStyle(el).opacity !== '0';
                })(),
                errors: Array.from(document.querySelectorAll('.validation-error, .error-message, [class*="error"]'))
                    .map(e => e.textContent && e.textContent.trim()).filter(Boolean).slice(0, 10),
                loginVisible: !!document.querySelector('#login-name-input'),
                registerVisible: (() => {
                    const el = document.getElementById('create-character');
                    return el ? window.getComputedStyle(el).opacity !== '0' : false;
                })(),
                worldSelectVisible: (() => {
                    const el = document.getElementById('world-select');
                    return el ? window.getComputedStyle(el).opacity !== '0' : false;
                })(),
                hasGame: !!(window.game && window.game.player),
                socketConnected: !!(window.game && window.game.socket && window.game.socket.connected),
            })"""
        )
        print(f"[debug] timeout state for {username}: {state}")
        raise

    await _debug_shot(page, f"05_in_game_{username}")
    return page


@asynccontextmanager
async def browser_session(
    *,
    headless: bool = True,
    viewport: dict[str, int] | None = None,
) -> AsyncIterator[tuple[Browser, BrowserContext, Page]]:
    """Full Playwright stack as a context manager. Caller still owns login."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        try:
            context = await browser.new_context(
                viewport=viewport or {"width": 1280, "height": 800}
            )
            page = await context.new_page()
            try:
                yield browser, context, page
            finally:
                await context.close()
        finally:
            await browser.close()
