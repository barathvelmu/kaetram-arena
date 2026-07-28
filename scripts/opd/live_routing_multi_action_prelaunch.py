#!/usr/bin/env python3
"""Create-only source seal and nine-trial plan for multi-action V2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any, Mapping

from scripts.opd.live_routing_multi_action_diagnostic import (
    SCHEMA_VERSION,
    SOURCE_PATHS,
    STATUS,
    canonical_sha256,
    expected_trial_identities,
    validate_registration,
)


PRELAUNCH_SCHEMA_VERSION = "kaetram.live-routing-multi-action-prelaunch.v2"
RUN_ID_RE = re.compile(r"[0-9a-f]{8}")


class MultiActionPrelaunchError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MultiActionPrelaunchError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise MultiActionPrelaunchError(f"non-finite JSON constant: {value}")


def _load_json_strict(path: Path, *, label: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise MultiActionPrelaunchError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise MultiActionPrelaunchError(f"{label} must be a regular non-symlink file")
    try:
        value = json.loads(
            path.read_bytes(),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise MultiActionPrelaunchError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise MultiActionPrelaunchError(f"{label} root must be an object")
    return value


def source_inventory(repo_root: Path) -> list[dict[str, Any]]:
    root = repo_root.resolve()
    rows: list[dict[str, Any]] = []
    for relative in SOURCE_PATHS:
        path = root / relative
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise MultiActionPrelaunchError(f"registered source unavailable: {relative}") from exc
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise MultiActionPrelaunchError(f"registered source is not a regular file: {relative}")
        rows.append(
            {"path": relative, "size_bytes": metadata.st_size, "sha256": sha256_file(path)}
        )
    return rows


def trial_plan(registration: Mapping[str, Any], run_id: str) -> list[dict[str, Any]]:
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise MultiActionPrelaunchError("run_id must be exactly eight lowercase hex characters")
    rows: list[dict[str, Any]] = []
    for identity in expected_trial_identities():
        row = {
            key: value
            for key, value in identity.items()
            if not key.endswith("_template")
        }
        row.update(
            {
                "trial_id": f"llrma-{run_id}-t{identity['schedule_index']:02d}",
                "username": identity["username_template"].format(run_id=run_id),
                "treatment_session_id": identity[
                    "treatment_session_id_template"
                ].format(run_id=run_id),
                "reconnect_session_id": identity[
                    "reconnect_session_id_template"
                ].format(run_id=run_id),
                "mongo_database": registration["zero_cost_contract"]["mongo_database"],
            }
        )
        rows.append(row)
    return rows


def build_prelaunch(
    registration: Mapping[str, Any],
    *,
    registration_raw_sha256: str,
    repo_root: Path,
    git_head: str,
    run_id: str,
) -> dict[str, Any]:
    errors = validate_registration(registration)
    if errors:
        raise MultiActionPrelaunchError("registration invalid: " + "; ".join(errors))
    if registration.get("status") != STATUS:
        raise MultiActionPrelaunchError("registration is not registered for prelaunch")
    if not re.fullmatch(r"[0-9a-f]{40}", git_head):
        raise MultiActionPrelaunchError("git head is not a full commit identity")
    if not re.fullmatch(r"[0-9a-f]{64}", registration_raw_sha256):
        raise MultiActionPrelaunchError("registration digest is malformed")
    sources = source_inventory(repo_root)
    plans = trial_plan(registration, run_id)
    receipt: dict[str, Any] = {
        "schema_version": PRELAUNCH_SCHEMA_VERSION,
        "study_id": registration["study_id"],
        "run_id": run_id,
        "registration": {
            "schema_version": SCHEMA_VERSION,
            "sha256": registration_raw_sha256,
        },
        "claim_contract_sha256": canonical_sha256(registration["claim_boundary"]),
        "git_head": git_head,
        "worktree_clean": True,
        "source_inventory": sources,
        "source_inventory_sha256": canonical_sha256(sources),
        "trials": plans,
        "trial_plan_sha256": canonical_sha256(plans),
        "authorization": "source_sealed_after_clean_commit_before_live_services",
    }
    receipt["payload_sha256"] = canonical_sha256(receipt)
    return receipt


def _git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MultiActionPrelaunchError("git source attestation failed") from exc


def create_prelaunch(
    registration_path: Path, output_path: Path, *, repo_root: Path, run_id: str
) -> dict[str, Any]:
    root = repo_root.resolve()
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise MultiActionPrelaunchError("repo_root is not the exact Git toplevel")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise MultiActionPrelaunchError("source worktree must be completely clean")
    registration = _load_json_strict(registration_path, label="registration")
    registration_raw = registration_path.read_bytes()
    receipt = build_prelaunch(
        registration,
        registration_raw_sha256=hashlib.sha256(registration_raw).hexdigest(),
        repo_root=root,
        git_head=_git(root, "rev-parse", "--verify", "HEAD^{commit}"),
        run_id=run_id,
    )
    raw = json.dumps(receipt, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    output = output_path.resolve()
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise MultiActionPrelaunchError("prelaunch parent must be an existing regular directory")
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise MultiActionPrelaunchError("refusing to overwrite prelaunch receipt") from exc
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return receipt


def verify_prelaunch(
    prelaunch_path: Path,
    registration_path: Path,
    *,
    repo_root: Path,
    require_clean_head: bool = True,
) -> dict[str, Any]:
    receipt = _load_json_strict(prelaunch_path, label="prelaunch receipt")
    if not isinstance(receipt, dict) or receipt.get("schema_version") != PRELAUNCH_SCHEMA_VERSION:
        raise MultiActionPrelaunchError("prelaunch schema drift")
    unsigned = {key: value for key, value in receipt.items() if key != "payload_sha256"}
    if receipt.get("payload_sha256") != canonical_sha256(unsigned):
        raise MultiActionPrelaunchError("prelaunch self-hash mismatch")
    registration = _load_json_strict(registration_path, label="registration")
    registration_raw = registration_path.read_bytes()
    if receipt.get("registration") != {
        "schema_version": SCHEMA_VERSION,
        "sha256": hashlib.sha256(registration_raw).hexdigest(),
    }:
        raise MultiActionPrelaunchError("prelaunch registration identity mismatch")
    expected_sources = source_inventory(repo_root)
    if receipt.get("source_inventory") != expected_sources or receipt.get(
        "source_inventory_sha256"
    ) != canonical_sha256(expected_sources):
        raise MultiActionPrelaunchError("prelaunch source inventory drift")
    expected_plans = trial_plan(registration, receipt.get("run_id"))
    if receipt.get("trials") != expected_plans or receipt.get(
        "trial_plan_sha256"
    ) != canonical_sha256(expected_plans):
        raise MultiActionPrelaunchError("prelaunch trial plan drift")
    if require_clean_head:
        root = repo_root.resolve()
        if _git(root, "rev-parse", "--verify", "HEAD^{commit}") != receipt.get("git_head"):
            raise MultiActionPrelaunchError("prelaunch Git head drift")
        if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise MultiActionPrelaunchError("prelaunch source worktree is no longer clean")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = create_prelaunch(
            args.registration, args.output, repo_root=args.repo_root, run_id=args.run_id
        )
    except (OSError, ValueError, MultiActionPrelaunchError) as exc:
        print(f"multi-action prelaunch refused: {exc}")
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
