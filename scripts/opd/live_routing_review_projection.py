#!/usr/bin/env python3
"""Build and verify a minimal anonymous projection of a live-routing run.

The private package is the evidence root.  This module deliberately emits no
identifier or digest from that package: it retains only re-keyed trial labels
and the narrow, descriptive outcomes needed by a reviewer.  A projection can
be checked structurally on its own and can be checked for exact parity against
the fully verified private package by an author or artifact chair.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


PROJECTION_SCHEMA = "kaetram.live-routing-anonymous-review-projection.v2"
ROOT_KEYS = {
    "schema_version",
    "scope",
    "trials",
    "summary",
    "projection_sha256",
}
TRIAL_KEYS = {
    "trial_label",
    "repeat",
    "arm",
    "validity",
    "registered_outcome",
    "candidate_invocations",
    "candidate_delivery",
    "immediate_target_reached",
    "delayed_target_reached",
    "reconnect_target_reached",
    "database_target_reached",
    "client_baseline_preserved",
    "strict_database_baseline_preserved",
    "database_defaults_and_session_bookkeeping_materialized",
}
SUMMARY_KEYS = {
    "scheduled_trials",
    "valid_trials",
    "invalid_trials",
    "arms",
    "inferential_statistics",
}
ARM_SUMMARY_KEYS = {
    "scheduled",
    "valid",
    "registered_passes",
    "candidate_not_invoked",
    "client_baseline_preserved",
    "strict_database_baseline_preserved",
    "database_defaults_and_session_bookkeeping_materialized",
}
SCOPE = "single_fixture_descriptive_routing_check_no_model_calls"
ARM_ORDER = (
    "structured_direct",
    "content_recovery_on",
    "content_recovery_off",
)
COMPLETED_SCHEDULE = (
    (1, "structured_direct"),
    (1, "content_recovery_on"),
    (1, "content_recovery_off"),
    (2, "content_recovery_on"),
    (2, "content_recovery_off"),
    (2, "structured_direct"),
    (3, "content_recovery_off"),
    (3, "structured_direct"),
    (3, "content_recovery_on"),
)
TARGET_ARMS = {"structured_direct", "content_recovery_on"}
ALLOWED_DELIVERY = {"confirmed", "not_attempted", "failed", "unknown"}
SHA256 = re.compile(r"[0-9a-f]{64}")
MATERIALIZED_STATISTICS_KEYS = {
    "averageTimePlayed",
    "cheater",
    "creationTime",
    "drops",
    "lastLogin",
    "loginCount",
    "mobExamines",
    "mobKills",
    "pvpDeaths",
    "pvpKills",
    "resources",
    "totalTimePlayed",
}


class ProjectionError(ValueError):
    """The anonymous projection is malformed or differs from private evidence."""


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical compact JSON used for equality and internal projection hashes."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProjectionError(f"non-finite JSON constant: {value}")


def load_json_strict(path: Path) -> tuple[dict[str, Any], bytes]:
    """Load a regular, finite, duplicate-key-free JSON object."""

    if path.is_symlink() or not path.is_file():
        raise ProjectionError(f"required JSON is missing or symlinked: {path}")
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProjectionError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectionError(f"JSON artifact root is not an object: {path}")
    return value, raw


def verify_package_or_raise(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Load the private verifier only for author-side parity operations."""

    from scripts.opd.live_routing_result_verify import (
        verify_package_or_raise as verify_private_package,
    )

    return verify_private_package(*args, **kwargs)


def _canonical_file_bytes(value: Any) -> bytes:
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


