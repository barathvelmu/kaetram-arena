from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.opd.live_routing_analyzer import analyze_run, canonical_json_bytes, canonical_sha256
from scripts.opd.live_routing_prelaunch import EXPECTED_LANE, build_prelaunch_payload
from scripts.opd.live_routing_result_verify import (
    MANIFEST_SCHEMA_VERSION,
    RUNTIME_PREFLIGHT_SCHEMA_VERSION,
    main,
    verify_package,
)
from scripts.opd.live_routing_services import MONGO_IMAGE
from tests.unit.test_live_routing_analyzer import _resign, _unsigned_receipt
from tests.unit.test_live_routing_prelaunch import _ready_repo


def _write_canonical(path: Path, value: dict) -> bytes:
    raw = canonical_json_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _services_evidence(live: dict, *, client_file_count: int = 25) -> dict:
    container = "kaetram-live-mongo-offline001"
    evidence = {
        "schema_version": "kaetram.live-routing-services.v1",
        "lane": {
            "host": "127.0.0.1",
            "mongo_port": 27017,
            "client_port": 9000,
            "game_port": 9191,
            "mongo_database": "kaetram_e2e",
            "model_calls": 0,
            "remote_endpoints": 0,
        },
        "mongo_image": MONGO_IMAGE,
        "mongo_image_attestation": {
            "reference": MONGO_IMAGE,
            "image_id": "sha256:" + "9" * 64,
            "architecture": "arm64",
            "os": "linux",
        },
        "container_name": container,
        "services": {
            "mongo": {
                "command": [
                    "$DOCKER", "run", "--rm", "--name", container,
                    "--pull=never", "--publish", "127.0.0.1:27017:27017",
                    "--mount", "type=bind,source=$RUN_ROOT/mongo-data,target=/data/db",
                    "sha256:" + "9" * 64, "--bind_ip_all", "--port", "27017",
                ],
                "pid": 4100,
                "process_group": 4100,
                "cwd": "$RUN_ROOT",
            },
            "client": {
                "command": [
                    "$PYTHON", "-m", "http.server", "9000", "--bind",
                    "127.0.0.1", "--directory",
                    "$GAME_ROOT/packages/client/dist",
                ],
                "pid": 4101,
                "process_group": 4101,
                "cwd": "$RUN_ROOT",
            },
            "game": {
                "command": [
                    "$NODE20", "--enable-source-maps",
                    "$GAME_ROOT/packages/server/dist/main.js", "--host",
                    "127.0.0.1", "--port", "9191",
                ],
                "pid": 4102,
                "process_group": 4102,
                "cwd": "$GAME_ROOT/packages/server",
            },
        },
        "environment": {
            "NODE_ENV": "e2e", "HOST": "127.0.0.1", "PORT": "9191",
            "SKIP_DATABASE": "false", "DATABASE": "mongodb",
            "MONGODB_HOST": "127.0.0.1", "MONGODB_PORT": "27017",
            "MONGODB_DATABASE": "kaetram_e2e", "MONGODB_SRV": "false",
            "MONGODB_TLS": "false", "API_ENABLED": "false",
            "HUB_ENABLED": "false", "DISCORD_ENABLED": "false",
        },
        "identity": {
            "game_revision": live["game_revision"],
            "server_bundle_sha256": live["game_bundle_sha256"],
            "client_dist_file_count": client_file_count,
            "client_dist_inventory_sha256": live["client_dist_inventory_sha256"],
            "docker_client_version": live["docker_client_version"],
            "docker_executable_sha256": live["docker_executable_sha256"],
            "python_executable_sha256": live["python_executable_sha256"],
            "node_version": live["node_version"],
            "node_executable_sha256": live["node_executable_sha256"],
        },
    }
    evidence["payload_sha256"] = canonical_sha256(evidence)
    return evidence


