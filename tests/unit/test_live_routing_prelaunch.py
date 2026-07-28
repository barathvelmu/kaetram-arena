from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts.opd.live_routing_diagnostic import (
    DESIGN_SOURCE_PATHS,
    LIVE_READY_ADDITIONAL_SOURCE_PATHS,
    REPO_ROOT,
    STATUS,
)
from scripts.opd.live_routing_prelaunch import (
    EXPECTED_LANE,
    READY_STATUS,
    PrelaunchError,
    build_prelaunch_payload,
    create_prelaunch_receipt,
    load_json_strict,
    require_loopback_uri,
    validate_source_relative_path,
    verify_prelaunch_receipt,
)


REGISTRATION = (
    REPO_ROOT / "research/experiments/local-live-routing-diagnostic-v1.json"
)


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _ready_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    registration = json.loads(REGISTRATION.read_text())
    registration["status"] = READY_STATUS
    for relative in (*DESIGN_SOURCE_PATHS, *LIVE_READY_ADDITIONAL_SOURCE_PATHS):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            source = REPO_ROOT / relative
            target.write_bytes(source.read_bytes())
        registration["source_contract"]["files"][relative] = ""
    for relative in registration["source_contract"]["files"]:
        target = repo / relative
        if not target.exists():
            source = REPO_ROOT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        registration["source_contract"]["files"][relative] = hashlib.sha256(
            target.read_bytes()
        ).hexdigest()
    registration_path = (
        repo / "research/experiments/local-live-routing-diagnostic-v1.json"
    )
    registration_path.parent.mkdir(parents=True)
    registration_path.write_text(json.dumps(registration, indent=2) + "\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Prelaunch Test")
    _git(repo, "config", "user.email", "prelaunch@example.invalid")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "frozen source")
    return repo, registration_path, _git(repo, "rev-parse", "HEAD")


def test_design_status_registration_cannot_create_receipt(tmp_path: Path) -> None:
    design_registration = json.loads(REGISTRATION.read_text())
    design_registration["status"] = STATUS
    registration_path = tmp_path / "design-registration.json"
    registration_path.write_text(json.dumps(design_registration))
    output = tmp_path / "receipt.json"
    with pytest.raises(PrelaunchError, match="not live-ready"):
        create_prelaunch_receipt(
            output,
            registration_path,
            repo_root=REPO_ROOT,
            expected_head="0" * 40,
            run_id="local001",
        )
    assert not output.exists()


def test_strict_json_rejects_duplicate_keys_and_non_finite_values(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a":1,"a":2}\n')
    with pytest.raises(PrelaunchError, match="duplicate JSON key"):
        load_json_strict(duplicate)
    non_finite = tmp_path / "nan.json"
    non_finite.write_text('{"a":NaN}\n')
    with pytest.raises(PrelaunchError, match="non-finite JSON constant"):
        load_json_strict(non_finite)


@pytest.mark.parametrize("path", ["../escape.py", "/tmp/escape.py", "a\\b.py"])
def test_source_contract_paths_cannot_escape(path: str) -> None:
    with pytest.raises(PrelaunchError):
        validate_source_relative_path(path)


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:9000",
        "http://0.0.0.0:9000",
        "https://127.0.0.1:9000",
        "http://127.0.0.1:9001",
        "http://127.0.0.1:9000/extra",
        "http://user@127.0.0.1:9000",
        "http://127.0.0.1:9000?probe=true",
        "http://192.0.2.1:9000",
    ],
)
def test_loopback_uri_validation_rejects_lane_drift(uri: str) -> None:
    with pytest.raises(PrelaunchError):
        require_loopback_uri(uri, schemes=("http",), expected_port=9000)


def test_loopback_uri_validation_accepts_numeric_loopback() -> None:
    assert require_loopback_uri(
        "http://127.0.0.1:9000", schemes=("http",), expected_port=9000
    )["host"] == "127.0.0.1"
    assert require_loopback_uri(
        "http://[::1]:9000", schemes=("http",), expected_port=9000
    )["host"] == "::1"


def test_lane_json_types_are_exact(tmp_path: Path) -> None:
    repo, registration, head = _ready_repo(tmp_path)
    lane = dict(EXPECTED_LANE)
    lane["model_calls"] = False
    with pytest.raises(PrelaunchError, match="effective lane differs"):
        create_prelaunch_receipt(
            tmp_path / "typed-receipt.json",
            registration,
            repo_root=repo,
            expected_head=head,
            run_id="local001",
            lane=lane,
        )


