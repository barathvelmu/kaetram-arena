from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from run_manifest import sha256_json, tool_schema_record
from scripts.build_hf_snapshot_lock import SCHEMA_VERSION
from scripts.local_mlx_endpoint import (
    LocalEndpointError,
    PINNED_MLX_LM_VERSION,
    build_backend_command,
    build_identity,
    normalize_mlx_tool_arguments,
    require_loopback,
    rewrite_chat_request,
    rewrite_chat_response,
)


def _lock() -> dict:
    snapshot = {
        "repo_type": "model",
        "repo_id": "owner/model",
        "revision": "a" * 40,
        "file_count": 2,
        "size_bytes": 7,
        "files": [
            {
                "path": "model.safetensors",
                "size_bytes": 3,
                "sha256": hashlib.sha256(b"abc").hexdigest(),
            },
            {
                "path": "tokenizer.json",
                "size_bytes": 4,
                "sha256": hashlib.sha256(b"tokn").hexdigest(),
            },
        ],
    }
    lock = {
        "schema_version": SCHEMA_VERSION,
        "source": "https://huggingface.co",
        "snapshots": {"base_2b": snapshot},
    }
    lock["lock_sha256"] = sha256_json(lock)
    return lock


def test_identity_is_derived_only_from_locked_artifacts() -> None:
    identity = build_identity(_lock(), "base_2b", "2b-base")
    assert identity.deployment_id == (
        f"local-mlx-lm-{PINNED_MLX_LM_VERSION}-base_2b-" + "a" * 12
    )
    assert identity.checkpoint_sha256 == hashlib.sha256(b"abc").hexdigest()
    assert identity.tokenizer_sha256 == hashlib.sha256(b"tokn").hexdigest()
    assert identity.render_contract_sha256 == tool_schema_record()["sha256"]
    assert identity.health_payload()["attestation"]["api_model"] == "2b-base"


def test_identity_rejects_scientific_alias_drift() -> None:
    with pytest.raises(LocalEndpointError, match="reviewed API model"):
        build_identity(_lock(), "base_2b", "wrong-name")


def test_request_rewrite_preserves_payload_and_hides_backend_path() -> None:
    source = {
        "model": "2b-base",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "observe"}}],
        "temperature": 0.6,
    }
    rewritten = json.loads(
        rewrite_chat_request(json.dumps(source).encode(), "2b-base")
    )
    assert rewritten == {**source, "model": "default_model"}

    with pytest.raises(LocalEndpointError, match="attested API model"):
        rewrite_chat_request(b'{"model":"2b-opd-r2"}', "2b-base")


def test_request_rewrite_normalizes_multi_turn_tool_history_for_mlx() -> None:
    observe_args = {}
    warp_args = {"location": "müdwich", "options": {"safe": True}}
    spaced_args = '{  "x": 188, "y": 157 }'
    source = {
        "model": "2b-base",
        "messages": [
            {"role": "system", "content": "play"},
            {
                "role": "assistant",
                "tool_calls": [{
                    "type": "function",
                    "function": {"name": "observe", "arguments": observe_args},
                }],
            },
            {"role": "tool", "content": '{"pos":{"x":328,"y":892}}'},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {"name": "warp", "arguments": warp_args},
                    },
                    {
                        "type": "function",
                        "function": {"name": "navigate", "arguments": spaced_args},
                    },
                ],
            },
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "warp",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        "seed": 17,
    }

    rewritten = json.loads(
        rewrite_chat_request(json.dumps(source).encode(), "2b-base")
    )
    calls = [
        call
        for message in rewritten["messages"]
        for call in message.get("tool_calls", [])
    ]
    assert json.loads(calls[0]["function"]["arguments"]) == observe_args
    assert json.loads(calls[1]["function"]["arguments"]) == warp_args
    assert calls[2]["function"]["arguments"] == spaced_args
    assert rewritten["tools"] == source["tools"]
    assert rewritten["messages"][2] == source["messages"][2]
    assert rewritten["seed"] == 17


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        [],
        1,
        True,
        "",
        "{broken",
        "[]",
        "null",
        "true",
    ],
)
def test_mlx_history_rejects_non_object_tool_arguments(arguments: object) -> None:
    messages = [{
        "role": "assistant",
        "tool_calls": [{
            "type": "function",
            "function": {"name": "observe", "arguments": arguments},
        }],
    }]
    with pytest.raises(
        LocalEndpointError,
        match=r"messages\[0\]\.tool_calls\[0\]\.function\.arguments",
    ):
        normalize_mlx_tool_arguments(messages)


def test_mlx_history_rejects_non_json_mapping_values() -> None:
    messages = [{
        "role": "assistant",
        "tool_calls": [{
            "type": "function",
            "function": {"name": "navigate", "arguments": {"x": float("nan")}},
        }],
    }]
    with pytest.raises(LocalEndpointError, match="JSON object"):
        normalize_mlx_tool_arguments(messages)


def test_response_rewrite_restores_alias_and_thinking_shape() -> None:
    source = {
        "model": "default_model",
        "choices": [{
            "message": {
                "reasoning": "I should observe.",
                "content": "\n",
                "tool_calls": [{"function": {"name": "observe", "arguments": "{}"}}],
            }
        }],
    }
    rewritten = json.loads(
        rewrite_chat_response(json.dumps(source).encode(), "2b-base")
    )
    assert rewritten["model"] == "2b-base"
    assert rewritten["choices"][0]["message"]["content"] == (
        "<think>I should observe.</think>\n"
    )
    assert rewritten["choices"][0]["message"]["tool_calls"] == (
        source["choices"][0]["message"]["tool_calls"]
    )


def test_loopback_is_mandatory_for_both_listeners() -> None:
    for host in ("127.0.0.1", "::1", "localhost"):
        require_loopback(host)
    with pytest.raises(LocalEndpointError, match="non-loopback"):
        require_loopback("0.0.0.0")


def test_backend_command_is_pinned_to_local_snapshot(tmp_path: Path) -> None:
    command = build_backend_command(
        "/venv/bin/python", tmp_path / "base_2b", "127.0.0.1", 8082
    )
    assert command[:4] == ["/venv/bin/python", "-m", "mlx_lm", "server"]
    assert command[command.index("--model") + 1] == str(tmp_path / "base_2b")
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "8082"
    assert '{"enable_thinking":true}' in command