def _complete_package(
    tmp_path: Path,
) -> tuple[Path, Path, Path, str]:
    repo, registration_path, head = _ready_repo(tmp_path)
    registration = json.loads(registration_path.read_text())
    prelaunch = build_prelaunch_payload(
        registration_path,
        repo_root=repo,
        expected_head=head,
        run_id="local001",
        lane=EXPECTED_LANE,
    )
    package = tmp_path / "package"
    package.mkdir()
    prelaunch_raw = _write_canonical(package / "prelaunch.json", prelaunch)
    live = registration["live_contract"]
    runtime_preflight = {
        "schema_version": RUNTIME_PREFLIGHT_SCHEMA_VERSION,
        "study_id": prelaunch["study_id"],
        "run_id": prelaunch["run_id"],
        "registration_sha256": prelaunch["registration"]["sha256"],
        "prelaunch_payload_sha256": prelaunch["payload_sha256"],
        "game": {
            "git_head": live["game_revision"],
            "worktree_clean": True,
            "bundle_path": "packages/server/dist/main.js",
            "bundle_size_bytes": 1234,
            "bundle_sha256": live["game_bundle_sha256"],
            "client_dist_file_count": 25,
            "client_dist_inventory_sha256": live[
                "client_dist_inventory_sha256"
            ],
        },
        "mongo": {
            "uri": "mongodb://127.0.0.1:27017/kaetram_e2e",
            "database": "kaetram_e2e",
            "nodes": [{"host": "127.0.0.1", "port": 27017}],
            "loopback_only": True,
        },
        "python": {
            key: live[key]
            for key in (
                "python_version",
                "python_executable_sha256",
                "mcp_version",
                "playwright_version",
                "pymongo_version",
            )
        },
        "services": _services_evidence(live),
    }
    runtime_preflight["payload_sha256"] = canonical_sha256(runtime_preflight)
    runtime_preflight_raw = _write_canonical(
        package / "runtime-preflight.json", runtime_preflight
    )
    receipts = [
        _unsigned_receipt(registration, prelaunch, plan)
        for plan in prelaunch["trials"]
    ]
    _resign(receipts, prelaunch)
    entries = []
    for index, receipt in enumerate(receipts, start=1):
        relative = f"receipts/trial-{index:02d}.json"
        raw = _write_canonical(package / relative, receipt)
        entries.append(
            {
                "schedule_index": index,
                "path": relative,
                "file_sha256": hashlib.sha256(raw).hexdigest(),
                "receipt_payload_sha256": receipt["payload_sha256"],
            }
        )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "study_id": prelaunch["study_id"],
        "run_id": prelaunch["run_id"],
        "registration_sha256": prelaunch["registration"]["sha256"],
        "prelaunch_file_sha256": hashlib.sha256(prelaunch_raw).hexdigest(),
        "prelaunch_payload_sha256": prelaunch["payload_sha256"],
        "runtime_preflight_file_sha256": hashlib.sha256(
            runtime_preflight_raw
        ).hexdigest(),
        "runtime_preflight_payload_sha256": runtime_preflight["payload_sha256"],
        "claim_contract_sha256": prelaunch["claim_contract_sha256"],
        "trial_plan_sha256": prelaunch["trial_plan_sha256"],
        "entries": entries,
        "final_chain_head": receipts[-1]["payload_sha256"],
    }
    manifest["payload_sha256"] = canonical_sha256(manifest)
    _write_canonical(package / "manifest.json", manifest)
    analysis = analyze_run(
        registration,
        prelaunch,
        receipts,
        manifest_payload_sha256=manifest["payload_sha256"],
    )
    _write_canonical(package / "analysis.json", analysis)
    return package, registration_path, repo, head