def _projection_sha256(value: dict[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "projection_sha256"}
    return hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()


def _json_equal(left: Any, right: Any) -> bool:
    try:
        return canonical_json_bytes(left) == canonical_json_bytes(right)
    except (TypeError, ValueError):
        return False


def _in_target_region(projection: Any, region: dict[str, Any]) -> bool:
    if not isinstance(projection, dict) or not isinstance(projection.get("pos"), dict):
        return False
    x = projection["pos"].get("x")
    y = projection["pos"].get("y")
    return bool(
        type(x) in (int, float)
        and type(y) in (int, float)
        and math.isfinite(x)
        and math.isfinite(y)
        and region["x_min"] <= x <= region["x_max"]
        and region["y_min"] <= y <= region["y_max"]
    )


def _contains_records(actual: Any, expected: Any) -> bool:
    if not isinstance(actual, list) or not isinstance(expected, list):
        return False
    actual_tokens = {canonical_json_bytes(item) for item in actual}
    return all(canonical_json_bytes(item) in actual_tokens for item in expected)


def _database_defaults_and_session_bookkeeping_materialized_only(
    actual: Any, expected: Any
) -> bool:
    """Recognize the observed default expansion and session bookkeeping.

    The run's strict database predicate fails because the game server expands
    empty/default collections on login.  We prove that narrow characterization
    here, then export a boolean instead of timestamps, object IDs, or raw rows.
    """

    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    if set(actual) != set(expected) or _json_equal(actual, expected):
        return False
    expanded = {"equipment", "quests", "achievements", "skills", "statistics"}
    if any(
        not _json_equal(actual[key], expected[key])
        for key in set(expected) - expanded
    ):
        return False
    if {
        key for key in expanded if not _json_equal(actual[key], expected[key])
    } != expanded:
        return False
    if not _contains_records(actual["quests"], expected["quests"]):
        return False
    if not isinstance(actual["equipment"], list) or not actual["equipment"]:
        return False
    if not all(
        isinstance(row, dict)
        and row.get("key") == ""
        and row.get("count") == -1
        for row in actual["equipment"]
    ):
        return False
    if not isinstance(actual["achievements"], list) or not actual["achievements"]:
        return False
    if not all(
        isinstance(row, dict) and row.get("stage") == 0
        for row in actual["achievements"]
    ):
        return False
    if not isinstance(actual["skills"], list) or not actual["skills"]:
        return False
    if not all(
        isinstance(row, dict) and row.get("experience") == 0
        for row in actual["skills"]
    ):
        return False
    statistics = actual["statistics"]
    if not isinstance(statistics, dict) or set(statistics) != MATERIALIZED_STATISTICS_KEYS:
        return False
    if any(
        statistics[key] != value
        for key, value in {
            "cheater": False,
            "drops": {},
            "mobExamines": [],
            "mobKills": {},
            "pvpDeaths": 0,
            "pvpKills": 0,
            "resources": {},
        }.items()
    ):
        return False
    for key in ("averageTimePlayed", "totalTimePlayed"):
        value = statistics[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return False
    for key in ("creationTime", "lastLogin", "loginCount"):
        value = statistics[key]
        if type(value) is not int or value < 1:
            return False
    if statistics["lastLogin"] < statistics["creationTime"]:
        return False
    expected_quest_tokens = {canonical_json_bytes(row) for row in expected["quests"]}
    for row in actual["quests"]:
        if canonical_json_bytes(row) in expected_quest_tokens:
            continue
        if not (
            isinstance(row, dict)
            and row.get("stage") == 0
            and row.get("sub_stage") == 0
            and row.get("completed_sub_stages") == []
        ):
            return False
    return True


def _load_receipts(package_dir: Path) -> list[dict[str, Any]]:
    return [
        load_json_strict(package_dir / "receipts" / f"trial-{index:02d}.json")[0]
        for index in range(1, 10)
    ]


def _project_rows(
    analysis: dict[str, Any],
    receipts: list[dict[str, Any]],
    registration: dict[str, Any],
) -> list[dict[str, Any]]:
    if len(receipts) != 9 or len(analysis.get("trials", [])) != 9:
        raise ProjectionError("verified source must contain exactly nine trials")
    by_trial_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for result, receipt in zip(analysis["trials"], receipts, strict=True):
        trial_id = result.get("trial_id")
        if (
            not isinstance(trial_id, str)
            or receipt.get("plan", {}).get("trial_id") != trial_id
            or trial_id in by_trial_id
        ):
            raise ProjectionError("analysis/receipt trial parity failed")
        by_trial_id[trial_id] = (result, receipt)

    ordered = sorted(
        by_trial_id.values(),
        key=lambda pair: pair[1].get("plan", {}).get("schedule_index"),
    )
    if [pair[1].get("plan", {}).get("schedule_index") for pair in ordered] != list(
        range(1, 10)
    ):
        raise ProjectionError("verified schedule indexes are not canonical")
    fixture = registration["state_fixture"]["expected"]
    database_fixture = registration["state_fixture"]["database_expected"]
    region = registration["measurement"]["mudwich_success_region"]
    rows: list[dict[str, Any]] = []
    for index, (result, receipt) in enumerate(ordered, start=1):
        arm = result["arm"]
        measurements = receipt["measurements"]
        immediate = measurements["immediate"]["normalized_projection"]
        delayed = measurements["delayed"]["normalized_projection"]
        reconnect = measurements["reconnect"]["normalized_projection"]
        database = measurements["database"]["normalized_projection"]
        target_arm = arm in TARGET_ARMS
        off_arm = arm == "content_recovery_off"
        rows.append(
            {
                "trial_label": f"trial-{index:02d}",
                "repeat": result["repeat"],
                "arm": arm,
                "validity": result["validity"],
                "registered_outcome": result["outcome"],
                "candidate_invocations": receipt["routing"][
                    "candidate_invocation_count"
                ],
                "candidate_delivery": receipt["routing"]["delivery_status"],
                "immediate_target_reached": (
                    _in_target_region(immediate, region) if target_arm else None
                ),
                "delayed_target_reached": (
                    _in_target_region(delayed, region) if target_arm else None
                ),
                "reconnect_target_reached": (
                    _in_target_region(reconnect, region) if target_arm else None
                ),
                "database_target_reached": (
                    _in_target_region(database, region) if target_arm else None
                ),
                "client_baseline_preserved": (
                    all(
                        _json_equal(projection, fixture)
                        for projection in (immediate, delayed, reconnect)
                    )
                    if off_arm
                    else None
                ),
                "strict_database_baseline_preserved": (
                    _json_equal(database, database_fixture) if off_arm else None
                ),
                "database_defaults_and_session_bookkeeping_materialized": (
                    _database_defaults_and_session_bookkeeping_materialized_only(
                        database, database_fixture
                    )
                    if off_arm
                    else False
                ),
            }
        )
    return rows


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    arms: dict[str, dict[str, int]] = {}
    for arm in ARM_ORDER:
        selected = [row for row in rows if row["arm"] == arm]
        arms[arm] = {
            "scheduled": len(selected),
            "valid": sum(row["validity"] == "valid" for row in selected),
            "registered_passes": sum(
                row["registered_outcome"] == "pass" for row in selected
            ),
            "candidate_not_invoked": sum(
                row["candidate_invocations"] == 0 for row in selected
            ),
            "client_baseline_preserved": sum(
                row["client_baseline_preserved"] is True for row in selected
            ),
            "strict_database_baseline_preserved": sum(
                row["strict_database_baseline_preserved"] is True
                for row in selected
            ),
            "database_defaults_and_session_bookkeeping_materialized": sum(
                row[
                    "database_defaults_and_session_bookkeeping_materialized"
                ]
                is True
                for row in selected
            ),
        }
    valid = sum(row["validity"] == "valid" for row in rows)
    return {
        "scheduled_trials": len(rows),
        "valid_trials": valid,
        "invalid_trials": len(rows) - valid,
        "arms": arms,
        "inferential_statistics": "forbidden_technical_repeats_not_independent",
    }


def build_review_projection(
    package_dir: Path,
    registration_path: Path,
    *,
    repo_root: Path,
    expected_head: str,
) -> dict[str, Any]:
    """Fail closed on the full verifier, then return an anonymous projection."""

    try:
        analysis = verify_package_or_raise(
            package_dir,
            registration_path,
            repo_root=repo_root,
            expected_head=expected_head,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ProjectionError(f"private package verification failed: {exc}") from exc
    registration, _ = load_json_strict(registration_path)
    receipts = _load_receipts(package_dir.resolve())
    trials = _project_rows(analysis, receipts, registration)
    projection: dict[str, Any] = {
        "schema_version": PROJECTION_SCHEMA,
        "scope": SCOPE,
        "trials": trials,
        "summary": _summarize(trials),
    }
    projection["projection_sha256"] = _projection_sha256(projection)
    validate_review_projection(projection)
    return projection


def validate_review_projection(projection: dict[str, Any]) -> None:
    """Strictly validate the self-contained anonymous artifact."""

    if set(projection) != ROOT_KEYS or projection.get("schema_version") != PROJECTION_SCHEMA:
        raise ProjectionError("projection root schema/key set drift")
    if projection.get("scope") != SCOPE:
        raise ProjectionError("projection scope drift")
    digest = projection.get("projection_sha256")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        raise ProjectionError("projection digest is invalid")
    if digest != _projection_sha256(projection):
        raise ProjectionError("projection self-hash mismatch")
    trials = projection.get("trials")
    if not isinstance(trials, list) or len(trials) != 9:
        raise ProjectionError("projection must contain exactly nine trials")
    expected_pairs = list(COMPLETED_SCHEDULE)
    for index, (row, expected_pair) in enumerate(zip(trials, expected_pairs, strict=True), 1):
        if not isinstance(row, dict) or set(row) != TRIAL_KEYS:
            raise ProjectionError(f"trial {index} key set drift")
        if row.get("trial_label") != f"trial-{index:02d}":
            raise ProjectionError("trial labels are not neutral and canonical")
        if (row.get("repeat"), row.get("arm")) != expected_pair:
            raise ProjectionError("trial repeat/arm order drift")
        if row.get("validity") not in {"valid", "invalid"}:
            raise ProjectionError("trial validity is invalid")
        if row.get("registered_outcome") not in {"pass", "fail", "not_assessable"}:
            raise ProjectionError("registered outcome is invalid")
        invocations = row.get("candidate_invocations")
        if type(invocations) is not int or invocations < 0:
            raise ProjectionError("candidate invocation count is invalid")
        if row.get("candidate_delivery") not in ALLOWED_DELIVERY:
            raise ProjectionError("candidate delivery status is invalid")
        target_arm = row["arm"] in TARGET_ARMS
        for key in (
            "immediate_target_reached",
            "delayed_target_reached",
            "reconnect_target_reached",
            "database_target_reached",
        ):
            if (target_arm and type(row[key]) is not bool) or (
                not target_arm and row[key] is not None
            ):
                raise ProjectionError(f"trial applicability drift: {key}")
        for key in (
            "client_baseline_preserved",
            "strict_database_baseline_preserved",
        ):
            if (not target_arm and type(row[key]) is not bool) or (
                target_arm and row[key] is not None
            ):
                raise ProjectionError(f"trial applicability drift: {key}")
        materialized = row[
            "database_defaults_and_session_bookkeeping_materialized"
        ]
        if type(materialized) is not bool:
            raise ProjectionError("database-materialization indicator is not boolean")
        if target_arm and materialized:
            raise ProjectionError(
                "database-materialization indicator is inapplicable to active arms"
            )
    summary = projection.get("summary")
    if not isinstance(summary, dict) or set(summary) != SUMMARY_KEYS:
        raise ProjectionError("projection summary key set drift")
    arms = summary.get("arms")
    if not isinstance(arms, dict) or set(arms) != set(ARM_ORDER):
        raise ProjectionError("projection arm summary order/key set drift")
    if any(not isinstance(value, dict) or set(value) != ARM_SUMMARY_KEYS for value in arms.values()):
        raise ProjectionError("projection arm summary key set drift")
    if not _json_equal(summary, _summarize(trials)):
        raise ProjectionError("projection summary differs from trial rows")
    if summary["valid_trials"] != 9 or summary["invalid_trials"] != 0:
        raise ProjectionError("completed-result projection must retain 9/9 validity")
    for row in trials:
        if row["arm"] in TARGET_ARMS:
            expected = {
                "validity": "valid",
                "registered_outcome": "pass",
                "candidate_invocations": 1,
                "candidate_delivery": "confirmed",
                "immediate_target_reached": True,
                "delayed_target_reached": True,
                "reconnect_target_reached": True,
                "database_target_reached": True,
                "client_baseline_preserved": None,
                "strict_database_baseline_preserved": None,
                "database_defaults_and_session_bookkeeping_materialized": False,
            }
        else:
            expected = {
                "validity": "valid",
                "registered_outcome": "fail",
                "candidate_invocations": 0,
                "candidate_delivery": "not_attempted",
                "immediate_target_reached": None,
                "delayed_target_reached": None,
                "reconnect_target_reached": None,
                "database_target_reached": None,
                "client_baseline_preserved": True,
                "strict_database_baseline_preserved": False,
                "database_defaults_and_session_bookkeeping_materialized": True,
            }
        if any(row.get(key) != value for key, value in expected.items()):
            raise ProjectionError("trial row differs from completed-result claim boundary")


def load_review_projection(path: Path) -> tuple[dict[str, Any], bytes]:
    projection, raw = load_json_strict(path)
    validate_review_projection(projection)
    if raw != _canonical_file_bytes(projection):
        raise ProjectionError("projection file is not in canonical rendered form")
    return projection, raw


def verify_review_projection_against_package(
    projection_path: Path,
    package_dir: Path,
    registration_path: Path,
    *,
    repo_root: Path,
    expected_head: str,
) -> dict[str, Any]:
    projection, _ = load_review_projection(projection_path)
    expected = build_review_projection(
        package_dir,
        registration_path,
        repo_root=repo_root,
        expected_head=expected_head,
    )
    if not _json_equal(projection, expected):
        raise ProjectionError("anonymous projection differs from verified full analysis")
    return projection


def write_review_projection(path: Path, projection: dict[str, Any]) -> None:
    validate_review_projection(projection)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(_canonical_file_bytes(projection))
    except FileExistsError as exc:
        raise ProjectionError(f"refusing to overwrite projection: {path}") from exc