def test_create_only_receipt_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    repo, registration, head = _ready_repo(tmp_path)
    output = tmp_path / "receipts" / "prelaunch.json"
    output.parent.mkdir()
    receipt = create_prelaunch_receipt(
        output,
        registration,
        repo_root=repo,
        expected_head=head,
        run_id="local001",
        lane=EXPECTED_LANE,
    )
    assert output.exists()
    assert stat.S_IMODE(output.stat().st_mode) == 0o444
    assert receipt["source"]["git_head"] == head
    assert len(receipt["trials"]) == 9
    assert len({trial["trial_id"] for trial in receipt["trials"]}) == 9
    assert len(
        {
            trial[key]
            for trial in receipt["trials"]
            for key in ("treatment_session_id", "reconnect_session_id")
        }
    ) == 18
    other_run = build_prelaunch_payload(
        registration,
        repo_root=repo,
        expected_head=head,
        run_id="local002",
        lane=EXPECTED_LANE,
    )
    assert receipt["trial_plan_sha256"] != other_run["trial_plan_sha256"]
    assert receipt["trials"][0]["username"] != other_run["trials"][0]["username"]
    assert receipt["trials"][0]["trial_id"] != other_run["trials"][0]["trial_id"]
    assert verify_prelaunch_receipt(
        output,
        registration,
        repo_root=repo,
        expected_head=head,
    ) == []
    with pytest.raises(PrelaunchError, match="overwrite|already exists"):
        create_prelaunch_receipt(
            output,
            registration,
            repo_root=repo,
            expected_head=head,
            run_id="local001",
        )

    os.chmod(output, 0o644)
    tampered = json.loads(output.read_text())
    tampered["run_id"] = "llrd-v1-tampered"
    output.write_text(json.dumps(tampered) + "\n")
    assert "prelaunch payload self-hash mismatch" in verify_prelaunch_receipt(
        output,
        registration,
        repo_root=repo,
        expected_head=head,
    )


def test_recomputed_self_hash_cannot_hide_tampering(tmp_path: Path) -> None:
    repo, registration, head = _ready_repo(tmp_path)
    output = tmp_path / "recomputed-tamper.json"
    create_prelaunch_receipt(
        output,
        registration,
        repo_root=repo,
        expected_head=head,
        run_id="local001",
    )
    os.chmod(output, 0o644)
    record = json.loads(output.read_text())
    record["limitations"]["live_results"] = "present"
    unsigned = {key: value for key, value in record.items() if key != "payload_sha256"}
    record["payload_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    output.write_text(json.dumps(record) + "\n")
    errors = verify_prelaunch_receipt(
        output,
        registration,
        repo_root=repo,
        expected_head=head,
    )
    assert any("differs from recomputed source/design seal" in error for error in errors)


def test_wrong_head_and_malformed_contract_fail_cleanly(tmp_path: Path) -> None:
    repo, registration, head = _ready_repo(tmp_path)
    with pytest.raises(PrelaunchError, match="Git HEAD drift"):
        create_prelaunch_receipt(
            tmp_path / "wrong-head.json",
            registration,
            repo_root=repo,
            expected_head="0" * 40,
            run_id="local001",
        )
    value = json.loads(registration.read_text())
    value["live_contract"] = []
    registration.write_text(json.dumps(value, indent=2) + "\n")
    _git(repo, "add", str(registration.relative_to(repo)))
    _git(repo, "commit", "-qm", "malformed registration")
    malformed_head = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(PrelaunchError, match="registration contract invalid"):
        create_prelaunch_receipt(
            tmp_path / "malformed.json",
            registration,
            repo_root=repo,
            expected_head=malformed_head,
            run_id="local001",
        )


def test_concurrent_create_only_publication_has_one_winner(tmp_path: Path) -> None:
    repo, registration, head = _ready_repo(tmp_path)
    output = tmp_path / "race.json"

    def attempt() -> str:
        try:
            create_prelaunch_receipt(
                output,
                registration,
                repo_root=repo,
                expected_head=head,
                run_id="local001",
            )
            return "created"
        except PrelaunchError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: attempt(), range(2)))
    assert sorted(outcomes) == ["created", "refused"]
    assert verify_prelaunch_receipt(
        output,
        registration,
        repo_root=repo,
        expected_head=head,
    ) == []


def test_dirty_repo_and_in_repo_output_fail_closed(tmp_path: Path) -> None:
    repo, registration, head = _ready_repo(tmp_path)
    with pytest.raises(PrelaunchError, match="outside the source repository"):
        create_prelaunch_receipt(
            repo / "receipt.json",
            registration,
            repo_root=repo,
            expected_head=head,
            run_id="local001",
        )
    (repo / "untracked.txt").write_text("dirty\n")
    with pytest.raises(PrelaunchError, match="not completely clean"):
        create_prelaunch_receipt(
            tmp_path / "dirty-receipt.json",
            registration,
            repo_root=repo,
            expected_head=head,
            run_id="local001",
        )