def _resign_runtime_preflight_and_manifest(package: Path, preflight: dict) -> None:
    preflight["payload_sha256"] = canonical_sha256(
        {key: value for key, value in preflight.items() if key != "payload_sha256"}
    )
    raw = _write_canonical(package / "runtime-preflight.json", preflight)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["runtime_preflight_file_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["runtime_preflight_payload_sha256"] = preflight["payload_sha256"]
    manifest["payload_sha256"] = canonical_sha256(
        {key: value for key, value in manifest.items() if key != "payload_sha256"}
    )
    _write_canonical(manifest_path, manifest)


def test_complete_package_verifies_with_source_seal(tmp_path: Path) -> None:
    package, registration, repo, head = _complete_package(tmp_path)
    assert verify_package(
        package,
        registration,
        repo_root=repo,
        expected_head=head,
    ) == []


def test_cli_requires_external_source_seal_arguments() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--package", "package", "--registration", "registration.json"])
    assert exc_info.value.code == 2


def test_extra_file_or_symlink_refuses_package(tmp_path: Path) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    (package / "posthoc.txt").write_text("exclude trial 1\n")
    assert "package file set drift" in verify_package(package, registration)[0]
    (package / "posthoc.txt").unlink()
    os.symlink(package / "analysis.json", package / "posthoc-link.json")
    assert "package contains symlink" in verify_package(package, registration)[0]


def test_duplicate_json_key_refuses_package(tmp_path: Path) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    analysis = (package / "analysis.json").read_text().rstrip()
    (package / "analysis.json").write_text(
        analysis[:-1] + ',"verdict":"posthoc"}\n'
    )
    assert "duplicate JSON key" in verify_package(package, registration)[0]


def test_nonfinite_json_constant_refuses_package(tmp_path: Path) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    analysis = (package / "analysis.json").read_text().rstrip()
    (package / "analysis.json").write_text(
        analysis[:-1] + ',"posthoc_nonfinite":NaN}\n'
    )
    assert "non-finite JSON constant" in verify_package(package, registration)[0]


def test_receipt_mutation_breaks_manifest_even_if_receipt_is_rehashed(
    tmp_path: Path,
) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    path = package / "receipts/trial-01.json"
    receipt = json.loads(path.read_text())
    receipt["routing"]["protocol_success"] = False
    receipt["payload_sha256"] = canonical_sha256(
        {key: value for key, value in receipt.items() if key != "payload_sha256"}
    )
    _write_canonical(path, receipt)
    assert "trial file digest mismatch" in verify_package(package, registration)[0]


def test_analysis_claim_cannot_be_rewritten(tmp_path: Path) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    path = package / "analysis.json"
    analysis = json.loads(path.read_text())
    analysis["verdict"] = "complete_with_failures"
    analysis["analysis_payload_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in analysis.items()
            if key != "analysis_payload_sha256"
        }
    )
    _write_canonical(path, analysis)
    assert "differs from deterministic recomputation" in verify_package(
        package, registration
    )[0]


def test_rehashed_runtime_preflight_drift_is_still_rejected(tmp_path: Path) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    preflight_path = package / "runtime-preflight.json"
    preflight = json.loads(preflight_path.read_text())
    preflight["game"]["bundle_sha256"] = "f" * 64
    preflight["payload_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in preflight.items()
            if key != "payload_sha256"
        }
    )
    raw = _write_canonical(preflight_path, preflight)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["runtime_preflight_file_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["runtime_preflight_payload_sha256"] = preflight["payload_sha256"]
    manifest["payload_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in manifest.items()
            if key != "payload_sha256"
        }
    )
    _write_canonical(manifest_path, manifest)
    assert "runtime game preflight differs from registration" in verify_package(
        package, registration
    )[0]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda services: services["lane"].__setitem__("game_port", 443),
            "escaped the zero-cost lane",
        ),
        (
            lambda services: services["identity"].__setitem__(
                "game_revision", "f" * 40
            ),
            "identity differs from runtime preflight",
        ),
        (
            lambda services: services["services"]["game"]["command"].append(
                "https://example.invalid"
            ),
            "owned game command drift",
        ),
        (
            lambda services: services["identity"].__setitem__(
                "node_version", "v20.20.1"
            ),
            "identity differs from runtime preflight",
        ),
        (
            lambda services: services["identity"].__setitem__(
                "docker_client_version", "Docker version 29.2.0, build stale"
            ),
            "identity differs from runtime preflight",
        ),
    ],
)
def test_rehashed_owned_service_drift_is_rejected(
    tmp_path: Path, mutation, message: str
) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    preflight = json.loads((package / "runtime-preflight.json").read_text())
    mutation(preflight["services"])
    preflight["services"]["payload_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in preflight["services"].items()
            if key != "payload_sha256"
        }
    )
    _resign_runtime_preflight_and_manifest(package, preflight)
    assert message in verify_package(package, registration)[0]


def test_owned_service_evidence_hash_is_required(tmp_path: Path) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    preflight = json.loads((package / "runtime-preflight.json").read_text())
    preflight["services"]["payload_sha256"] = "0" * 64
    _resign_runtime_preflight_and_manifest(package, preflight)
    assert "owned-service evidence self-hash mismatch" in verify_package(
        package, registration
    )[0]
