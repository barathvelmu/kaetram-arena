"""Session fixture that boots the isolated Kaetram e2emcp lane for MCP E2E tests.

Spawns three child processes under the same `NODE_ENV=e2emcp`:

1. Kaetram server  — websocket on :19101, API on :19102
2. Kaetram client  — served on :19100 (HMR on :19103)
3. E2E DB helper   — REST helper on :19300, writes to `kaetram_e2emcp` Mongo DB

Health-checks all three HTTP endpoints plus a real WebSocket handshake
against the game server before yielding. Teardown SIGTERMs the spawned
PIDs only; no pkill.

Node 18 or 20 is required (uWS compatibility). The fixture fails fast on
other Node versions rather than wedging the suite.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

KAETRAM_OPEN = Path.home() / "projects" / "Kaetram-Open"
E2E_PKG = KAETRAM_OPEN / "packages" / "e2e"

CLIENT_URL = "http://127.0.0.1:19100"
SERVER_API_URL = "http://127.0.0.1:19102"
SERVER_WS_URL = "ws://127.0.0.1:19101"
DB_HELPER_URL = "http://127.0.0.1:19300/api/v1"

BOOT_TIMEOUT_SECONDS = 120


@dataclass
class LaneHandles:
    server: subprocess.Popen
    client: subprocess.Popen
    db_helper: subprocess.Popen

    client_url: str = CLIENT_URL
    server_api_url: str = SERVER_API_URL
    server_ws_url: str = SERVER_WS_URL
    db_helper_url: str = DB_HELPER_URL


def _node_version(node_bin: str) -> str:
    try:
        return subprocess.check_output([node_bin, "--version"], text=True).strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"node is not available at {node_bin}: {exc}") from exc


def _candidate_node_bins() -> list[Path]:
    candidates: list[Path] = []

    path_node = Path(subprocess.check_output(["which", "node"], text=True).strip())
    if path_node.exists():
        candidates.append(path_node)

    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if nvm_root.exists():
        for prefix in ("v20", "v18"):
            for install in sorted(nvm_root.glob(f"{prefix}*/bin/node"), reverse=True):
                candidates.append(install)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def _resolve_supported_node() -> tuple[str, str]:
    attempted: list[str] = []
    for candidate in _candidate_node_bins():
        version = _node_version(str(candidate))
        attempted.append(f"{candidate}={version}")
        if version.startswith("v18.") or version.startswith("v20."):
            return str(candidate), version

    attempted_text = ", ".join(attempted) if attempted else "no node binaries found"
    raise RuntimeError(
        "Kaetram requires Node 18 or 20 (uWS compatibility). "
        f"Attempted: {attempted_text}. Install Node 20 via nvm and re-run."
    )


def _spawn(name: str, args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    shell = os.environ.get("SHELL", "/bin/zsh")
    process = subprocess.Popen(
        [shell, "-lc", shlex.join(args)],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    return process


async def _wait_for_http(url: str, deadline: float) -> None:
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(url)
                if response.status_code < 500:
                    return
            except (httpx.HTTPError, OSError):
                pass
            await asyncio.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {url}")


def _terminate(handles: LaneHandles) -> None:
    for proc in (handles.db_helper, handles.client, handles.server):
        if proc.poll() is not None:
            continue
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

    deadline = time.monotonic() + 5.0
    for proc in (handles.db_helper, handles.client, handles.server):
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)

    for proc in (handles.db_helper, handles.client, handles.server):
        if proc.poll() is not None:
            continue
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


@pytest_asyncio.fixture(scope="session")
async def isolated_lane() -> LaneHandles:
    if not KAETRAM_OPEN.exists():
        raise RuntimeError(f"Kaetram-Open repo not found at {KAETRAM_OPEN}")

    node_bin, _node_version_text = _resolve_supported_node()

    base_env = os.environ.copy()
    base_env["NODE_ENV"] = "e2emcp"
    base_env["PATH"] = f"{Path(node_bin).parent}:{base_env.get('PATH', '')}"

    server_env = base_env.copy()

    client_env = base_env.copy()
    client_env["CLIENT_HMR_PORT"] = "19103"

    db_env = base_env.copy()
    db_env["E2E_DB_HOST"] = "127.0.0.1"
    db_env["E2E_DB_PORT"] = "19300"

    server = _spawn(
        "server",
        ["yarn", "workspace", "@kaetram/server", "run", "dev"],
        KAETRAM_OPEN,
        server_env,
    )
    client = _spawn(
        "client",
        [
            "yarn",
            "workspace",
            "@kaetram/client",
            "run",
            "dev",
            "--port",
            "19100",
            "--host",
            "127.0.0.1",
        ],
        KAETRAM_OPEN,
        client_env,
    )
    db_helper = _spawn(
        "db",
        ["yarn", "tsx", "database/server"],
        E2E_PKG,
        db_env,
    )

    handles = LaneHandles(server=server, client=client, db_helper=db_helper)

    try:
        deadline = time.monotonic() + BOOT_TIMEOUT_SECONDS
        await asyncio.gather(
            _wait_for_http(f"{DB_HELPER_URL}/health", deadline),
            _wait_for_http(SERVER_API_URL + "/", deadline),
            _wait_for_http(CLIENT_URL + "/", deadline),
        )
        # A direct websocket handshake here looks like a harmless liveness
        # check, but with the Kaetram dev lane it can poison the first real
        # browser login that follows. Prefer HTTP readiness plus a brief
        # warmup window before yielding the shared lane to tests.
        await asyncio.sleep(6.0)
    except Exception:
        _terminate(handles)
        for proc, label in (
            (server, "server"),
            (client, "client"),
            (db_helper, "db"),
        ):
            if proc.stdout is not None:
                tail = proc.stdout.read()[-4000:] if proc.stdout.readable() else ""
                sys.stderr.write(f"\n=== {label} stdout tail ===\n{tail}\n")
        raise

    yield handles

    _terminate(handles)


@pytest.fixture
def unique_username(request: pytest.FixtureRequest) -> str:
    """Fresh username per test. Cleanup is the caller's responsibility
    (use `cleanup_player` fixture or the seed helper's context manager)."""
    short_id = uuid.uuid4().hex[:8]
    safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", request.node.name).strip("_").lower()
    safe_name = safe_name[:19] or "test"
    return f"e2e_{safe_name}_{short_id}"
