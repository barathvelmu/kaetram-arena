#!/usr/bin/env python3
"""Create and verify a source/configuration seal for the live routing study.

This module never probes or starts MongoDB, the game, a browser, a model, or a
remote endpoint.  The current design-only registration is intentionally
refused.  A reviewed future commit must first mark the design live-ready and
add the result-bearing launcher/analyzer/verifier to its source contract.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.opd.live_routing_diagnostic import validate_registration  # noqa: E402


SCHEMA_VERSION = "kaetram.live-routing-diagnostic-prelaunch.v1"
READY_STATUS = "registered_before_live_execution"
SEALED_STATUS = "sealed_prelaunch_configuration_only"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
EXPECTED_LANE = {
    "client_url": "http://127.0.0.1:9000",
    "game_ws_url": "ws://127.0.0.1:9191",
    "mongo_uri": "mongodb://127.0.0.1:27017/kaetram_e2e",
    "mongo_database": "kaetram_e2e",
    "model_calls": 0,
    "remote_endpoints": "forbidden",
    "metered_services": "forbidden",
    "network_probe_performed": False,
    "services_started": False,
}
EXPECTED_ZERO_COST_CONTRACT = {
    "model_calls": 0,
    "remote_endpoints": "forbidden",
    "metered_services": "forbidden",
    "network_scope": "loopback_only",
    "game_port": 9191,
    "mongo_port": 27017,
    "mongo_database": "kaetram_e2e",
}
EXPECTED_LIVE_GATES = {
    "cold_mcp_session_per_trial": True,
    "cold_browser_session_per_trial": True,
    "fresh_unique_player_per_trial": True,
    "unique_username_count": 9,
    "mongo_database_explicit_every_operation": "kaetram_e2e",
    "runtime_receipts_required": True,
}
PRELAUNCH_KEYS = {
    "schema_version",
    "study_id",
    "run_id",
    "status",
    "created_at_utc",
    "registration",
    "claim_contract",
    "claim_contract_sha256",
    "candidate_contract_sha256",
    "fixture_contract_sha256",
    "stage_contract_sha256",
    "zero_cost_contract_sha256",
    "source",
    "lane",
    "trials",
    "trial_plan_sha256",
    "limitations",
    "payload_sha256",
}


class PrelaunchError(RuntimeError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def load_json_strict(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PrelaunchError(f"invalid JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PrelaunchError(f"JSON root must be an object: {path}")
    return value, hashlib.sha256(raw).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PrelaunchError(f"value is not canonical JSON: {exc}") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repo: Path, *arguments: str, binary: bool = False) -> str | bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            capture_output=True,
            text=not binary,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PrelaunchError(f"git inspection failed: {' '.join(arguments)}") from exc
    return result.stdout


def git_source_identity(repo_root: Path, expected_head: str) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    if not GIT_SHA_RE.fullmatch(expected_head):
        raise PrelaunchError("expected_head must be a full 40-character commit")
    top = Path(str(_git(repo_root, "rev-parse", "--show-toplevel")).strip()).resolve()
    if top != repo_root:
        raise PrelaunchError("repo_root is not the exact Git toplevel")
    head = str(_git(repo_root, "rev-parse", "--verify", "HEAD^{commit}")).strip()
    if head != expected_head:
        raise PrelaunchError(f"Git HEAD drift: expected={expected_head}, actual={head}")
    status = _git(
        repo_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    if status:
        raise PrelaunchError("source worktree is not completely clean")
    return {"git_head": head, "worktree_clean": True, "dirty_paths": []}


def require_loopback_uri(
    uri: str,
    *,
    schemes: tuple[str, ...],
    expected_port: int,
    expected_path: str = "",
) -> dict[str, Any]:
    try:
        parsed = urlsplit(uri)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise PrelaunchError(f"invalid lane URI: {uri!r}") from exc
    if parsed.scheme not in schemes or not host:
        raise PrelaunchError(f"lane URI has unapproved scheme/host: {uri!r}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PrelaunchError(f"lane URI cannot contain credentials/query/fragment: {uri!r}")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise PrelaunchError("lane hosts must be numeric loopback addresses") from exc
    if not address.is_loopback or port != expected_port:
        raise PrelaunchError(f"lane URI is not the registered loopback port: {uri!r}")
    path = parsed.path or ""
    if path != expected_path:
        raise PrelaunchError(f"lane URI path drift: expected={expected_path!r}, actual={path!r}")
    return {"scheme": parsed.scheme, "host": str(address), "port": port, "path": path}


def validate_lane(lane: dict[str, Any]) -> None:
    if canonical_json_bytes(lane) != canonical_json_bytes(EXPECTED_LANE):
        raise PrelaunchError("effective lane differs from the frozen zero-cost lane")
    require_loopback_uri(
        lane["client_url"], schemes=("http",), expected_port=9000
    )
    require_loopback_uri(
        lane["game_ws_url"], schemes=("ws",), expected_port=9191
    )
    require_loopback_uri(
        lane["mongo_uri"],
        schemes=("mongodb",),
        expected_port=27017,
        expected_path="/kaetram_e2e",
    )


def validate_registration_gates(registration: dict[str, Any]) -> None:
    if canonical_json_bytes(registration.get("zero_cost_contract")) != canonical_json_bytes(
        EXPECTED_ZERO_COST_CONTRACT
    ):
        raise PrelaunchError("registration zero-cost contract is not exact")
    live = registration.get("live_contract")
    if not isinstance(live, dict):
        raise PrelaunchError("registration live contract is missing")
    for key, expected in EXPECTED_LIVE_GATES.items():
        if live.get(key) != expected:
            raise PrelaunchError(f"registration live gate drift: {key}")


def validate_source_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PrelaunchError(f"invalid source-contract path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise PrelaunchError(f"source-contract path escapes repository: {value!r}")
    return value


def validate_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise PrelaunchError("created_at_utc must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PrelaunchError("created_at_utc must carry an explicit UTC offset")


def derive_trial_identities(
    registration: dict[str, Any], run_id: str
) -> list[dict[str, Any]]:
    schedule = registration.get("schedule")
    identities = registration.get("trial_identities")
    arms = registration.get("arms")
    if not isinstance(schedule, list) or not isinstance(identities, list):
        raise PrelaunchError("registration is missing schedule/trial identities")
    if not isinstance(arms, list):
        raise PrelaunchError("registration arms are missing")
    arm_map = {arm.get("arm"): arm for arm in arms if isinstance(arm, dict)}
    expected: list[dict[str, Any]] = []
    index = 0
    for repeat_row in schedule:
        repeat = repeat_row.get("repeat")
        order = repeat_row.get("arm_order")
        if not isinstance(repeat, int) or not isinstance(order, list):
            raise PrelaunchError("invalid registered schedule row")
        for position, arm_name in enumerate(order, start=1):
            index += 1
            arm = arm_map.get(arm_name)
            if not isinstance(arm, dict):
                raise PrelaunchError(f"schedule references unknown arm: {arm_name!r}")
            expected.append(
                {
                    "schedule_index": index,
                    "repeat": repeat,
                    "position_within_repeat": position,
                    "pair_id": f"repeat-{repeat:02d}",
                    "arm": arm_name,
                    "trial_key": f"llrd-v1-t{index:02d}",
                    "username_template": f"lr_{{run_id}}_{index:02d}",
                    "treatment_session_id_template": (
                        f"llrd-{{run_id}}-t{index:02d}-treatment"
                    ),
                    "reconnect_session_id_template": (
                        f"llrd-{{run_id}}-t{index:02d}-reconnect"
                    ),
                    "route": arm.get("route"),
                    "recovery": arm.get("recovery"),
                    "expected_candidate_invocations": arm.get(
                        "expected_candidate_invocations"
                    ),
                }
            )
    if identities != expected or len(expected) != 9:
        raise PrelaunchError("trial identities do not exactly match the registered schedule")
    for key in (
        "trial_key",
        "username_template",
        "treatment_session_id_template",
        "reconnect_session_id_template",
    ):
        values = [row[key] for row in identities]
        if len(set(values)) != 9:
            raise PrelaunchError(f"trial identity field is not unique: {key}")
    sessions = [row[key] for row in identities for key in (
        "treatment_session_id_template", "reconnect_session_id_template"
    )]
    if len(set(sessions)) != 18:
        raise PrelaunchError("treatment/reconnect session identities are not globally unique")
    resolved = []
    for row in identities:
        resolved_row = {
            **{
                key: value
                for key, value in row.items()
                if not key.endswith("_template")
            },
            "username": row["username_template"].format(run_id=run_id),
            "treatment_session_id": row["treatment_session_id_template"].format(
                run_id=run_id
            ),
            "reconnect_session_id": row["reconnect_session_id_template"].format(
                run_id=run_id
            ),
        }
        resolved.append(resolved_row)
    return resolved


def bind_trial_ids(
    trials: list[dict[str, Any]],
    *,
    study_id: str,
    run_id: str,
    registration_sha256: str,
) -> list[dict[str, Any]]:
    bound: list[dict[str, Any]] = []
    for trial in trials:
        identity = {
            "study_id": study_id,
            "run_id": run_id,
            "registration_sha256": registration_sha256,
            "schedule_index": trial["schedule_index"],
            "repeat": trial["repeat"],
            "position_within_repeat": trial["position_within_repeat"],
            "arm": trial["arm"],
            "username": trial["username"],
            "treatment_session_id": trial["treatment_session_id"],
            "reconnect_session_id": trial["reconnect_session_id"],
        }
        bound.append(
            {
                **trial,
                "study_id": study_id,
                "run_id": run_id,
                "registration_sha256": registration_sha256,
                "trial_id": f"llrd-{canonical_sha256(identity)[:24]}",
                "trial_identity_sha256": canonical_sha256(identity),
            }
        )
    if len({trial["trial_id"] for trial in bound}) != len(bound):
        raise PrelaunchError("derived trial IDs are not unique")
    return bound


def _tracked_bytes(repo_root: Path, head: str, path: Path) -> bytes:
    relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
    _git(repo_root, "ls-files", "--error-unmatch", "--", relative)
    return bytes(_git(repo_root, "show", f"{head}:{relative}", binary=True))


def build_prelaunch_payload(
    registration_path: Path,
    *,
    repo_root: Path,
    expected_head: str,
    run_id: str,
    lane: dict[str, Any],
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    registration_path = registration_path.resolve()
    repo_root = repo_root.resolve()
    registration, registration_sha = load_json_strict(registration_path)
    if registration.get("status") != READY_STATUS:
        raise PrelaunchError(
            "registration is not live-ready; prelaunch creation is forbidden"
        )
    if not re.fullmatch(r"[a-z0-9]{8}", run_id):
        raise PrelaunchError("run_id must be exactly 8 lowercase alphanumeric characters")
    try:
        registration_errors = validate_registration(
            registration,
            repo_root=repo_root,
            expected_status=READY_STATUS,
        )
    except (AttributeError, KeyError, TypeError, ValueError, OSError) as exc:
        raise PrelaunchError(f"malformed registration contract: {exc}") from exc
    if registration_errors:
        raise PrelaunchError(
            "registration contract invalid: " + "; ".join(registration_errors)
        )
    validate_registration_gates(registration)
    validate_lane(lane)
    source_identity = git_source_identity(repo_root, expected_head)
    try:
        tracked_registration = _tracked_bytes(
            repo_root, expected_head, registration_path
        )
    except ValueError as exc:
        raise PrelaunchError("registration must be inside the source repository") from exc
    if hashlib.sha256(tracked_registration).hexdigest() != registration_sha:
        raise PrelaunchError("registration bytes do not match the registered Git commit")

    source_contract = registration.get("source_contract")
    source_files = source_contract.get("files") if isinstance(source_contract, dict) else None
    if not isinstance(source_files, dict) or not source_files:
        raise PrelaunchError("registration source contract is missing")
    inventory = []
    for relative, expected_sha in sorted(source_files.items()):
        relative = validate_source_relative_path(relative)
        if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
            raise PrelaunchError(f"invalid source digest for {relative}")
        path = repo_root / relative
        if not path.is_file() or path.is_symlink():
            raise PrelaunchError(f"source path missing or symlinked: {relative}")
        try:
            working_bytes = path.read_bytes()
        except OSError as exc:
            raise PrelaunchError(f"source path unreadable: {relative}") from exc
        actual = hashlib.sha256(working_bytes).hexdigest()
        if actual != expected_sha:
            raise PrelaunchError(f"source digest drift: {relative}")
        tracked_bytes = _tracked_bytes(repo_root, expected_head, path)
        if working_bytes != tracked_bytes:
            raise PrelaunchError(f"source bytes differ from Git commit: {relative}")
        inventory.append(
            {"path": relative, "size_bytes": len(working_bytes), "sha256": actual}
        )

    study_id = registration.get("study_id")
    if not isinstance(study_id, str):
        raise PrelaunchError("registration study ID is missing")
    trials = bind_trial_ids(
        derive_trial_identities(registration, run_id),
        study_id=study_id,
        run_id=run_id,
        registration_sha256=registration_sha,
    )
    for trial in trials:
        if not re.fullmatch(r"[a-z0-9_]{1,16}", trial["username"]):
            raise PrelaunchError("resolved trial username violates Kaetram limits")
        trial.update(
            {
                "mongo_database": "kaetram_e2e",
                "candidate_sha256": registration["candidate"]["sha256"],
                "content_envelope_sha256": registration["candidate"][
                    "content_envelope_sha256"
                ],
                "precondition_sha256": canonical_sha256(
                    registration["state_fixture"]["expected"]
                ),
            }
        )
    claim_contract = {
        key: registration[key]
        for key in (
            "claim_boundary",
            "reporting",
            "failure_policy",
            "verdict_algorithm",
        )
    }
    timestamp = created_at_utc or datetime.now(timezone.utc).isoformat()
    validate_timestamp(timestamp)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "study_id": study_id,
        "run_id": run_id,
        "status": SEALED_STATUS,
        "created_at_utc": timestamp,
        "registration": {
            "path": registration_path.relative_to(repo_root).as_posix(),
            "sha256": registration_sha,
            "schema_version": registration.get("schema_version"),
            "execution_status": registration.get("status"),
        },
        "claim_contract": claim_contract,
        "claim_contract_sha256": canonical_sha256(claim_contract),
        "candidate_contract_sha256": canonical_sha256(registration["candidate"]),
        "fixture_contract_sha256": canonical_sha256(registration["state_fixture"]),
        "stage_contract_sha256": canonical_sha256(
            registration["measurement"]["stages"]
        ),
        "zero_cost_contract_sha256": canonical_sha256(
            registration["zero_cost_contract"]
        ),
        "source": {
            **source_identity,
            "inventory": inventory,
            "inventory_sha256": canonical_sha256(inventory),
        },
        "lane": lane,
        "trials": trials,
        "trial_plan_sha256": canonical_sha256(trials),
        "limitations": {
            "runtime_attestation": "not_performed",
            "network_probe": "not_performed",
            "external_timestamp": "not_claimed",
            "live_results": "not_present",
        },
    }
    payload["payload_sha256"] = canonical_sha256(payload)
    return payload


def publish_json_create_only(path: Path, record: dict[str, Any]) -> str:
    path = path.resolve()
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise PrelaunchError("output parent must be an existing non-symlink directory")
    if path.exists() or path.is_symlink():
        raise PrelaunchError("refusing to overwrite an existing prelaunch receipt")
    payload = canonical_json_bytes(record) + b"\n"
    temporary = parent / f".{path.name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, path)
        temporary.unlink()
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as exc:
        raise PrelaunchError("prelaunch receipt already exists") from exc
    except OSError as exc:
        raise PrelaunchError(f"prelaunch publication failed: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
    return hashlib.sha256(payload).hexdigest()


def create_prelaunch_receipt(
    output_path: Path,
    registration_path: Path,
    *,
    repo_root: Path,
    expected_head: str,
    run_id: str,
    lane: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output_path = output_path.resolve()
    if output_path == repo_root or repo_root in output_path.parents:
        raise PrelaunchError("prelaunch output must be outside the source repository")
    payload = build_prelaunch_payload(
        registration_path,
        repo_root=repo_root,
        expected_head=expected_head,
        run_id=run_id,
        lane=dict(lane or EXPECTED_LANE),
    )
    final_identity = git_source_identity(repo_root, expected_head)
    if payload["source"]["git_head"] != final_identity["git_head"]:
        raise PrelaunchError("Git identity changed before prelaunch publication")
    publish_json_create_only(output_path, payload)
    return payload


def verify_prelaunch_receipt(
    receipt_path: Path,
    registration_path: Path,
    *,
    repo_root: Path,
    expected_head: str,
) -> list[str]:
    try:
        receipt, _ = load_json_strict(receipt_path)
        if set(receipt) != PRELAUNCH_KEYS:
            raise PrelaunchError("prelaunch receipt key set drift")
        unsigned = {key: value for key, value in receipt.items() if key != "payload_sha256"}
        if receipt.get("payload_sha256") != canonical_sha256(unsigned):
            raise PrelaunchError("prelaunch payload self-hash mismatch")
        expected = build_prelaunch_payload(
            registration_path,
            repo_root=repo_root,
            expected_head=expected_head,
            run_id=receipt.get("run_id", ""),
            lane=receipt.get("lane", {}),
            created_at_utc=receipt.get("created_at_utc"),
        )
        if receipt != expected:
            raise PrelaunchError("prelaunch receipt differs from recomputed source/design seal")
    except (PrelaunchError, TypeError, ValueError) as exc:
        return [str(exc)]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--registration", type=Path, required=True)
    create.add_argument("--repo-root", type=Path, required=True)
    create.add_argument("--expected-head", required=True)
    create.add_argument("--run-id", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--receipt", type=Path, required=True)
    verify.add_argument("--registration", type=Path, required=True)
    verify.add_argument("--repo-root", type=Path, required=True)
    verify.add_argument("--expected-head", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            receipt = create_prelaunch_receipt(
                args.output,
                args.registration,
                repo_root=args.repo_root,
                expected_head=args.expected_head,
                run_id=args.run_id,
            )
            print(f"created prelaunch-only seal: {args.output}")
            print(f"payload_sha256: {receipt['payload_sha256']}")
            return 0
        errors = verify_prelaunch_receipt(
            args.receipt,
            args.registration,
            repo_root=args.repo_root,
            expected_head=args.expected_head,
        )
    except PrelaunchError as exc:
        print(f"prelaunch refused: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("prelaunch verification FAILED", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"prelaunch verification passed: {args.receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
