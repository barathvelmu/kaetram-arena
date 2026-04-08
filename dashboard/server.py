"""Server infrastructure: ThreadedHTTPServer, WebSocketRelay, ScreenshotWatcher."""

import asyncio
import http.server
import json
import os
import threading
import time

import websockets

from dashboard.constants import STATE_DIR, MAX_AGENTS, WS_PORT, SCREENSHOT_POLL_INTERVAL
from dashboard.handler import DashboardHandler


class ThreadedHTTPServer(http.server.HTTPServer):
    def process_request(self, request, client_address):
        thread = threading.Thread(target=self._handle, args=(request, client_address))
        thread.daemon = True
        thread.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass
        finally:
            self.shutdown_request(request)


class ScreenshotWatcher:
    """Polls screenshot file mtimes and calls a callback on change."""

    def __init__(self, on_change):
        """on_change(agent_id, filepath, mtime) — agent_id is None for single-agent, int for multi."""
        self.on_change = on_change
        self._mtimes = {}
        self._running = False
        self._active_paths = []
        self._path_refresh_counter = 0

    def _refresh_watch_paths(self):
        """Build list of paths that actually exist (avoid wasted stat() on missing files)."""
        paths = []
        for name in ("live_screen.png", "screenshot.png"):
            p = os.path.join(STATE_DIR, name)
            if os.path.isfile(p):
                paths.append((None, p))
        for i in range(MAX_AGENTS):
            for name in ("live_screen.png", "screenshot.png"):
                p = os.path.join("/tmp", f"kaetram_agent_{i}", "state", name)
                if os.path.isfile(p):
                    paths.append((i, p))
        self._active_paths = paths

    def run(self):
        self._running = True
        self._refresh_watch_paths()
        while self._running:
            # Re-discover paths every 30 cycles (~30s) to pick up new agents
            self._path_refresh_counter += 1
            if self._path_refresh_counter >= 30:
                self._refresh_watch_paths()
                self._path_refresh_counter = 0

            for agent_id, filepath in self._active_paths:
                try:
                    mtime = os.path.getmtime(filepath)
                    prev = self._mtimes.get(filepath)
                    if prev is None:
                        self._mtimes[filepath] = mtime
                    elif mtime != prev:
                        self._mtimes[filepath] = mtime
                        self.on_change(agent_id, filepath, mtime)
                except OSError:
                    pass
            time.sleep(SCREENSHOT_POLL_INTERVAL)

    def stop(self):
        self._running = False


class WebSocketRelay:
    """Manages WebSocket connections and broadcasts screenshot notifications."""

    def __init__(self, host="0.0.0.0", port=WS_PORT):
        self.host = host
        self.port = port
        self.connections = set()
        self._loop = None

    async def handler(self, websocket):
        self.connections.add(websocket)
        try:
            await websocket.send(json.dumps({"type": "connected", "ws_version": 1}))
            async for _ in websocket:
                pass  # drain client messages
        except websockets.ConnectionClosed:
            pass
        finally:
            self.connections.discard(websocket)

    def notify_screenshot(self, agent_id, mtime):
        if self._loop is None or self._loop.is_closed():
            return
        msg = json.dumps({"type": "screenshot", "agent": agent_id, "ts": mtime})
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    async def _broadcast(self, message):
        if self.connections:
            websockets.broadcast(self.connections, message)

    async def _run(self):
        async with websockets.serve(
            self.handler, self.host, self.port,
            ping_interval=30, ping_timeout=10, compression=None,
        ):
            await asyncio.Future()  # run forever

    def run_in_thread(self):
        def _thread_main():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._run())
            except Exception:
                pass
        t = threading.Thread(target=_thread_main, daemon=True, name="ws-relay")
        t.start()
        return t


def start_dashboard():
    """Main entry point — start the dashboard server."""
    ws_relay = WebSocketRelay()
    ws_relay.run_in_thread()

    watcher = ScreenshotWatcher(
        on_change=lambda aid, fp, mt: ws_relay.notify_screenshot(aid, mt)
    )
    watcher_thread = threading.Thread(target=watcher.run, daemon=True, name="screenshot-watcher")
    watcher_thread.start()

    print(f"Dashboard running at http://0.0.0.0:8080")
    print(f"WebSocket relay on ws://0.0.0.0:{WS_PORT}")
    server = ThreadedHTTPServer(("0.0.0.0", 8080), DashboardHandler)
    server.serve_forever()
