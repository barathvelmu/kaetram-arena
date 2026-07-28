#!/usr/bin/env python3
"""Build blinded, unlabeled appropriateness-audit packets from V2 outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd.analyze_structured_call_validity import (  # noqa: E402
    strict_json_object,
    validate_structured_call,
)
from scripts.opd.audit_trigger_incidence_artifact_v2 import (  # noqa: E402
    audit_artifact,
)
from scripts.opd.response_router import route_content_tool_call  # noqa: E402


REGISTRATION_SCHEMA = "kaetram.trigger-appropriateness-audit-registration.v1"
PACKET_SCHEMA = "kaetram.trigger-appropriateness-judge-packet.v1"
PROTOCOL_SCHEMA = "kaetram.trigger-appropriateness-judge-protocol.v1"
KEY_SCHEMA = "kaetram.trigger-appropriateness-sealed-key.v1"
SOURCE_SCHEMA = "kaetram.trigger-appropriateness-source-manifest.v1"
MANIFEST_SCHEMA = "kaetram.trigger-appropriateness-packet-manifest.v1"

DEFAULT_REGISTRATION = (
    REPO / "research" / "experiments" / "local-trigger-appropriateness-audit-v1.json"
)
DEFAULT_ARTIFACT = REPO / "research" / "artifacts" / "local-trigger-incidence-v2"
DEFAULT_TRUST_ROOT = (
    REPO
    / "research"
    / "results"
    / "local-trigger-incidence-v2"
    / "artifact-trust-root.json"
)
RUN_RELATIVE_PATHS = (
    "runs/base_2b/results.jsonl",
    "runs/opd_r2_2b/results.jsonl",
    "runs/opd_r3_2b/results.jsonl",
)
OUTPUT_FILENAMES = (
    "judge-protocol.json",
    "judge-packets.jsonl",
    "reviewer-a.template.jsonl",
    "reviewer-b.template.jsonl",
    "adjudication.template.jsonl",
    "label-seals.template.json",
    "labeling-workflow.md",
    "sealed-analysis-key.json",
    "source-manifest.json",
)
AGENT_PSEUDONYM = re.compile(r"qwencompletionist", re.IGNORECASE)
SOURCE_SESSION_ORDINAL = re.compile(r"\bsession\s*#\s*\d+\b", re.IGNORECASE)
VISIBLE_FORBIDDEN = (
    re.compile(r"/Users/", re.IGNORECASE),
    re.compile(r"/home/", re.IGNORECASE),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"(?:barath|patnir|modal|qwen|github)", re.IGNORECASE),
    re.compile(r"\b(?:run|session)_20\d+", re.IGNORECASE),
    re.compile(r"\bsession\s*#\s*\d+\b", re.IGNORECASE),
    re.compile(r"\b(?:base_2b|opd_r[23]_2b)\b", re.IGNORECASE),
    re.compile(r'"(?:snapshot|condition_id|native_tool_schema|sample_index|seed|route|reasoning|state_id|source_log|occurrences|occurrence_count|primary_subset)"\s*:'),
    re.compile(r"</?think>", re.IGNORECASE),
)


class PacketBuildError(RuntimeError):
    pass


def _reject_constant(value: str) -> None:
    raise PacketBuildError(f"non-finite JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PacketBuildError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, json.JSONDecodeError, PacketBuildError) as exc:
        raise PacketBuildError(f"cannot load strict JSON: {path}") from exc
    if not isinstance(value, dict):
        raise PacketBuildError(f"JSON root is not an object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PacketBuildError(f"cannot read JSONL: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            row = json.loads(
                line,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (json.JSONDecodeError, PacketBuildError) as exc:
            raise PacketBuildError(f"invalid JSONL row: {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise PacketBuildError(f"JSONL row is not an object: {path}:{line_number}")
        rows.append(row)
    return rows


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PacketBuildError("value is not canonical JSON") from exc


def pretty_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PacketBuildError("value is not JSON serializable") from exc


def jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical_bytes(row) + b"\n" for row in rows)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_registration(registration: dict[str, Any]) -> None:
    if registration.get("schema_version") != REGISTRATION_SCHEMA:
        raise PacketBuildError("registration schema drift")
    if registration.get("status") != "posthoc_design_only_unlabeled":
        raise PacketBuildError("registration is not design-only and unlabeled")
    census = registration.get("complete_census")
    primary = registration.get("primary_subset")
    gate = registration.get("human_label_gate")
    if (
        not isinstance(census, dict)
        or census.get("expected_unique_items") != 123
        or census.get("expected_valid_occurrences") != 590
        or not isinstance(primary, dict)
        or primary.get("expected_unique_items") != 25
        or primary.get("outcome_blind") is not True
        or not isinstance(gate, dict)
        or gate.get("independent_reviewers") != 2
    ):
        raise PacketBuildError("registration count or human-label gate drift")
    rubric = registration.get("rubric")
    if not isinstance(rubric, dict) or rubric.get("precondition_support") != [
        "supported",
        "contradicted",
        "unobservable",
    ] or rubric.get("strategic_appropriateness") != [
        "appropriate",
        "plausible",
        "inappropriate",
        "unassessable",
    ]:
        raise PacketBuildError("registration rubric drift")
    claim = registration.get("claim_boundary")
    if not isinstance(claim, dict) or claim.get("current") != (
        "This registration and any generated unlabeled packet bundle contain no result."
    ):
        raise PacketBuildError("registration claim boundary drift")


def _candidate_from_row(row: dict[str, Any]) -> tuple[str, dict[str, Any], str] | None:
    message = row.get("response_message")
    if not isinstance(message, dict):
        raise PacketBuildError("successful V2 row has no response message")
    structured = message.get("tool_calls") or []
    if structured:
        if not isinstance(structured, list):
            raise PacketBuildError("structured calls are not a list")
        verdicts = [validate_structured_call(call) for call in structured]
        if not all(valid for valid, _reason in verdicts):
            return None
        if len(structured) != 1:
            raise PacketBuildError("schema-valid multiple-call row is unsupported")
        function = structured[0]["function"]
        return function["name"], strict_json_object(function["arguments"]), "structured"
    decision = route_content_tool_call(message.get("content") or "")
    if decision["status"] != "promoted":
        return None
    if len(decision["calls"]) != 1:
        raise PacketBuildError("strict router promoted a non-singleton call")
    call = decision["calls"][0]
    return call["name"], call["args"], "recovered_content"


def _sanitize_text(value: str) -> str:
    sanitized = AGENT_PSEUDONYM.sub("THE_AGENT", value)
    sanitized = SOURCE_SESSION_ORDINAL.sub("session", sanitized)
    if re.search(r"</?think>", sanitized, re.IGNORECASE):
        raise PacketBuildError("reasoning tag survived context sanitization")
    return sanitized


def _judge_context(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list) or not messages:
        raise PacketBuildError("state messages are missing")
    visible = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            raise PacketBuildError("state message is malformed")
        role = message["role"]
        content = message.get("content")
        if role == "assistant":
            # Historical assistant prose is chain-of-thought-like reasoning. The
            # named tool result that follows is retained, but the prose is not.
            continue
        if role in {"system", "user"}:
            if not isinstance(content, str):
                raise PacketBuildError("visible instruction content is not text")
            visible.append({"role": role, "content": _sanitize_text(content)})
        elif role == "tool":
            if not isinstance(content, str) or not isinstance(message.get("name"), str):
                raise PacketBuildError("visible tool result is malformed")
            visible.append(
                {
                    "role": "tool",
                    "name": message["name"],
                    "content": _sanitize_text(content),
                }
            )
        else:
            raise PacketBuildError(f"unsupported context role: {role}")
    if not visible or any(item["role"] == "assistant" for item in visible):
        raise PacketBuildError("reasoning-free judging context is invalid")
    return visible


def _scan_visible(filename: str, payload: bytes) -> None:
    text = payload.decode("utf-8")
    for pattern in VISIBLE_FORBIDDEN:
        match = pattern.search(text)
        if match is not None:
            raise PacketBuildError(
                f"identity or hidden metadata leaked into {filename}: {match.group(0)!r}"
            )


def _source_records(artifact_dir: Path) -> list[dict[str, Any]]:
    relatives = (
        "artifact-index.json",
        "registration.json",
        "design/design.json",
        *RUN_RELATIVE_PATHS,
    )
    records = []
    for relative in relatives:
        path = artifact_dir / relative
        if path.is_symlink() or not path.is_file():
            raise PacketBuildError(f"source artifact file is missing or unsafe: {relative}")
        records.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return records


def _load_source(
    artifact_dir: Path,
    expected_index_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    try:
        audit = audit_artifact(
            artifact_dir, expected_index_sha256=expected_index_sha256
        )
    except Exception as exc:
        raise PacketBuildError("V2 source artifact failed its independent audit") from exc
    registration = load_json(artifact_dir / "registration.json")
    design = load_json(artifact_dir / "design" / "design.json")
    rows = []
    for relative in RUN_RELATIVE_PATHS:
        rows.extend(load_jsonl(artifact_dir / relative))
    return registration, design, rows, audit


def build_payloads(
    *,
    artifact_dir: Path = DEFAULT_ARTIFACT,
    trust_root_path: Path = DEFAULT_TRUST_ROOT,
    registration_path: Path = DEFAULT_REGISTRATION,
) -> dict[str, bytes]:
    artifact_dir = artifact_dir.resolve()
    trust_root_path = trust_root_path.resolve()
    registration_path = registration_path.resolve()
    audit_registration = load_json(registration_path)
    _validate_registration(audit_registration)
    trust_root = load_json(trust_root_path)
    expected_index_sha256 = trust_root.get("artifact_index_sha256")
    if not isinstance(expected_index_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", expected_index_sha256
    ) is None:
        raise PacketBuildError("V2 trust root has no valid artifact-index digest")
    source_registration, design, rows, audit = _load_source(
        artifact_dir, expected_index_sha256
    )
    snapshots = tuple(source_registration.get("snapshots") or ())
    if len(snapshots) != 3:
        raise PacketBuildError("source group set drift")
    states = design.get("states")
    if not isinstance(states, list) or len(states) != 20:
        raise PacketBuildError("source state design drift")
    states_by_id = {}
    for state in states:
        if not isinstance(state, dict) or not isinstance(state.get("state_id"), str):
            raise PacketBuildError("source state is malformed")
        if state["state_id"] in states_by_id:
            raise PacketBuildError("duplicate source state identifier")
        states_by_id[state["state_id"]] = state

    grouped: dict[tuple[str, str, bytes], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("status") != "ok":
            raise PacketBuildError("source result grid is incomplete")
        candidate = _candidate_from_row(row)
        if candidate is None:
            continue
        name, arguments, route = candidate
        state_id = row.get("state_id")
        if state_id not in states_by_id:
            raise PacketBuildError("result row references an unknown state")
        key = (state_id, name, canonical_bytes(arguments))
        grouped[key].append(
            {
                "snapshot": row.get("snapshot"),
                "condition_id": row.get("condition_id"),
                "route": route,
                "sample_index": row.get("sample_index"),
                "seed": row.get("seed"),
                "row_sha256": sha256_bytes(canonical_bytes(row)),
            }
        )

    expected_census = audit_registration["complete_census"]
    if sum(len(items) for items in grouped.values()) != expected_census[
        "expected_valid_occurrences"
    ] or len(grouped) != expected_census["expected_unique_items"]:
        raise PacketBuildError("valid candidate census differs from registration")

    packets = []
    key_items = []
    item_ids: set[str] = set()
    for (state_id, name, arguments_bytes), occurrences in grouped.items():
        state = states_by_id[state_id]
        arguments = json.loads(arguments_bytes)
        candidate = {"name": name, "arguments": arguments}
        item_material = {
            "namespace": audit_registration["study_id"],
            "source_artifact_index_sha256": expected_index_sha256,
            "state_messages_sha256": state["messages_sha256"],
            "candidate": candidate,
        }
        item_id = "item-" + sha256_bytes(canonical_bytes(item_material))[:20]
        if item_id in item_ids:
            raise PacketBuildError("pseudonymous item identifier collision")
        item_ids.add(item_id)
        context = _judge_context(state["messages"])
        context_sha256 = sha256_bytes(canonical_bytes(context))
        packet = {
            "schema_version": PACKET_SCHEMA,
            "item_id": item_id,
            "context": context,
            "candidate": candidate,
        }
        packets.append(packet)
        observed_snapshots = sorted({item["snapshot"] for item in occurrences})
        primary = observed_snapshots == sorted(snapshots)
        key_items.append(
            {
                "item_id": item_id,
                "state_id": state_id,
                "source_messages_sha256": state["messages_sha256"],
                "judge_context_sha256": context_sha256,
                "candidate_sha256": sha256_bytes(canonical_bytes(candidate)),
                "occurrence_count": len(occurrences),
                "distinct_snapshot_count": len(observed_snapshots),
                "primary_subset": primary,
                "occurrences": sorted(
                    occurrences,
                    key=lambda item: (
                        str(item["snapshot"]),
                        str(item["condition_id"]),
                        int(item["sample_index"]),
                        int(item["seed"]),
                        str(item["route"]),
                    ),
                ),
            }
        )
    packets.sort(key=lambda item: item["item_id"])
    key_items.sort(key=lambda item: item["item_id"])
    primary_count = sum(item["primary_subset"] for item in key_items)
    if primary_count != audit_registration["primary_subset"]["expected_unique_items"]:
        raise PacketBuildError("mechanical primary subset differs from registration")

    protocol = {
        "schema_version": PROTOCOL_SCHEMA,
        "study_id": audit_registration["study_id"],
        "status": "unlabeled_design_only",
        "task": (
            "Judge the exact candidate only from the retained pre-call context. "
            "Do not infer whether it later executed or succeeded."
        ),
        "context_policy": (
            "Assistant free-text reasoning is omitted. System and user instructions "
            "and named tool results are retained; the historical agent pseudonym and "
            "source-session ordinals are redacted."
        ),
        "rubric": audit_registration["rubric"],
        "label_record_schema": {
            "required_fields": [
                "item_id",
                "precondition_support",
                "strategic_appropriateness",
                "concise_rationale",
            ],
            "additional_fields_allowed": False,
            "concise_rationale": "One or two sentences grounded only in visible context.",
        },
        "human_gate": {
            "independent_reviewers": 2,
            "independent_until_both_hash_sealed": True,
            "distinct_third_human_adjudicator": True,
            "human_adjudication_required_for_every_disagreement": True,
            "any_missing_or_invalid_label_blocks_all_aggregation": True,
        },
        "claim_boundary": (
            "This packet bundle is an unlabeled post-hoc design, not an execution "
            "study and not a scientific result."
        ),
    }
    judge_packets = jsonl_bytes(packets)
    protocol_payload = pretty_bytes(protocol)
    label_templates = {}
    for role, filename in (
        ("reviewer_a", "reviewer-a.template.jsonl"),
        ("reviewer_b", "reviewer-b.template.jsonl"),
    ):
        label_templates[filename] = jsonl_bytes(
            {
                "schema_version": "kaetram.trigger-appropriateness-human-label.v1",
                "item_id": packet["item_id"],
                "reviewer_role": role,
                "precondition_support": None,
                "strategic_appropriateness": None,
                "concise_rationale": None,
            }
            for packet in packets
        )
    adjudication_template = jsonl_bytes(
        {
            "schema_version": "kaetram.trigger-appropriateness-adjudication.v1",
            "item_id": packet["item_id"],
            "adjudication_required": None,
            "resolved_precondition_support": None,
            "resolved_strategic_appropriateness": None,
            "concise_rationale": None,
        }
        for packet in packets
    )
    seal_template = pretty_bytes(
        {
            "schema_version": "kaetram.trigger-appropriateness-label-seals.v1",
            "reviewer_a": {
                "human_reviewer_id": None,
                "file_sha256": None,
                "independent": None,
                "completed_without_access_to_other_labels": None,
                "completed_without_access_to_sealed_key": None,
            },
            "reviewer_b": {
                "human_reviewer_id": None,
                "file_sha256": None,
                "independent": None,
                "completed_without_access_to_other_labels": None,
                "completed_without_access_to_sealed_key": None,
            },
            "both_reviewer_files_sealed_before_adjudication": None,
            "adjudicator_human_id": None,
            "adjudication_file_sha256": None,
        }
    )
    workflow_payload = (
        "# Human-label workflow\n\n"
        "This bundle contains no labels and no result. Do these steps in order.\n\n"
        "1. Give `judge-protocol.json`, `judge-packets.jsonl`, and a copy of "
        "`reviewer-a.template.jsonl` to Reviewer A. Do not give them the sealed key "
        "or Reviewer B's file.\n"
        "2. Independently give the same judging files and a copy of "
        "`reviewer-b.template.jsonl` to Reviewer B. Do not give them the sealed key "
        "or Reviewer A's file.\n"
        "3. Each reviewer fills every null rubric field and gives a concise rationale "
        "for all 123 items. Keep item IDs and row order unchanged.\n"
        "4. Compute SHA-256 for both completed files and fill the two reviewer blocks "
        "in `label-seals.template.json`. The two human reviewer IDs must be distinct.\n"
        "5. Only after both reviewer files are sealed, compare them. A third human "
        "adjudicator (not Reviewer A or B) copies "
        "`adjudication.template.jsonl`; mark every disagreement true and every agreement "
        "false. A human adjudicator must resolve both rubric fields and write a rationale "
        "for each disagreement. Agreement rows keep all resolution fields null.\n"
        "6. Hash the completed adjudication file, finish the seal file, and run "
        "`scripts/opd/verify_trigger_appropriateness_labels.py`. It fails on one missing "
        "label, bad hash, leaked key, reviewer reuse, or unresolved disagreement.\n"
        "7. Do not unseal source-group membership or compute an aggregate until that "
        "verifier succeeds. The verifier merges labels; it does not compute statistics.\n"
    ).encode("utf-8")
    visible_payloads = {
        "judge-protocol.json": protocol_payload,
        "judge-packets.jsonl": judge_packets,
        **label_templates,
        "adjudication.template.jsonl": adjudication_template,
        "label-seals.template.json": seal_template,
        "labeling-workflow.md": workflow_payload,
    }
    for filename, payload in visible_payloads.items():
        _scan_visible(filename, payload)

    sealed_key = {
        "schema_version": KEY_SCHEMA,
        "study_id": audit_registration["study_id"],
        "status": "sealed_until_two_complete_independent_label_sets_and_adjudication",
        "source_snapshot_order": list(snapshots),
        "selection_rule": audit_registration["primary_subset"]["selection"],
        "unique_item_count": len(key_items),
        "valid_occurrence_count": sum(item["occurrence_count"] for item in key_items),
        "primary_item_count": primary_count,
        "items": key_items,
    }
    sealed_payload = pretty_bytes(sealed_key)

    source_files = _source_records(artifact_dir)
    source_manifest = {
        "schema_version": SOURCE_SCHEMA,
        "study_id": audit_registration["study_id"],
        "source_artifact_index_sha256": expected_index_sha256,
        "source_artifact_tree_sha256": audit["artifact_tree_sha256"],
        "source_audit_schema_version": audit["schema_version"],
        "source_scheduled_requests": audit["scheduled_requests"],
        "source_successful_requests": audit["successful_requests"],
        "audit_registration": {
            "path": registration_path.relative_to(REPO).as_posix(),
            "sha256": sha256_file(registration_path),
        },
        "trust_root": {
            "path": trust_root_path.relative_to(REPO).as_posix(),
            "sha256": sha256_file(trust_root_path),
        },
        "source_files": source_files,
        "builder_sha256": sha256_file(Path(__file__).resolve()),
        "verifier_sha256": sha256_file(
            REPO / "scripts" / "opd" / "verify_trigger_appropriateness_packets.py"
        ),
        "label_verifier_sha256": sha256_file(
            REPO / "scripts" / "opd" / "verify_trigger_appropriateness_labels.py"
        ),
    }
    source_payload = pretty_bytes(source_manifest)
    payloads = {
        **visible_payloads,
        "sealed-analysis-key.json": sealed_payload,
        "source-manifest.json": source_payload,
    }
    file_records = [
        {
            "path": name,
            "size_bytes": len(payloads[name]),
            "sha256": sha256_bytes(payloads[name]),
        }
        for name in OUTPUT_FILENAMES
    ]
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "study_id": audit_registration["study_id"],
        "status": "unlabeled_design_only",
        "files": file_records,
        "tree_sha256": sha256_bytes(canonical_bytes(file_records)),
        "unique_item_count": len(key_items),
        "primary_item_count": primary_count,
        "identity_scan": {
            "visible_files": list(visible_payloads),
            "scanner_version": "kaetram-trigger-judge-visible-scan-v1",
            "matches": 0,
            "passed": True,
        },
        "human_label_gate_satisfied": False,
        "contains_labels": False,
        "contains_results": False,
    }
    payloads["manifest.json"] = pretty_bytes(manifest)
    return payloads


def write_bundle(output_dir: Path, payloads: dict[str, bytes]) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise PacketBuildError(f"refusing to overwrite output directory: {output_dir}")
    if set(payloads) != {*OUTPUT_FILENAMES, "manifest.json"}:
        raise PacketBuildError("output payload set drift")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(mode=0o700)
    try:
        for name in (*OUTPUT_FILENAMES, "manifest.json"):
            path = output_dir / name
            with path.open("xb") as handle:
                handle.write(payloads[name])
                handle.flush()
                os.fsync(handle.fileno())
        directory_fd = os.open(output_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        # Preserve partial output for diagnosis; never pretend it is complete.
        raise
    return json.loads(payloads["manifest.json"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--trust-root", type=Path, default=DEFAULT_TRUST_ROOT)
    parser.add_argument("--registration", type=Path, default=DEFAULT_REGISTRATION)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payloads = build_payloads(
        artifact_dir=args.artifact_dir,
        trust_root_path=args.trust_root,
        registration_path=args.registration,
    )
    manifest = write_bundle(args.out, payloads)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
