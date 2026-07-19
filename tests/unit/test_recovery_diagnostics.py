"""Unit tests for offline recovery and copy-prior diagnostics."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "opd"))

from copy_prior_diag import (  # noqa: E402
    context_conditions,
    parse_endpoint,
    render_context,
    restore_args,
    sha256_text,
    summarize,
    target_stats,
)
from recovery_audit import audit_logs  # noqa: E402


def test_restore_args_does_not_mutate_logged_turn() -> None:
    turn = SimpleNamespace(
        text='<function=gather("Oak")></function>',
        thinking="",
        tool_calls=[{
            "type": "function",
            "function": {"name": "gather", "arguments": {}},
        }],
    )

    repaired = restore_args(turn)

    assert repaired is not None
    assert repaired["tool_calls"][0]["function"]["arguments"] == {
        "resource_name": "Oak"
    }
    assert turn.tool_calls[0]["function"]["arguments"] == {}


def test_parse_endpoint_and_hash_are_deterministic() -> None:
    assert parse_endpoint("teacher=https://example.test/v1/") == (
        "teacher", "https://example.test/v1"
    )
    assert sha256_text("same") == sha256_text("same")
    with pytest.raises(Exception):
        parse_endpoint("missing-url")


def test_context_conditions_change_only_requested_context_parts() -> None:
    state = {
        "messages_real": [
            {"role": "system", "content": "Use gather(resource_name)."},
            {"role": "user", "content": "play"},
            {"role": "assistant", "content": "real"},
        ],
        "messages_repaired": [
            {"role": "system", "content": "Use gather(resource_name)."},
            {"role": "user", "content": "play"},
            {"role": "assistant", "content": "repaired"},
        ],
    }
    conditions = context_conditions(state)
    assert conditions["real"][2]["content"] == "real"
    assert conditions["history_repaired"][2]["content"] == "repaired"
    assert "[params:" in conditions["docs_repaired"][0]["content"]
    assert conditions["docs_repaired"][2]["content"] == "real"
    assert conditions["history_and_docs_repaired"][2]["content"] == "repaired"


def test_render_context_preserves_historical_none_and_supports_canonical_schema() -> None:
    class Tokenizer:
        def __init__(self):
            self.kwargs = []

        def apply_chat_template(self, messages, **kwargs):
            self.kwargs.append(kwargs)
            return "rendered"

    tokenizer = Tokenizer()
    messages = [{"role": "user", "content": "play"}]
    assert render_context(tokenizer, messages, "none") == "rendered"
    assert "tools" not in tokenizer.kwargs[-1]
    assert render_context(tokenizer, messages, "canonical") == "rendered"
    assert tokenizer.kwargs[-1]["tools"]
    with pytest.raises(ValueError, match="unsupported tool schema"):
        render_context(tokenizer, messages, "live")


def test_target_stats_and_summary_ignore_failed_scores() -> None:
    assert target_stats({"target_logprobs": [-1.0, None, -3.0]}) == (-4.0, -2.0, 2)
    rows = [
        {"state_id": "a", "endpoint": "t", "context_condition": "real", "candidate": "malformed",
         "mean_target_logprob": -2.0},
        {"state_id": "b", "endpoint": "t", "context_condition": "real", "candidate": "malformed",
         "mean_target_logprob": -4.0},
        {"state_id": "a", "endpoint": "t", "context_condition": "real", "candidate": "canonical",
         "mean_target_logprob": -1.0},
        {"state_id": "b", "endpoint": "t", "context_condition": "real", "candidate": "canonical",
         "mean_target_logprob": None},
    ]
    grouped = summarize(rows)
    assert grouped["groups"]["t/real/malformed"]["n"] == 2
    assert grouped["groups"]["t/real/malformed"]["median_target_logprob"] == -3.0
    assert grouped["paired_effects"]["t/real/canonical_minus_malformed"] == {
        "n": 1, "mean": 1.0, "median": 1.0,
    }


def test_recovery_audit_counts_recovered_execution_and_repeat_proxy(tmp_path: Path) -> None:
    log = tmp_path / "session_1_test.log"
    records = [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": '<function=gather("Oak")>'},
        ]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "r1", "name": "gather", "input": {"resource_name": "Oak"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r1", "content": "[format] fixed\n\n{\"ok\": true}"},
        ]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "r2", "name": "gather", "input": {"resource_name": "Oak"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r2", "content": "[format] fixed\n\n{\"error\": \"NO_RESOURCE\"}"},
        ]}},
    ]
    log.write_text("".join(json.dumps(record) + "\n" for record in records))

    report = audit_logs([log], relapse_window=2)
    assert report["totals"]["malformed_emissions"] == 1
    assert report["totals"]["recovered_calls"] == 2
    assert report["totals"]["recovered_execution_errors"] == 1
    assert report["totals"]["repeat_recoveries_within_window"] == 1
    assert report["recovered_by_tool"] == {"gather": 2}
