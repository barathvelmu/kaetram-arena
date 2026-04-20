"""State-based polling for Playwright tests.

Use `wait_for_state` instead of `asyncio.sleep(N)`. The predicate runs
against a fresh `observe_via_browser(page)` snapshot; returning truthy
resolves, returning falsy keeps polling.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from playwright.async_api import Page

from .observe import observe_via_browser

Predicate = Callable[[dict[str, Any]], bool]


class StateTimeout(AssertionError):
    def __init__(self, last_snapshot: dict[str, Any] | None, elapsed: float):
        super().__init__(
            f"wait_for_state timed out after {elapsed:.1f}s; last snapshot: {last_snapshot!r}"
        )
        self.last_snapshot = last_snapshot


async def wait_for_state(
    page: Page,
    predicate: Predicate,
    *,
    timeout: float = 10.0,
    interval: float = 0.25,
) -> dict[str, Any]:
    """Poll `observe_via_browser` every `interval` until `predicate(snapshot)` is
    truthy. Returns the matching snapshot. Raises `StateTimeout` on timeout."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    start = time.monotonic()

    while time.monotonic() < deadline:
        snapshot = await observe_via_browser(page)
        last = snapshot
        try:
            if predicate(snapshot):
                return snapshot
        except (KeyError, TypeError, AttributeError):
            pass
        await asyncio.sleep(interval)

    raise StateTimeout(last, time.monotonic() - start)


async def wait_for_value(
    page: Page,
    accessor: Callable[[dict[str, Any]], Any],
    expected: Any,
    *,
    timeout: float = 10.0,
    interval: float = 0.25,
) -> dict[str, Any]:
    """Convenience wrapper: wait until `accessor(snapshot) == expected`."""
    return await wait_for_state(
        page, lambda s: accessor(s) == expected, timeout=timeout, interval=interval
    )
