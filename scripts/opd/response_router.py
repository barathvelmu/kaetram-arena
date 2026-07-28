#!/usr/bin/env python3
"""Strict, schema-aware routing policy for content-only tool calls."""

from __future__ import annotations

import re
from typing import Any

from scripts.opd.canonicalize import recover_tool_calls
from tool_surface import validate_tool_call_arguments


_OPEN = re.compile(r"<tool_call>", re.IGNORECASE)
_CLOSE = re.compile(r"</tool_call>", re.IGNORECASE)
_BLOCK = re.compile(r"<tool_call>.*?</tool_call>", re.IGNORECASE | re.DOTALL)


def route_content_tool_call(content: Any) -> dict:
    """Return a deterministic fail-closed decision for ordinary-text content.

    Promotion requires exactly one explicit, closed ``tool_call`` envelope,
    exactly one parser-recoverable candidate, and a valid call under the frozen
    model-visible schema. Anything ambiguous or malformed is quarantined.
    """

    if not isinstance(content, str) or not content:
        return {"status": "no_candidate", "calls": [], "reason": "empty_content"}
    recovered = recover_tool_calls(content)
    if not recovered:
        return {"status": "no_candidate", "calls": [], "reason": "not_recoverable"}
    blocks = _BLOCK.findall(content)
    if len(_OPEN.findall(content)) != 1 or len(_CLOSE.findall(content)) != 1:
        return {
            "status": "quarantined",
            "calls": [],
            "reason": "invalid_tool_call_envelope",
        }
    if len(blocks) != 1:
        return {
            "status": "quarantined",
            "calls": [],
            "reason": "ambiguous_tool_call_envelope",
        }
    if len(recovered) != 1:
        return {
            "status": "quarantined",
            "calls": [],
            "reason": "candidate_count_not_one",
        }
    candidate = recovered[0]
    valid, reason = validate_tool_call_arguments(
        candidate.get("name"), candidate.get("args")
    )
    if not valid:
        return {"status": "quarantined", "calls": [], "reason": reason}
    return {"status": "promoted", "calls": recovered, "reason": "valid"}
