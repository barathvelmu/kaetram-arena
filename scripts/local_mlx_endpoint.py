#!/usr/bin/env python3
"""Run one hash-verified Kaetram checkpoint behind a loopback-only API.

The process starts MLX-LM on an internal loopback port, then exposes a small
OpenAI-compatible gateway on another loopback port.  The gateway:

* publishes the immutable ``/health`` identity required by factorial_eval.py;
* accepts the reviewed scientific model name while translating it to
  MLX-LM's built-in ``default_model`` alias; and
* never binds either listener to a non-loopback interface.

No network service or paid endpoint is involved after the public snapshot has
been downloaded.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from run_manifest import tool_schema_record  # noqa: E402
from scripts.fetch_hf_snapshot import fetch_snapshot, load_lock  # noqa: E402


PINNED_MLX_LM_VERSION = "0.31.3"
SUPPORTED_MODELS = {
    "base_2b": "2b-base",
    "opd_r2_2b": "2b-opd-r2",
    "opd_r3_2b": "2b-opd-r3",
}
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class LocalEndpointError(RuntimeError):
    """Raised when local inference cannot satisfy the reviewed contract."""


@dataclass(frozen=True)
class EndpointIdentity:
    snapshot_name: str
    api_model: str
    deployment_id: str
    checkpoint_sha256: str
    tokenizer_sha256: str
    render_contract_sha256: str

    def health_payload(self) -> dict:
        return {
            "status": "ok",
            "attestation": {
                "deployment_id": self.deployment_id,
                "api_model": self.api_model,
                "checkpoint_sha256": self.checkpoint_sha256,
                "tokenizer_sha256": self.tokenizer_sha256,
                "render_contract_sha256": self.render_contract_sha256,
            },
        }


def require_loopback(host: str) -> None:
    if host not in LOOPBACK_HOSTS:
        raise LocalEndpointError(
            f"refusing non-loopback host {host!r}; local model endpoints must stay private"
        )


def require_mlx_runtime() -> str:
    try:
        installed = version("mlx-lm")
    except PackageNotFoundError as exc:
        raise LocalEndpointError(
            f"mlx-lm=={PINNED_MLX_LM_VERSION} is required in the active Python environment"
        ) from exc
    if installed != PINNED_MLX_LM_VERSION:
        raise LocalEndpointError(
            f"mlx-lm version mismatch: expected {PINNED_MLX_LM_VERSION}, got {installed}"
        )
    return installed


def _locked_sha256(snapshot: dict, relative_path: str) -> str:
    matches = [
        record
        for record in snapshot["files"]
        if record.get("path") == relative_path and isinstance(record.get("sha256"), str)
    ]
    if len(matches) != 1:
        raise LocalEndpointError(
            f"{relative_path}: require exactly one SHA-256-identified locked file"
        )
    return matches[0]["sha256"]


def build_identity(lock: dict, snapshot_name: str, api_model: str) -> EndpointIdentity:
    if snapshot_name not in SUPPORTED_MODELS:
        raise LocalEndpointError(f"unsupported local evaluation snapshot: {snapshot_name}")
    expected_api_model = SUPPORTED_MODELS[snapshot_name]
    if api_model != expected_api_model:
        raise LocalEndpointError(
            f"{snapshot_name} must use reviewed API model {expected_api_model!r}"
        )
    snapshot = lock["snapshots"][snapshot_name]
    top_level_weights = [
        record
        for record in snapshot["files"]
        if "/" not in record["path"]
        and record["path"].endswith(".safetensors")
        and isinstance(record.get("sha256"), str)
    ]
    if len(top_level_weights) != 1:
        raise LocalEndpointError(
            f"{snapshot_name}: expected one top-level SHA-256-identified weights file"
        )
    checkpoint_sha256 = top_level_weights[0]["sha256"]
    tokenizer_sha256 = _locked_sha256(snapshot, "tokenizer.json")
    revision = snapshot.get("revision", "")
    if not isinstance(revision, str) or len(revision) != 40:
        raise LocalEndpointError(f"{snapshot_name}: invalid locked revision")
    return EndpointIdentity(
        snapshot_name=snapshot_name,
        api_model=api_model,
        deployment_id=(
            f"local-mlx-lm-{PINNED_MLX_LM_VERSION}-"
            f"{snapshot_name}-{revision[:12]}"
        ),
        checkpoint_sha256=checkpoint_sha256,
        tokenizer_sha256=tokenizer_sha256,
        render_contract_sha256=tool_schema_record()["sha256"],
    )


def build_backend_command(
    python: str,
    model_dir: Path,
    host: str,
    port: int,
) -> list[str]:
    require_loopback(host)
    return [
        python,
        "-m",
        "mlx_lm",
        "server",
        "--model",
        str(model_dir),
        "--host",
        host,
        "--port",
        str(port),
        "--prompt-cache-size",
        "1",
        "--chat-template-args",
        '{"enable_thinking":true}',
        "--log-level",
        "INFO",
    ]


def normalize_mlx_tool_arguments(messages: object) -> None:
    """Adapt Arena's Qwen history shape to MLX-LM's OpenAI wire contract.

    Arena deliberately retains historical function arguments as mappings:
    Qwen's model-visible chat template iterates those mappings directly, which
    matches training.  MLX-LM's HTTP server first applies ``json.loads`` to
    every historical argument value, so its wire representation must instead
    be a JSON-object string.  MLX decodes the string before rendering, leaving
    the model-visible mapping unchanged.
    """
    if not isinstance(messages, list):
        return
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        tool_calls = message.get("tool_calls")
        if tool_calls is None:
            continue
        if not isinstance(tool_calls, list):
            continue
        for call_index, tool_call in enumerate(tool_calls):
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            path = (
                f"messages[{message_index}].tool_calls[{call_index}]"
                ".function.arguments"
            )
            arguments = function.get("arguments")
            if isinstance(arguments, dict):
                try:
                    function["arguments"] = json.dumps(
                        arguments,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                except (TypeError, ValueError) as exc:
                    raise LocalEndpointError(
                        f"{path} must be a JSON object or JSON-object string"
                    ) from exc
                continue
            if isinstance(arguments, str) and arguments:
                try:
                    decoded = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise LocalEndpointError(
                        f"{path} must be a JSON object or JSON-object string"
                    ) from exc
                if isinstance(decoded, dict):
                    # Preserve already-valid strings byte-for-byte. Re-encoding
                    # would change prompt order/spacing or double-encode them.
                    continue
            raise LocalEndpointError(
                f"{path} must be a JSON object or JSON-object string"
            )


def rewrite_chat_request(body: bytes, api_model: str) -> bytes:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalEndpointError("request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise LocalEndpointError("request body must be a JSON object")
    if payload.get("model") != api_model:
        raise LocalEndpointError(
            f"request model must be the attested API model {api_model!r}"
        )
    normalize_mlx_tool_arguments(payload.get("messages"))
    payload["model"] = "default_model"
    return json.dumps(payload, separators=(",", ":")).encode()


def rewrite_chat_response(body: bytes, api_model: str) -> bytes:
    """Restore the public alias and the harness's historical think-block shape."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return body
    if not isinstance(payload, dict):
        return body
    payload["model"] = api_model
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            message = choice.get("message") if isinstance(choice, dict) else None
            if not isinstance(message, dict):
                continue
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            content = message.get("content") or ""
            if isinstance(reasoning, str) and reasoning:
                message["content"] = f"<think>{reasoning}</think>{content}"
    return json.dumps(payload, separators=(",", ":")).encode()


