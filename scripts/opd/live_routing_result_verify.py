#!/usr/bin/env python3
"""Offline verifier for a completed local live-routing diagnostic package."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.opd.live_routing_analyzer import (  # noqa: E402
    AnalysisError,
    analyze_run,
    canonical_json_bytes,
    canonical_sha256,
)
from scripts.opd.live_routing_diagnostic import (  # noqa: E402
    LIVE_READY_ADDITIONAL_SOURCE_PATHS,
    load_registration_strict,
    validate_registration,
)
from scripts.opd.live_routing_prelaunch import (  # noqa: E402
    EXPECTED_LANE,
    PRELAUNCH_KEYS,
    READY_STATUS,
    PrelaunchError,
    bind_trial_ids,
    derive_trial_identities,
    validate_lane,
    verify_prelaunch_receipt,
)


MANIFEST_SCHEMA_VERSION = "kaetram.live-routing-diagnostic-manifest.v1"
MANIFEST_KEYS = {
    "schema_version",
    "study_id",
    "run_id",
    "registration_sha256",
    "prelaunch_file_sha256",
    "prelaunch_payload_sha256",
    "claim_contract_sha256",
    "trial_plan_sha256",
    "entries",
    "final_chain_head",
    "payload_sha256",
}
ENTRY_KEYS = {
    "schedule_index",
    "path",
    "file_sha256",
    "receipt_payload_sha256",
}


class VerificationError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise VerificationError(f"non-finite JSON constant: {value}")


def load_json_strict(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise VerificationError(f"required artifact is missing or symlinked: {path}")
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise VerificationError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"JSON artifact root is not an object: {path}")
    return value, raw


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _verify_self_hash(record: dict[str, Any], field: str, label: str) -> None:
    unsigned = {key: value for key, value in record.items() if key != field}
    if record.get(field) != canonical_sha256(unsigned):
        raise VerificationError(f"{label} self-hash mismatch")


def _expected_trials(
    registration: dict[str, Any], registration_sha: str, run_id: str
) -> list[dict[str, Any]]:
    trials = bind_trial_ids(
        derive_trial_identities(registration, run_id),
        study_id=registration["study_id"],
        run_id=run_id,
        registration_sha256=registration_sha,
    )
    for trial in trials:
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
    return trials


def verify_package_or_raise(
    package_dir: Path,
    registration_path: Path,
    *,
    repo_root: Path | None = None,
    expected_head: str | None = None,
) -> dict[str, Any]:
    """Verify a package without contacting any runtime service."""

    if package_dir.is_symlink() or not package_dir.is_dir():
        raise VerificationError("package root must be an existing non-symlink directory")
    package_dir = package_dir.resolve()
    expected_paths = {
        "prelaunch.json",
        "manifest.json",
        "analysis.json",
        *{f"receipts/trial-{index:02d}.json" for index in range(1, 10)},
    }
    actual_paths: set[str] = set()
    for path in package_dir.rglob("*"):
        if path.is_symlink():
            raise VerificationError(f"package contains symlink: {path}")
        if path.is_file():
            actual_paths.add(path.relative_to(package_dir).as_posix())
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        raise VerificationError(f"package file set drift: missing={missing}, extra={extra}")

    registration, registration_raw = load_json_strict(registration_path)
    registration_sha = _sha256_bytes(registration_raw)
    registration_errors = validate_registration(
        registration, expected_status=READY_STATUS
    )
    if registration_errors:
        raise VerificationError(
            "registration contract invalid: " + "; ".join(registration_errors)
        )
    for relative in LIVE_READY_ADDITIONAL_SOURCE_PATHS:
        if relative not in registration["source_contract"]["files"]:
            raise VerificationError(f"live-ready source missing: {relative}")

    prelaunch_path = package_dir / "prelaunch.json"
    prelaunch, prelaunch_raw = load_json_strict(prelaunch_path)
    if set(prelaunch) != PRELAUNCH_KEYS:
        raise VerificationError("prelaunch key set drift")
    _verify_self_hash(prelaunch, "payload_sha256", "prelaunch")
    if prelaunch.get("registration", {}).get("sha256") != registration_sha:
        raise VerificationError("prelaunch registration digest mismatch")
    if prelaunch.get("status") != "sealed_prelaunch_configuration_only":
        raise VerificationError("prelaunch status drift")
    if prelaunch.get("registration", {}).get("execution_status") != READY_STATUS:
        raise VerificationError("prelaunch did not seal a live-ready registration")
    if prelaunch.get("claim_contract") != {
        key: registration[key]
        for key in ("claim_boundary", "reporting", "failure_policy", "verdict_algorithm")
    }:
        raise VerificationError("prelaunch claim contract drift")
    if prelaunch.get("claim_contract_sha256") != canonical_sha256(
        prelaunch["claim_contract"]
    ):
        raise VerificationError("prelaunch claim contract digest mismatch")
    if prelaunch.get("candidate_contract_sha256") != canonical_sha256(
        registration["candidate"]
    ):
        raise VerificationError("candidate contract digest mismatch")
    if prelaunch.get("fixture_contract_sha256") != canonical_sha256(
        registration["state_fixture"]
    ):
        raise VerificationError("fixture contract digest mismatch")
    if prelaunch.get("stage_contract_sha256") != canonical_sha256(
        registration["measurement"]["stages"]
    ):
        raise VerificationError("stage contract digest mismatch")
    if prelaunch.get("zero_cost_contract_sha256") != canonical_sha256(
        registration["zero_cost_contract"]
    ):
        raise VerificationError("zero-cost contract digest mismatch")
    validate_lane(prelaunch.get("lane", {}))
    expected_trials = _expected_trials(
        registration, registration_sha, prelaunch.get("run_id", "")
    )
    if canonical_json_bytes(prelaunch.get("trials")) != canonical_json_bytes(
        expected_trials
    ):
        raise VerificationError("prelaunch trial plan drift")
    if prelaunch.get("trial_plan_sha256") != canonical_sha256(expected_trials):
        raise VerificationError("prelaunch trial-plan digest mismatch")

    if (repo_root is None) != (expected_head is None):
        raise VerificationError("repo_root and expected_head must be supplied together")
    if repo_root is not None and expected_head is not None:
        errors = verify_prelaunch_receipt(
            prelaunch_path,
            registration_path,
            repo_root=repo_root,
            expected_head=expected_head,
        )
        if errors:
            raise VerificationError("source/design seal invalid: " + "; ".join(errors))

    manifest, _ = load_json_strict(package_dir / "manifest.json")
    if set(manifest) != MANIFEST_KEYS:
        raise VerificationError("manifest key set drift")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise VerificationError("manifest schema drift")
    _verify_self_hash(manifest, "payload_sha256", "manifest")
    expected_manifest_refs = {
        "study_id": prelaunch["study_id"],
        "run_id": prelaunch["run_id"],
        "registration_sha256": registration_sha,
        "prelaunch_file_sha256": _sha256_bytes(prelaunch_raw),
        "prelaunch_payload_sha256": prelaunch["payload_sha256"],
        "claim_contract_sha256": prelaunch["claim_contract_sha256"],
        "trial_plan_sha256": prelaunch["trial_plan_sha256"],
    }
    for key, expected in expected_manifest_refs.items():
        if manifest.get(key) != expected:
            raise VerificationError(f"manifest reference drift: {key}")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or len(entries) != 9:
        raise VerificationError("manifest must contain exactly nine entries")

    receipts = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise VerificationError(f"manifest entry key set drift: {index}")
        expected_path = f"receipts/trial-{index:02d}.json"
        if (
            canonical_json_bytes(entry.get("schedule_index"))
            != canonical_json_bytes(index)
            or entry.get("path") != expected_path
        ):
            raise VerificationError(f"manifest entry order/path drift: {index}")
        receipt, raw = load_json_strict(package_dir / expected_path)
        if entry.get("file_sha256") != _sha256_bytes(raw):
            raise VerificationError(f"trial file digest mismatch: {index}")
        if entry.get("receipt_payload_sha256") != receipt.get("payload_sha256"):
            raise VerificationError(f"trial payload digest mismatch: {index}")
        receipts.append(receipt)
    if manifest.get("final_chain_head") != receipts[-1].get("payload_sha256"):
        raise VerificationError("manifest final chain head mismatch")

    try:
        recomputed = analyze_run(
            registration,
            prelaunch,
            receipts,
            manifest_payload_sha256=manifest["payload_sha256"],
        )
    except AnalysisError as exc:
        raise VerificationError(str(exc)) from exc
    analysis, _ = load_json_strict(package_dir / "analysis.json")
    if canonical_json_bytes(analysis) != canonical_json_bytes(recomputed):
        raise VerificationError("analysis differs from deterministic recomputation")
    if not _is_sha256(analysis.get("analysis_payload_sha256")):
        raise VerificationError("analysis payload digest is invalid")
    return recomputed


def verify_package(
    package_dir: Path,
    registration_path: Path,
    *,
    repo_root: Path | None = None,
    expected_head: str | None = None,
) -> list[str]:
    try:
        verify_package_or_raise(
            package_dir,
            registration_path,
            repo_root=repo_root,
            expected_head=expected_head,
        )
    except (
        VerificationError,
        AnalysisError,
        PrelaunchError,
        AttributeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        return [str(exc)]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--expected-head")
    args = parser.parse_args(argv)
    errors = verify_package(
        args.package,
        args.registration,
        repo_root=args.repo_root,
        expected_head=args.expected_head,
    )
    if errors:
        print("live routing package verification FAILED", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"live routing package verification passed: {args.package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
