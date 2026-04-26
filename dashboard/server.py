"""Server infrastructure: ThreadedHTTPServer, WebSocketRelay."""

import asyncio
import glob
import http.server
import json
import logging
import os
import threading
import time

import websockets

from dashboard.constants import WS_PORT
from dashboard.handler import DashboardHandler

logger = logging.getLogger(__name__)


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


class WebSocketRelay:
    """Manages WebSocket connections and broadcasts typed messages.

    Auto-restarts the asyncio server if it dies (port-bind failure, asyncio
    exception). A bad message or a client disconnect must not kill the relay
    for the lifetime of the dashboard.
    """

    def __init__(self, host="0.0.0.0", port=WS_PORT):
        self.host = host
        self.port = port
        self.connections = set()
        self._loop = None

    async def handler(self, websocket):
        self.connections.add(websocket)
        try:
            await websocket.send(json.dumps({"type": "connected", "ws_version": 2}))
            async for _ in websocket:
                pass  # drain client messages
        except websockets.ConnectionClosed:
            pass
        finally:
            self.connections.discard(websocket)

    # ── Typed broadcast primitive ──

    def _broadcast_typed(self, msg_type, payload):
        """Schedule a broadcast on the relay's event loop. Safe to call from
        any thread (uses run_coroutine_threadsafe). No-op if the loop is down."""
        if self._loop is None or self._loop.is_closed():
            return
        msg = json.dumps({"type": msg_type, **payload})
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)
        except RuntimeError as e:
            logger.debug("WS broadcast scheduling failed: %s", e)

    # ── Public, typed convenience methods ──

    def notify_state(self, agent_id, payload, ts=None):
        self._broadcast_typed(
            "state",
            {"agent": agent_id, "payload": payload, "ts": ts if ts is not None else time.time()},
        )

    def notify_activity(self, agent_id, events, ts=None):
        self._broadcast_typed(
            "activity",
            {"agent": agent_id, "events": events, "ts": ts if ts is not None else time.time()},
        )

    def notify_heartbeat(self, agents, ts=None):
        self._broadcast_typed(
            "heartbeat",
            {"agents": agents, "ts": ts if ts is not None else time.time()},
        )

    def notify_test_event(self, run_id, event, payload, ts=None):
        """Broadcast a pytest run event (start, runtest_*, session_finish, etc.).
        Consumed by the Tests tab via ws.onmessage `case 'test_event'`."""
        self._broadcast_typed(
            "test_event",
            {
                "run_id": run_id,
                "event": event,
                "payload": payload,
                "ts": ts if ts is not None else time.time(),
            },
        )

    def notify_restart(self, ts=None):
        """Tell every connected tab to drop local state and re-fetch.
        Fired by handle_restart_run after wiping caches so the UI catches up
        within a single RTT instead of waiting for the next refreshSlow tick."""
        self._broadcast_typed(
            "restart",
            {"ts": ts if ts is not None else time.time()},
        )

    async def _broadcast(self, message):
        if self.connections:
            websockets.broadcast(self.connections, message)

    async def _heartbeat_loop(self):
        """Periodically advertise WS health + currently-watched agent slots.

        Lets the frontend distinguish "watcher alive but agent gone" from
        "WS dead" — the latter shows no heartbeats at all.
        """
        while True:
            try:
                slots = sorted({
                    int(p.split(os.sep)[-3].replace("kaetram_agent_", ""))
                    for p in glob.glob("/tmp/kaetram_agent_*/state/game_state.json")
                })
            except Exception:
                slots = []
            self.notify_heartbeat(slots)
            await asyncio.sleep(5)

    async def _run(self):
        async with websockets.serve(
            self.handler, self.host, self.port,
            ping_interval=30, ping_timeout=10,
            # permessage-deflate: state/activity payloads are dense JSON with
            # high cross-tick redundancy. Compression cuts WAN bandwidth ~3-4×
            # over the slim payload at near-zero CPU cost on localhost.
            compression="deflate",
        ):
            # Heartbeat task lives for the lifetime of the server.
            asyncio.create_task(self._heartbeat_loop())
            await asyncio.Future()  # run forever

    def run_in_thread(self):
        def _thread_main():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            # Outer auto-restart: if _run raises (port bind failure, asyncio
            # exception), wait 1s and retry. Today this thread silently dies
            # and the dashboard falls back to HTTP polling forever.
            while True:
                try:
                    self._loop.run_until_complete(self._run())
                except Exception as e:
                    logger.warning("WebSocketRelay crashed: %s — restarting in 1s", e)
                    time.sleep(1)
        t = threading.Thread(target=_thread_main, daemon=True, name="ws-relay")
        t.start()
        return t


# ── Module-level singleton wiring ──
# Exposed so other dashboard modules (handler.py ingest endpoints) can push
# typed messages to all connected clients without reaching into start_dashboard.

_relay_singleton: WebSocketRelay | None = None


def get_relay() -> WebSocketRelay | None:
    """Return the running WebSocketRelay (or None before start_dashboard runs)."""
    return _relay_singleton


def start_dashboard():
    """Main entry point — start the dashboard server."""
    global _relay_singleton
    ws_relay = WebSocketRelay()
    _relay_singleton = ws_relay
    ws_relay.run_in_thread()

    http_port = int(os.environ.get("DASHBOARD_HTTP_PORT", "8080"))
    print(f"Dashboard running at http://0.0.0.0:{http_port}")
    print(f"WebSocket relay on ws://0.0.0.0:{WS_PORT}")
    server = ThreadedHTTPServer(("0.0.0.0", http_port), DashboardHandler)
    server.serve_forever()
