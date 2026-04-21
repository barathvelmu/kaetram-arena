"""LLM endpoint resolution — auto-detect Modal vs Ollama vs none.

Routing priority:
  1. Explicit env: LLM_ENDPOINT + LLM_MODEL
  2. MODAL_TOKEN_ID present → arena's Modal serve endpoint (Qwen3.5-9B)
  3. Ollama reachable at localhost:11434 → local Qwen3.5-4B
  4. None available → tests that need an LLM skip cleanly

Tests call `resolve_llm_endpoint()` and either receive `(base_url, model)`
or get a pytest.skip reason they can pass through.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass

OLLAMA_DEFAULT_HOST = "127.0.0.1"
OLLAMA_DEFAULT_PORT = 11434
OLLAMA_DEFAULT_MODEL = "qwen3.5:4b-16k"

MODAL_DEFAULT_ENDPOINT = (
    "https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1"
)
MODAL_DEFAULT_MODEL = "kaetram"


@dataclass
class LLMEndpoint:
    base_url: str
    model: str
    provider: str  # "modal" | "ollama" | "explicit"
    temperature: float = 0.0
    seed: int = 42


def _tcp_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_llm_endpoint() -> LLMEndpoint | None:
    """Return a routed endpoint or None if nothing is available.

    Callers translate None into a pytest.skip."""
    # 1. Explicit override
    if os.environ.get("LLM_ENDPOINT") and os.environ.get("LLM_MODEL"):
        return LLMEndpoint(
            base_url=os.environ["LLM_ENDPOINT"],
            model=os.environ["LLM_MODEL"],
            provider="explicit",
        )

    # 2. Modal — detect via token ID (arena's canonical check)
    if os.environ.get("MODAL_TOKEN_ID"):
        return LLMEndpoint(
            base_url=os.environ.get("MODAL_ENDPOINT", MODAL_DEFAULT_ENDPOINT),
            model=os.environ.get("MODAL_MODEL", MODAL_DEFAULT_MODEL),
            provider="modal",
        )

    # 3. Ollama local
    if _tcp_open(OLLAMA_DEFAULT_HOST, OLLAMA_DEFAULT_PORT):
        # Prefer the proxy (port 11435) if it's up — fixes Qwen3.5 think:false
        proxy_up = _tcp_open(OLLAMA_DEFAULT_HOST, 11435)
        port = 11435 if proxy_up else OLLAMA_DEFAULT_PORT
        return LLMEndpoint(
            base_url=f"http://{OLLAMA_DEFAULT_HOST}:{port}/v1",
            model=os.environ.get("OLLAMA_MODEL", OLLAMA_DEFAULT_MODEL),
            provider="ollama",
        )

    # 4. Nothing available
    return None


def skip_if_no_llm():
    """pytest helper — `llm = skip_if_no_llm()` at test start.

    Raises pytest.skip cleanly if no endpoint resolvable.
    """
    import pytest
    ep = resolve_llm_endpoint()
    if ep is None:
        pytest.skip(
            "No LLM endpoint available: set LLM_ENDPOINT+LLM_MODEL env vars, "
            "or ensure Ollama is running on :11434, or set MODAL_TOKEN_ID."
        )
    return ep
