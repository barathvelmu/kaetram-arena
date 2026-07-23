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
import contextlib
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from finetune.render import patch_qwen_chat_template  # noqa: E402
from run_manifest import sha256_json, tool_schema_record  # noqa: E402
from scripts.fetch_hf_snapshot import (  # noqa: E402
    fetch_snapshot,
    load_lock,
    locked_snapshot_tree_sha256,
)
from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS  # noqa: E402


PINNED_MLX_LM_VERSION = "0.31.3"
SUPPORTED_MODELS = {
    "base_2b": "2b-base",
    "opd_r2_2b": "2b-opd-r2",
    "opd_r3_2b": "2b-opd-r3",
}
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
CANONICAL_TOKENIZER_SNAPSHOT = "base_2b"
TOKENIZER_RUNTIME_FILES = frozenset({
    "added_tokens.json",
    "chat_template.jinja",
    "merges.txt",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer.model",
    "tokenizer_config.json",
    "vocab.json",
})
LOCAL_RENDER_CONTRACT_SCHEMA = "kaetram.local-render-contract.v1"
RENDER_PROBE_STRINGS = (
    "isOpen",
    "waitForTimeout",
    "C'mon",
    "...///\n/a",
)
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
    snapshot_tree_sha256: str
    snapshot_lock_sha256: str
    tokenizer_sha256: str
    render_contract_sha256: str
    chat_template_sha256: str
    tokenizer_source_revision: str
    fix_mistral_regex: bool

    def health_payload(self) -> dict:
        return {
            "status": "ok",
            "attestation": {
                "deployment_id": self.deployment_id,
                "api_model": self.api_model,
                "checkpoint_sha256": self.checkpoint_sha256,
                "snapshot_tree_sha256": self.snapshot_tree_sha256,
                "snapshot_lock_sha256": self.snapshot_lock_sha256,
                "tokenizer_sha256": self.tokenizer_sha256,
                "render_contract_sha256": self.render_contract_sha256,
                "chat_template_sha256": self.chat_template_sha256,
                "tokenizer_source_revision": self.tokenizer_source_revision,
                "fix_mistral_regex": self.fix_mistral_regex,
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise LocalEndpointError(f"cannot hash runtime input: {path}") from exc
    return digest.hexdigest()


def load_patched_chat_template(canonical_tokenizer_dir: Path) -> str:
    """Load and patch the one canonical Qwen renderer without changing its files."""
    config_path = canonical_tokenizer_dir / "tokenizer_config.json"
    try:
        tokenizer_config = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalEndpointError(
            f"cannot read canonical tokenizer config: {config_path}"
        ) from exc
    configured = tokenizer_config.get("chat_template")
    template_path = canonical_tokenizer_dir / "chat_template.jinja"
    try:
        file_template = template_path.read_text() if template_path.exists() else None
    except OSError as exc:
        raise LocalEndpointError(
            f"cannot read canonical chat template: {template_path}"
        ) from exc
    if file_template is not None and configured is not None and file_template != configured:
        raise LocalEndpointError(
            "canonical tokenizer_config.json and chat_template.jinja disagree"
        )
    template = file_template if file_template is not None else configured
    if not isinstance(template, str) or not template:
        raise LocalEndpointError("canonical tokenizer has no chat template")
    holder = SimpleNamespace(chat_template=template)
    try:
        # This CLI's stdout is machine-readable JSON in --verify-only mode.
        with contextlib.redirect_stdout(io.StringIO()):
            patch_qwen_chat_template(holder)
    except RuntimeError as exc:
        raise LocalEndpointError(str(exc)) from exc
    return holder.chat_template


def build_render_contract(
    lock: dict,
    patched_chat_template: str,
    canonical_tokenizer_dir: Path,
    effective_renderer: dict,
) -> dict:
    """Describe every model-visible local rendering choice."""
    snapshot = lock["snapshots"][CANONICAL_TOKENIZER_SNAPSHOT]
    return {
        "schema_version": LOCAL_RENDER_CONTRACT_SCHEMA,
        "engine": "mlx-lm",
        "engine_version": PINNED_MLX_LM_VERSION,
        "tokenizer_snapshot": CANONICAL_TOKENIZER_SNAPSHOT,
        "tokenizer_repo_id": snapshot["repo_id"],
        "tokenizer_revision": snapshot["revision"],
        "tokenizer_sha256": _locked_sha256(snapshot, "tokenizer.json"),
        "tokenizer_config_sha256": _sha256_file(
            canonical_tokenizer_dir / "tokenizer_config.json"
        ),
        "chat_template_sha256": hashlib.sha256(
            patched_chat_template.encode("utf-8")
        ).hexdigest(),
        # Transformers 5.14 can misclassify checkpoint-local Qwen configs as
        # Mistral when transformers_version is absent. The training and
        # historical serving contract uses Qwen's original regex.
        "fix_mistral_regex": False,
        "tool_schema_sha256": tool_schema_record()["sha256"],
        "effective_renderer": effective_renderer,
    }


def build_identity(
    lock: dict,
    snapshot_name: str,
    api_model: str,
    render_contract: dict,
) -> EndpointIdentity:
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
    canonical_tokenizer = lock["snapshots"][CANONICAL_TOKENIZER_SNAPSHOT]
    tokenizer_sha256 = _locked_sha256(canonical_tokenizer, "tokenizer.json")
    revision = snapshot.get("revision", "")
    if not isinstance(revision, str) or len(revision) != 40:
        raise LocalEndpointError(f"{snapshot_name}: invalid locked revision")
    return EndpointIdentity(
        snapshot_name=snapshot_name,
        api_model=api_model,
        deployment_id=(
            f"local-mlx-lm-{PINNED_MLX_LM_VERSION}-"
            f"{snapshot_name}-{revision[:12]}-"
            f"{sha256_json(render_contract)[:12]}"
        ),
        checkpoint_sha256=checkpoint_sha256,
        snapshot_tree_sha256=locked_snapshot_tree_sha256(snapshot),
        snapshot_lock_sha256=lock["lock_sha256"],
        tokenizer_sha256=tokenizer_sha256,
        render_contract_sha256=sha256_json(render_contract),
        chat_template_sha256=render_contract["chat_template_sha256"],
        tokenizer_source_revision=render_contract["tokenizer_revision"],
        fix_mistral_regex=render_contract["fix_mistral_regex"],
    )


def build_runtime_view(
    model_dir: Path,
    canonical_tokenizer_dir: Path,
    destination: Path,
    patched_chat_template: str,
) -> Path:
    """Assemble a non-mutating view: arm weights plus one canonical tokenizer."""
    destination.mkdir(parents=True, exist_ok=False)
    for source in model_dir.iterdir():
        (destination / source.name).symlink_to(source, target_is_directory=source.is_dir())

    for filename in TOKENIZER_RUNTIME_FILES:
        target = destination / filename
        if target.is_symlink() or target.exists():
            target.unlink()
        source = canonical_tokenizer_dir / filename
        if source.exists():
            target.symlink_to(source)

    config_path = destination / "tokenizer_config.json"
    template_path = destination / "chat_template.jinja"
    for generated in (config_path, template_path):
        if generated.is_symlink() or generated.exists():
            generated.unlink()
    linked_config = canonical_tokenizer_dir / "tokenizer_config.json"
    try:
        tokenizer_config = json.loads(linked_config.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalEndpointError(
            f"cannot build canonical tokenizer runtime view from {linked_config}"
        ) from exc
    tokenizer_config["chat_template"] = patched_chat_template
    tokenizer_config["fix_mistral_regex"] = False
    config_path.write_text(
        json.dumps(tokenizer_config, ensure_ascii=False, sort_keys=True) + "\n"
    )
    template_path.write_text(patched_chat_template)
    return destination


def verify_effective_renderer(
    runtime_model_dir: Path,
    patched_chat_template: str,
) -> dict:
    """Exercise the exact derived tokenizer on tool history and drift probes."""
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise LocalEndpointError(
            "transformers is required to attest the effective local renderer"
        ) from exc
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            runtime_model_dir,
            local_files_only=True,
            fix_mistral_regex=False,
        )
    except (OSError, ValueError, TypeError) as exc:
        raise LocalEndpointError(
            f"cannot load effective tokenizer from {runtime_model_dir}"
        ) from exc
    actual_template = tokenizer.chat_template
    if actual_template != patched_chat_template:
        raise LocalEndpointError(
            "effective tokenizer chat template differs from the canonical patch"
        )

    messages = [
        {"role": "system", "content": "You are the registered render probe."},
        {"role": "user", "content": "Inspect the game state."},
        {
            "role": "assistant",
            "reasoning_content": "I should observe before acting.",
            "content": "",
            "tool_calls": [{
                "type": "function",
                "function": {"name": "observe", "arguments": {}},
            }],
        },
        {
            "role": "tool",
            "content": json.dumps({
                "position": {"x": 328, "y": 892},
                "isOpen": True,
                "waitForTimeout": False,
                "dialogue": "C'mon",
            }, separators=(",", ":")),
        },
        {"role": "user", "content": "Continue."},
    ]
    try:
        rendered = tokenizer.apply_chat_template(
            messages,
            tools=MODEL_VISIBLE_TOOL_DEFINITIONS,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=True,
        )
        token_ids = tokenizer.encode(rendered, add_special_tokens=False)
        probe_ids = {
            probe: tokenizer.encode(probe, add_special_tokens=False)
            for probe in RENDER_PROBE_STRINGS
        }
    except (ValueError, TypeError, RuntimeError) as exc:
        raise LocalEndpointError("effective renderer probe failed") from exc
    return {
        "tokenizer_class": type(tokenizer).__name__,
        "vocab_size": len(tokenizer),
        "rendered_text_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "rendered_token_ids_sha256": sha256_json(token_ids),
        "rendered_token_count": len(token_ids),
        "probe_token_ids_sha256": sha256_json(probe_ids),
    }


def build_backend_command(
    python: str,
    model_dir: Path,
    host: str,
    port: int,
    patched_chat_template: str,
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
        "--chat-template",
        patched_chat_template,
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
    runtime_view_context = None
    try:
        require_loopback(args.host)
        require_loopback(args.backend_host)
        require_mlx_runtime()
        lock = load_lock(args.lock)
        model_dir = (args.snapshots_root / args.snapshot).resolve()
        canonical_tokenizer_dir = (
            args.snapshots_root / CANONICAL_TOKENIZER_SNAPSHOT
        ).resolve()
        fetch_snapshot(lock["snapshots"][args.snapshot], model_dir, verify_only=True)
        if args.snapshot != CANONICAL_TOKENIZER_SNAPSHOT:
            fetch_snapshot(
                lock["snapshots"][CANONICAL_TOKENIZER_SNAPSHOT],
                canonical_tokenizer_dir,
                verify_only=True,
            )
        patched_chat_template = load_patched_chat_template(canonical_tokenizer_dir)
        runtime_view_context = tempfile.TemporaryDirectory(
            prefix=f"kaetram-{args.snapshot}-runtime-"
        )
        runtime_model_dir = build_runtime_view(
            model_dir,
            canonical_tokenizer_dir,
            Path(runtime_view_context.name) / "model",
            patched_chat_template,
        )
        effective_renderer = verify_effective_renderer(
            runtime_model_dir, patched_chat_template
        )
        render_contract = build_render_contract(
            lock,
            patched_chat_template,
            canonical_tokenizer_dir,
            effective_renderer,
        )
        identity = build_identity(
            lock, args.snapshot, args.api_model, render_contract
        )
        if args.verify_only:
            print(json.dumps({
                **identity.health_payload(),
                "render_contract": render_contract,
            }, indent=2, sort_keys=True))
            return 0

        backend_url = f"http://{args.backend_host}:{args.backend_port}"
        command = build_backend_command(
            sys.executable,
            runtime_model_dir,
            args.backend_host,
            args.backend_port,
            patched_chat_template,
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
        if runtime_view_context is not None:
            runtime_view_context.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
