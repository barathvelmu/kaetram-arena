#!/usr/bin/env python3
"""Fail-closed, at-most-once evidence for one MCP tool invocation.

This module deliberately separates parser/schema acceptance, MCP transport,
and immediate observed state.  It does not infer semantic appropriateness,
durable persistence, quest utility, or model quality.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from tool_surface import (
    MODEL_VISIBLE_TOOL_EFFECT_CLASSES,
    MODEL_VISIBLE_TOOL_SCHEMA_SHA256,
    TOOL_EFFECT_CLASS_VERSION,
    validate_tool_call_arguments,
)


SCHEMA_VERSION = "kaetram.tool-execution-evidence.v1"


@dataclass(frozen=True)
class TransportResult:
    """Lossless portion of an MCP CallToolResult needed by the audit layer."""

    text: str
    is_error: bool = False


class ToolNotAttemptedError(RuntimeError):
    """The client proved that no request was sent to MCP."""


CallTool = Callable[[str, dict[str, Any]], Awaitable[TransportResult]]
ObserveImmediate = Callable[[], Awaitable[TransportResult]]


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def parse_tool_result_json(
    text: Any,
    *,
    expected_name: str | None = None,
) -> dict[str, Any] | None:
    """Parse the JSON payload from a tool result without accepting prose."""

    if not isinstance(text, str):
        return None
    candidate = text.strip()
    if expected_name is not None:
        prefix = f"{expected_name}: "
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
    elif ": " in candidate and candidate.split(": ", 1)[0].isidentifier():
        candidate = candidate.split(": ", 1)[1]
    for marker in ("\n\nASCII_MAP:", "\n\nDIGEST:", "\n\nSTUCK_CHECK:"):
        if marker in candidate:
            candidate = candidate.split(marker, 1)[0]
    try:
        payload = json.loads(
            candidate,
            parse_constant=_reject_nonfinite,
            object_pairs_hook=_unique_object,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _canonical_json(value: Any) -> Any:
    """Return a deterministic JSON-safe representation."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # json.dumps with allow_nan=False is the final non-finite guard.
        json.dumps(value, allow_nan=False)
        return value
    if isinstance(value, dict):
        return {
            str(key): _canonical_json(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_json(item) for item in value]
    return {
        "__unsupported_type__": f"{type(value).__module__}.{type(value).__qualname__}"
    }


def _safe_evidence_value(value: Any) -> Any:
    """Represent invalid arguments without crashing or inventing JSON values."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        label = (
            "nan"
            if math.isnan(value)
            else "infinity" if value > 0 else "-infinity"
        )
        return {"__invalid_nonfinite_float__": label}
    if isinstance(value, dict):
        return {
            str(key): _safe_evidence_value(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_evidence_value(item) for item in value]
    return {
        "__unsupported_type__": f"{type(value).__module__}.{type(value).__qualname__}"
    }


def _selected_dict(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: _canonical_json(value.get(key)) for key in keys if key in value}


def normalize_observation(payload: dict[str, Any]) -> dict[str, Any]:
    """Project an observe payload onto stable, player-local state fields."""

    if not isinstance(payload, dict):
        raise TypeError("observation payload must be an object")

    inventory = []
    for item in payload.get("inventory", []):
        if isinstance(item, dict):
            projected = _selected_dict(item, ("slot", "key", "count"))
            slots = item.get("slots")
            if isinstance(slots, list):
                projected["slots"] = sorted(
                    slot
                    for slot in slots
                    if isinstance(slot, int) and not isinstance(slot, bool)
                )
            inventory.append(projected)
    inventory.sort(
        key=lambda item: (
            item.get("slot") is None,
            item.get("slot", 0),
            str(item.get("key", "")),
            item.get("count", 0),
        )
    )

    active_quests = []
    for quest in payload.get("active_quests", []):
        if isinstance(quest, dict):
            active_quests.append(
                _selected_dict(
                    quest,
                    (
                        "name",
                        "key",
                        "stage",
                        "stage_count",
                        "sub_stage",
                        "items_progress",
                    ),
                )
            )
    active_quests.sort(
        key=lambda quest: (str(quest.get("name", "")), str(quest.get("key", "")))
    )

    finished_quests = []
    for quest in payload.get("finished_quests", []):
        if isinstance(quest, dict):
            name = quest.get("name", quest.get("key"))
        else:
            name = quest
        if isinstance(name, str):
            finished_quests.append(name)

    status_source = payload.get("status")
    status = _selected_dict(
        status_source,
        ("dead", "stuck", "nav", "indoors"),
    )
    if isinstance(status_source, dict) and isinstance(
        status_source.get("combat"), dict
    ):
        status["combat"] = _selected_dict(
            status_source["combat"], ("target", "target_hp")
        )
    if "is_dead" in payload:
        status["is_dead"] = _canonical_json(payload["is_dead"])
    if "indoors" in payload:
        status["indoors"] = _canonical_json(payload["indoors"])

    projection = {
        "pos": _selected_dict(payload.get("pos"), ("x", "y")),
        "stats": _selected_dict(
            payload.get("stats"), ("hp", "max_hp", "level", "xp")
        ),
        "status": _canonical_json(status),
        "equipment": _canonical_json(payload.get("equipment", {})),
        "skills": _canonical_json(payload.get("skills", {})),
        "inventory": inventory,
        "active_quests": active_quests,
        "finished_quests": sorted(set(finished_quests)),
    }
    # Reject NaN/Infinity and prove the projection is JSON serializable.
    json.dumps(projection, allow_nan=False, sort_keys=True)
    return projection


def observation_delta(before: Any, after: Any) -> dict[str, Any]:
    """Return deterministic leaf changes between normalized observations."""

    changes: list[dict[str, Any]] = []

    def visit(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                visit(left.get(key), right.get(key), f"{path}/{escaped}")
            return
        if left != right:
            changes.append(
                {"path": path or "/", "before": left, "after": right}
            )

    visit(before, after, "")
    return {"changed": bool(changes), "change_count": len(changes), "changes": changes}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evidence_sha256(record: dict[str, Any]) -> str:
    """Hash an evidence record using canonical JSON bytes."""

    payload = json.dumps(
        record,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def _invoke(
    call_tool: CallTool,
    name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], TransportResult | None]:
    record: dict[str, Any] = {
        "attempted": True,
        "result_received": False,
        "delivery_status": "unknown_after_exception",
        "protocol_success": None,
        "is_error": None,
        "tool_reported_error": None,
        "result_text": None,
        "result_sha256": None,
        "exception": None,
    }
    try:
        result = await call_tool(name, arguments)
        if not isinstance(result, TransportResult):
            raise TypeError("call_tool must return TransportResult")
    except ToolNotAttemptedError as exc:
        record.update(
            {
                "attempted": False,
                "delivery_status": "not_attempted",
                "exception": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }
        )
        return record, None
    except Exception as exc:  # delivery is unknown; action is never retried
        record["exception"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        return record, None
    record.update(
        {
            "result_received": True,
            "delivery_status": "confirmed_by_result",
            "protocol_success": not result.is_error,
            "is_error": result.is_error,
            "result_text": result.text,
            "result_sha256": _sha256_text(result.text),
        }
    )
    parsed = parse_tool_result_json(result.text, expected_name=name)
    if isinstance(parsed, dict) and parsed.get("error") not in (None, "", False):
        record["tool_reported_error"] = _canonical_json(parsed["error"])
    return record, result


async def _observe(observe_immediate: ObserveImmediate) -> dict[str, Any]:
    try:
        result = await observe_immediate()
        if not isinstance(result, TransportResult):
            raise TypeError("observe_immediate must return TransportResult")
    except Exception as exc:
        return {
            "ok": False,
            "raw_text": None,
            "raw_sha256": None,
            "snapshot": None,
            "reason": "transport_exception",
            "exception": {"type": type(exc).__name__, "message": str(exc)},
        }
    payload = None if result.is_error else parse_tool_result_json(
        result.text, expected_name="observe"
    )
    try:
        snapshot = normalize_observation(payload) if payload is not None else None
    except (TypeError, ValueError):
        snapshot = None
    return {
        "ok": snapshot is not None,
        "raw_text": result.text,
        "raw_sha256": _sha256_text(result.text),
        "snapshot": snapshot,
        "reason": (
            "valid"
            if snapshot is not None
            else "mcp_error" if result.is_error else "invalid_observation_payload"
        ),
        "exception": None,
    }


async def execute_tool_call_with_evidence(
    call_tool: CallTool,
    name: str,
    arguments: Any,
    *,
    observe_immediate: ObserveImmediate | None = None,
) -> dict[str, Any]:
    """Execute one schema-valid tool at most once and return stage evidence."""

    valid, reason = validate_tool_call_arguments(name, arguments)
    effect_class = MODEL_VISIBLE_TOOL_EFFECT_CLASSES.get(name, "unknown")
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "call": {"name": name, "arguments": _safe_evidence_value(arguments)},
        "frozen_schema": {
            "sha256": MODEL_VISIBLE_TOOL_SCHEMA_SHA256,
            "valid": valid,
            "reason": reason,
        },
        "effect_class_contract": TOOL_EFFECT_CLASS_VERSION,
        "effect_class": effect_class,
        "mcp": {
            "attempted": False,
            "result_received": False,
            "delivery_status": "not_attempted",
            "protocol_success": None,
            "is_error": None,
            "tool_reported_error": None,
            "result_text": None,
            "result_sha256": None,
            "exception": None,
        },
        "observation": {
            "status": "not_started",
            "before": None,
            "after": None,
            "delta": None,
        },
    }
    if not valid:
        receipt["observation"]["status"] = "not_applicable_schema_invalid"
        return receipt

    call_arguments = dict(arguments)
    if effect_class == "observation":
        receipt["mcp"], result = await _invoke(call_tool, name, call_arguments)
        payload = (
            parse_tool_result_json(result.text, expected_name=name)
            if result is not None and not result.is_error
            else None
        )
        try:
            snapshot = normalize_observation(payload) if payload is not None else None
        except (TypeError, ValueError):
            snapshot = None
        receipt["observation"].update(
            {
                "status": (
                    "observation_readback"
                    if snapshot is not None
                    else "observation_readback_unavailable"
                ),
                "after": snapshot,
            }
        )
        return receipt

    if effect_class == "read_only":
        receipt["mcp"], _ = await _invoke(call_tool, name, call_arguments)
        receipt["observation"]["status"] = "not_applicable_read_only"
        return receipt

    if observe_immediate is None:
        receipt["mcp"], _ = await _invoke(call_tool, name, call_arguments)
        receipt["observation"]["status"] = "not_measured"
        return receipt

    before = await _observe(observe_immediate)
    receipt["observation"]["before"] = before
    if not before["ok"]:
        receipt["observation"]["status"] = "before_unavailable_action_not_invoked"
        return receipt

    receipt["mcp"], _ = await _invoke(call_tool, name, call_arguments)
    after = await _observe(observe_immediate)
    receipt["observation"]["after"] = after
    if not after["ok"]:
        receipt["observation"]["status"] = "after_unavailable_action_not_retried"
        return receipt
    receipt["observation"].update(
        {
            "status": "measured",
            "delta": observation_delta(before["snapshot"], after["snapshot"]),
        }
    )
    return receipt


def compact_evidence_record(record: dict[str, Any]) -> dict[str, Any]:
    """Drop duplicate raw text while retaining hashes, outcomes, and deltas."""

    compact = json.loads(json.dumps(record, allow_nan=False))
    compact.get("mcp", {}).pop("result_text", None)
    observation = compact.get("observation", {})
    for side in ("before", "after"):
        value = observation.get(side)
        if isinstance(value, dict):
            value.pop("raw_text", None)
    return compact
