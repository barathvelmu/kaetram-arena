#!/usr/bin/env python3
"""Fail-closed offline verifier for a multi-action V2 result package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.opd.live_routing_multi_action_analyzer import analyze_run
from scripts.opd.live_routing_multi_action_diagnostic import (
    canonical_sha256,
    validate_registration,
)
from scripts.opd.live_routing_multi_action_prelaunch import verify_prelaunch
from scripts.opd.live_routing_result_verify import validate_runtime_preflight


MANIFEST_SCHEMA_VERSION = "kaetram.live-routing-multi-action-manifest.v2"


class MultiActionVerificationError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MultiActionVerificationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_bytes(),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                MultiActionVerificationError(f"non-finite JSON constant: {value}")
            ),
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise MultiActionVerificationError(f"JSON artifact unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise MultiActionVerificationError(f"JSON artifact root is not an object: {path.name}")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_package(root: Path, *, repo_root: Path) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise MultiActionVerificationError("result root is missing or unsafe")
    root = root.resolve()
    manifest_path = root / "manifest.json"
    manifest = _load(manifest_path)
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise MultiActionVerificationError("manifest schema drift")
    unsigned = {key: value for key, value in manifest.items() if key != "payload_sha256"}
    if manifest.get("payload_sha256") != canonical_sha256(unsigned):
        raise MultiActionVerificationError("manifest self-hash mismatch")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise MultiActionVerificationError("manifest file inventory is missing")
    paths: list[str] = []
    total_size = 0
    for row in files:
        if not isinstance(row, dict) or set(row) != {"path", "size_bytes", "sha256"}:
            raise MultiActionVerificationError("manifest file row drift")
        relative = row["path"]
        pure = PurePosixPath(relative) if isinstance(relative, str) else None
        if pure is None or pure.is_absolute() or ".." in pure.parts or str(pure) != relative:
            raise MultiActionVerificationError("unsafe manifest path")
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise MultiActionVerificationError(f"manifest file missing or unsafe: {relative}")
        if path.stat().st_size != row["size_bytes"] or _sha(path) != row["sha256"]:
            raise MultiActionVerificationError(f"manifest file digest mismatch: {relative}")
        paths.append(relative)
        total_size += row["size_bytes"]
    if total_size > 16 * 1024 * 1024:
        raise MultiActionVerificationError("result package exceeds the registered 16 MiB cap")
    expected_receipts = [f"receipts/trial-{index:02d}.json" for index in range(1, 10)]
    expected_paths = ["analysis.json", "prelaunch.json", "registration.json", "runtime-preflight.json", *expected_receipts]
    if sorted(paths) != sorted(expected_paths) or len(set(paths)) != len(paths):
        raise MultiActionVerificationError("manifest package membership drift")
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for directory, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in directory_names:
            child = directory_path / name
            relative = child.relative_to(root).as_posix()
            if child.is_symlink() or not stat.S_ISDIR(child.lstat().st_mode):
                raise MultiActionVerificationError("package contains a symlinked or unsafe directory")
            actual_directories.add(relative)
        for name in file_names:
            child = directory_path / name
            relative = child.relative_to(root).as_posix()
            if child.is_symlink() or not stat.S_ISREG(child.lstat().st_mode):
                raise MultiActionVerificationError("package contains a symlinked or nonregular file")
            actual_files.add(relative)
    if actual_directories != {"receipts"}:
        raise MultiActionVerificationError("package directory membership drift")
    if actual_files != {"manifest.json", *expected_paths}:
        raise MultiActionVerificationError("package contains extra or missing files")
    registration = _load(root / "registration.json")
    errors = validate_registration(registration)
    if errors:
        raise MultiActionVerificationError("packaged registration invalid: " + "; ".join(errors))
    # Verification binds the packaged prelaunch to the still-available sealed
    # source tree, but does not require the checkout to remain on that head.
    prelaunch = verify_prelaunch(
        root / "prelaunch.json",
        root / "registration.json",
        repo_root=repo_root,
        require_clean_head=False,
    )
    runtime_preflight = _load(root / "runtime-preflight.json")
    validate_runtime_preflight(
        runtime_preflight,
        registration=registration,
        registration_sha256=prelaunch["registration"]["sha256"],
        prelaunch=prelaunch,
    )
    receipts = [_load(root / relative) for relative in expected_receipts]
    plans = prelaunch.get("trials")
    if not isinstance(plans, list) or len(plans) != 9:
        raise MultiActionVerificationError("prelaunch trial plan is missing")
    for receipt, plan in zip(receipts, plans, strict=True):
        if receipt.get("plan") != plan:
            raise MultiActionVerificationError("trial receipt differs from sealed prelaunch plan")
        if receipt.get("registration_sha256") != prelaunch["registration"]["sha256"]:
            raise MultiActionVerificationError("trial receipt registration identity drift")
    recomputed = analyze_run(receipts)
    observed = _load(root / "analysis.json")
    if observed != recomputed:
        raise MultiActionVerificationError("analysis differs from raw receipts")
    return {
        "verified": True,
        "verdict": recomputed["verdict"],
        "protocol_valid": recomputed["protocol_valid"],
        "full_predicate_pass": recomputed["full_predicate_pass"],
        "manifest_payload_sha256": manifest["payload_sha256"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_package(args.result_root, repo_root=args.repo_root)
    except (OSError, ValueError, MultiActionVerificationError) as exc:
        print(f"multi-action package verification failed: {exc}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
