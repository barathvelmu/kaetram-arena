#!/usr/bin/env python3
"""Independently verify a V3 trigger-incidence registration/design package."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import trigger_incidence_probe as v1  # noqa: E402
from scripts.opd import trigger_incidence_probe_v2 as v2  # noqa: E402
from scripts.opd import prepare_trigger_incidence_v3 as prepare  # noqa: E402


ProbeError = v1.ProbeError
EXPECTED_FILENAMES = {
    "effective-registration.json",
    "design.json",
    "design.receipt.json",
    "v3-preparation.receipt.json",
}


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _read(path: Path) -> dict:
    value = prepare._read_json(path)
    if not isinstance(value, dict):
        raise ProbeError(f"expected JSON object: {path}")
    return value


def _assert_execution_ready(
    registration_path: Path, design_dir: Path, design: dict
) -> str:
    if _git(["status", "--porcelain"]):
        raise ProbeError("execution gate requires a clean checkout")
    head = _git(["rev-parse", "HEAD"])
    try:
        upstream = _git(["rev-parse", "@{upstream}"])
    except subprocess.CalledProcessError as exc:
        raise ProbeError("execution gate requires a pushed upstream") from exc
    if upstream != head:
        raise ProbeError("execution gate requires HEAD to equal its pushed upstream")
    paths = [registration_path, *(design_dir / name for name in EXPECTED_FILENAMES)]
    for path in paths:
        try:
            relative = path.resolve().relative_to(REPO.resolve()).as_posix()
        except ValueError as exc:
            raise ProbeError("execution-gate files must be inside the repository") from exc
        if _git(["ls-files", "--error-unmatch", relative]) != relative:
            raise ProbeError(f"execution-gate file is not tracked: {relative}")
        committed = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=REPO,
            check=True,
            capture_output=True,
        ).stdout
        if committed != path.read_bytes():
            raise ProbeError(f"execution-gate file differs from HEAD: {relative}")
    source_commit = str(design.get("source_git_commit", ""))
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", source_commit, head],
        cwd=REPO,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise ProbeError("registration commit is not an ancestor of design HEAD")
    return head


def verify(
    registration_path: Path,
    historical_root: Path,
    design_dir: Path,
    *,
    require_execution_ready: bool = False,
) -> dict:
    registration, registration_sha256 = prepare.load_registration(registration_path)
    source_audit = prepare.verify_source_archive(registration, historical_root)
    if not design_dir.is_dir():
        raise ProbeError("V3 design directory does not exist")
    actual_names = {path.name for path in design_dir.iterdir() if path.is_file()}
    if actual_names != EXPECTED_FILENAMES:
        raise ProbeError("V3 design directory has missing or unexpected files")

    effective_path = design_dir / "effective-registration.json"
    effective = _read(effective_path)
    expected_effective = prepare.materialize_effective_registration(registration)
    if effective != expected_effective:
        raise ProbeError("effective V3 registration is not the frozen V2 transformation")
    expected_effective_bytes = (
        json.dumps(expected_effective, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    if effective_path.read_bytes() != expected_effective_bytes:
        raise ProbeError("effective V3 registration is not canonically serialized")
    effective_sha256 = v1.sha256_file(effective_path)

    design_path = design_dir / "design.json"
    design = v2.load_design(
        design_path,
        effective,
        effective_sha256,
        historical_root=None,
    )
    expected_design = v2._derive_design(
        effective,
        effective_sha256,
        historical_root,
        {
            "source_git_commit": design["source_git_commit"],
            "dirty_paths": [],
        },
    )
    if design != expected_design:
        raise ProbeError("V3 design does not rederive from the registered source archive")

    excluded = set(effective["state_pool"]["excluded_source_logs"])
    selected = {state["source_log"] for state in design["states"]}
    prepare.require_zero_panel_overlap(selected, excluded)
    prepare.require_identity_safe_design(design)
    if len(selected) != 20:
        raise ProbeError("V3 design does not contain 20 unique states")

    design_receipt = _read(design_dir / "design.receipt.json")
    preparation_receipt = _read(design_dir / "v3-preparation.receipt.json")
    expected_preparation = {
        "schema_version": prepare.PREPARATION_SCHEMA,
        "study_id": registration["study_id"],
        "v3_registration_sha256": registration_sha256,
        "effective_registration_sha256": effective_sha256,
        "design_sha256": v1.sha256_file(design_path),
        "selected_source_tree_sha256": v1._source_tree_sha256(design["states"]),
        "source_audit": source_audit,
        "v2_overlap_count": 0,
        "outcomes_inspected_for_selection": False,
        "execution_authorized": False,
        "next_gate": (
            "Commit and push this complete design package, then run the independent "
            "V3 verifier with --require-execution-ready before issuing requests."
        ),
        "source_git_commit": design["source_git_commit"],
        "dirty_paths": [],
    }
    if preparation_receipt != expected_preparation:
        raise ProbeError("V3 preparation receipt does not match the exact package")
    if design_receipt.get("design_sha256") != expected_preparation["design_sha256"]:
        raise ProbeError("V2-compatible and V3 preparation receipts disagree")

    execution_commit = None
    if require_execution_ready:
        execution_commit = _assert_execution_ready(
            registration_path, design_dir, design
        )
    return {
        "schema_version": "kaetram.local-trigger-incidence-v3-verification.v1",
        "study_id": registration["study_id"],
        "source_run_id": registration["state_pool"]["source_run_id"],
        "source_role": registration["state_pool"]["source_role"],
        "source_model": "2b-opd-r3",
        "state_count": len(design["states"]),
        "reconstructable_decision_state_count": source_audit[
            "reconstructable_decision_state_count"
        ],
        "v2_overlap_count": 0,
        "matched_source_logs_sha256": source_audit[
            "matched_source_logs_sha256"
        ],
        "eligible_source_logs_sha256": source_audit[
            "eligible_source_logs_sha256"
        ],
        "design_sha256": expected_preparation["design_sha256"],
        "outcomes_inspected_for_selection": False,
        "execution_ready": require_execution_ready,
        "execution_commit": execution_commit,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registration", type=Path, default=REPO / prepare.REGISTRATION_PATH
    )
    parser.add_argument("--historical-root", type=Path, required=True)
    parser.add_argument("--design-dir", type=Path, required=True)
    parser.add_argument("--require-execution-ready", action="store_true")
    args = parser.parse_args()
    result = verify(
        args.registration,
        args.historical_root,
        args.design_dir,
        require_execution_ready=args.require_execution_ready,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