def _backend_ready(url: str, timeout_seconds: float = 180.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not contacted"
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{url}/v1/models", timeout=2) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = type(exc).__name__
        time.sleep(0.25)
    raise LocalEndpointError(f"MLX-LM backend did not become ready: {last_error}")


def make_handler(
    identity: EndpointIdentity,
    backend_url: str,
) -> type[BaseHTTPRequestHandler]:
    health_body = json.dumps(identity.health_payload(), sort_keys=True).encode()

    class GatewayHandler(BaseHTTPRequestHandler):
        server_version = "KaetramLocalMLX/1"

        def _send_json(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path == "/health":
                try:
                    with urlopen(f"{backend_url}/v1/models", timeout=2) as response:
                        if response.status != 200:
                            raise LocalEndpointError(
                                f"backend health returned HTTP {response.status}"
                            )
                except (HTTPError, URLError, TimeoutError, OSError, LocalEndpointError):
                    self._send_json(
                        503,
                        json.dumps({
                            "status": "unavailable",
                            "error": "local MLX-LM backend is not healthy",
                        }).encode(),
                    )
                    return
                self._send_json(200, health_body)
                return
            if self.path == "/v1/models":
                body = json.dumps({
                    "object": "list",
                    "data": [{
                        "id": identity.api_model,
                        "object": "model",
                        "owned_by": "local-hash-verified",
                    }],
                }).encode()
                self._send_json(200, body)
                return
            self._send_json(404, b'{"error":"not found"}')

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != "/v1/chat/completions":
                self._send_json(404, b'{"error":"not found"}')
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0:
                    raise LocalEndpointError("request body is empty")
                body = rewrite_chat_request(
                    self.rfile.read(content_length), identity.api_model
                )
                headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower() not in HOP_BY_HOP_HEADERS
                    and key.lower() not in {"host", "content-length"}
                }
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(body))
                request = Request(
                    f"{backend_url}/v1/chat/completions",
                    data=body,
                    headers=headers,
                    method="POST",
                )
                with urlopen(request, timeout=360) as response:
                    response_body = rewrite_chat_response(
                        response.read(), identity.api_model
                    )
                    self.send_response(response.status)
                    for key, value in response.headers.items():
                        if key.lower() not in HOP_BY_HOP_HEADERS \
                                and key.lower() != "content-length":
                            self.send_header(key, value)
                    self.send_header("Content-Length", str(len(response_body)))
                    self.end_headers()
                    self.wfile.write(response_body)
            except LocalEndpointError as exc:
                self._send_json(
                    400,
                    json.dumps({"error": str(exc)}, sort_keys=True).encode(),
                )
            except HTTPError as exc:
                response_body = exc.read()
                self._send_json(exc.code, response_body)
            except (URLError, TimeoutError, OSError) as exc:
                self._send_json(
                    502,
                    json.dumps(
                        {"error": f"local MLX-LM backend unavailable: {type(exc).__name__}"}
                    ).encode(),
                )

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"[gateway] {self.address_string()} {fmt % args}", file=sys.stderr)

    return GatewayHandler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", choices=sorted(SUPPORTED_MODELS), required=True)
    parser.add_argument("--api-model", required=True)
    parser.add_argument("--snapshots-root", type=Path, required=True)
    parser.add_argument(
        "--lock",
        type=Path,
        default=REPO / "research/experiments/provenance/public-hf-snapshots.lock.json",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--backend-host", default="127.0.0.1")
    parser.add_argument("--backend-port", type=int, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--startup-timeout-seconds", type=float, default=180.0)
    args = parser.parse_args(argv)

    backend = None
    server = None
    try:
        require_loopback(args.host)
        require_loopback(args.backend_host)
        require_mlx_runtime()
        lock = load_lock(args.lock)
        identity = build_identity(lock, args.snapshot, args.api_model)
        model_dir = (args.snapshots_root / args.snapshot).resolve()
        fetch_snapshot(lock["snapshots"][args.snapshot], model_dir, verify_only=True)
        if args.verify_only:
            print(json.dumps(identity.health_payload(), indent=2, sort_keys=True))
            return 0

        backend_url = f"http://{args.backend_host}:{args.backend_port}"
        command = build_backend_command(
            sys.executable, model_dir, args.backend_host, args.backend_port
        )
        backend = subprocess.Popen(command, start_new_session=True)
        _backend_ready(backend_url, args.startup_timeout_seconds)
        if backend.poll() is not None:
            raise LocalEndpointError(
                f"MLX-LM backend exited during startup with code {backend.returncode}"
            )

        server = ThreadingHTTPServer(
            (args.host, args.port),
            make_handler(identity, backend_url),
        )

        def stop(_signum: int, _frame: object) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        print(
            json.dumps({
                "endpoint": f"http://{args.host}:{args.port}/v1",
                **identity.health_payload(),
            }, sort_keys=True),
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    except (LocalEndpointError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if server is not None:
            server.server_close()
        if backend is not None and backend.poll() is None:
            os.killpg(backend.pid, signal.SIGTERM)
            try:
                backend.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(backend.pid, signal.SIGKILL)
                backend.wait()


if __name__ == "__main__":
    raise SystemExit(main())
