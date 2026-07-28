import json
from pathlib import Path

import pytest

from scripts.opd import build_trigger_appropriateness_packets as builder
from scripts.opd import verify_trigger_appropriateness_packets as verifier
from scripts.opd import verify_trigger_appropriateness_labels as label_verifier


@pytest.fixture(scope="module")
def payloads() -> dict[str, bytes]:
    return builder.build_payloads()


def _json(payload: bytes) -> dict:
    return json.loads(payload)


def _jsonl(payload: bytes) -> list[dict]:
    return [json.loads(line) for line in payload.splitlines()]


def test_packet_build_is_complete_blinded_and_unlabeled(payloads) -> None:
    assert set(payloads) == {*builder.OUTPUT_FILENAMES, "manifest.json"}
    packets = _jsonl(payloads["judge-packets.jsonl"])
    assert len(packets) == 123
    assert len({row["item_id"] for row in packets}) == 123
    assert [row["item_id"] for row in packets] == sorted(
        row["item_id"] for row in packets
    )
    for packet in packets:
        assert set(packet) == {"schema_version", "item_id", "context", "candidate"}
        assert all(message["role"] != "assistant" for message in packet["context"])
        visible = json.dumps(packet, sort_keys=True).lower()
        for hidden in (
            '"snapshot"',
            '"condition_id"',
            '"route"',
            '"reasoning"',
            '"state_id"',
            '"sample_index"',
            '"seed"',
            "qwen",
            "modal",
            "/users/",
            "session #",
        ):
            assert hidden not in visible
    manifest = _json(payloads["manifest.json"])
    assert manifest["human_label_gate_satisfied"] is False
    assert manifest["contains_labels"] is False
    assert manifest["contains_results"] is False
    assert manifest["identity_scan"] == {
        "matches": 0,
        "passed": True,
        "scanner_version": "kaetram-trigger-judge-visible-scan-v1",
        "visible_files": [
            "judge-protocol.json",
            "judge-packets.jsonl",
            "reviewer-a.template.jsonl",
            "reviewer-b.template.jsonl",
            "adjudication.template.jsonl",
            "label-seals.template.json",
            "labeling-workflow.md",
        ],
    }


def test_sealed_key_contains_only_hidden_selection_and_source_accounting(payloads) -> None:
    key = _json(payloads["sealed-analysis-key.json"])
    assert key["unique_item_count"] == 123
    assert key["valid_occurrence_count"] == 590
    assert key["primary_item_count"] == 25
    assert sum(item["primary_subset"] for item in key["items"]) == 25
    assert sum(item["occurrence_count"] for item in key["items"]) == 590
    assert all(item["occurrence_count"] == len(item["occurrences"]) for item in key["items"])
    assert all(
        item["primary_subset"] == (item["distinct_snapshot_count"] == 3)
        for item in key["items"]
    )
    visible = payloads["judge-packets.jsonl"]
    for item in key["items"]:
        assert item["state_id"].encode() not in visible


def test_protocol_requires_two_independent_humans_and_adjudication(payloads) -> None:
    protocol = _json(payloads["judge-protocol.json"])
    assert protocol["status"] == "unlabeled_design_only"
    assert protocol["human_gate"] == {
        "any_missing_or_invalid_label_blocks_all_aggregation": True,
        "distinct_third_human_adjudicator": True,
        "human_adjudication_required_for_every_disagreement": True,
        "independent_reviewers": 2,
        "independent_until_both_hash_sealed": True,
    }
    assert "not a scientific result" in protocol["claim_boundary"]


def test_blank_templates_cover_every_item_without_results(payloads) -> None:
    item_ids = [row["item_id"] for row in _jsonl(payloads["judge-packets.jsonl"])]
    for role, name in (
        ("reviewer_a", "reviewer-a.template.jsonl"),
        ("reviewer_b", "reviewer-b.template.jsonl"),
    ):
        rows = _jsonl(payloads[name])
        assert [row["item_id"] for row in rows] == item_ids
        assert all(row["reviewer_role"] == role for row in rows)
        assert all(row["precondition_support"] is None for row in rows)
        assert all(row["strategic_appropriateness"] is None for row in rows)
        assert all(row["concise_rationale"] is None for row in rows)
    adjudication = _jsonl(payloads["adjudication.template.jsonl"])
    assert [row["item_id"] for row in adjudication] == item_ids
    assert all(row["adjudication_required"] is None for row in adjudication)
    seals = _json(payloads["label-seals.template.json"])
    assert seals["reviewer_a"]["file_sha256"] is None
    assert seals["reviewer_b"]["file_sha256"] is None
    assert seals["adjudication_file_sha256"] is None


def test_bundle_round_trip_and_tamper_rejection(tmp_path: Path, payloads) -> None:
    output = tmp_path / "packets"
    builder.write_bundle(output, payloads)
    result = verifier.verify_bundle(output)
    assert result["unique_item_count"] == 123
    assert result["primary_item_count"] == 25
    assert result["identity_scan_passed"] is True
    assert result["human_label_gate_satisfied"] is False
    assert result["contains_results"] is False

    packets = output / "judge-packets.jsonl"
    packets.write_bytes(packets.read_bytes().replace(b'"item-', b'"item-tampered-', 1))
    with pytest.raises(verifier.PacketVerificationError):
        verifier.verify_bundle(output)


def test_builder_refuses_overwrite_and_untrusted_source(tmp_path: Path, payloads) -> None:
    output = tmp_path / "packets"
    builder.write_bundle(output, payloads)
    with pytest.raises(builder.PacketBuildError, match="refusing to overwrite"):
        builder.write_bundle(output, payloads)

    bad_trust = tmp_path / "trust.json"
    trust = builder.load_json(builder.DEFAULT_TRUST_ROOT)
    trust["artifact_index_sha256"] = "0" * 64
    bad_trust.write_text(json.dumps(trust))
    with pytest.raises(builder.PacketBuildError, match="independent audit"):
        builder.build_payloads(trust_root_path=bad_trust)


def test_visible_scan_rejects_identity_hidden_metadata_and_reasoning() -> None:
    for payload in (
        b'{"identity":"/Users/example"}\n',
        b'{"snapshot":"hidden"}\n',
        b'{"reasoning":"hidden"}\n',
        b'{"content":"<think>hidden</think>"}\n',
        b'{"content":"QwenCompletionist"}\n',
        b'{"content":"Session #161"}\n',
    ):
        with pytest.raises(builder.PacketBuildError, match="leaked"):
            builder._scan_visible("fixture.jsonl", payload)


def test_completion_gate_blocks_missing_and_merges_only_after_adjudication(
    tmp_path: Path, payloads
) -> None:
    bundle = tmp_path / "bundle"
    builder.write_bundle(bundle, payloads)
    reviewer_a = _jsonl(payloads["reviewer-a.template.jsonl"])
    reviewer_b = _jsonl(payloads["reviewer-b.template.jsonl"])
    for row in reviewer_a:
        row["precondition_support"] = "supported"
        row["strategic_appropriateness"] = "appropriate"
        row["concise_rationale"] = "The visible context supports this call."
    for row in reviewer_b:
        row["precondition_support"] = "supported"
        row["strategic_appropriateness"] = "appropriate"
        row["concise_rationale"] = "The call is grounded in the visible state."
    # Force one real disagreement so the adjudication path is exercised.
    reviewer_b[0]["strategic_appropriateness"] = "plausible"
    a_path = tmp_path / "reviewer-a.jsonl"
    b_path = tmp_path / "reviewer-b.jsonl"
    a_path.write_bytes(builder.jsonl_bytes(reviewer_a))
    b_path.write_bytes(builder.jsonl_bytes(reviewer_b))

    adjudication = _jsonl(payloads["adjudication.template.jsonl"])
    for index, row in enumerate(adjudication):
        row["adjudication_required"] = index == 0
        if index == 0:
            row["resolved_precondition_support"] = "supported"
            row["resolved_strategic_appropriateness"] = "plausible"
            row["concise_rationale"] = "The evidence supports execution, but strategy is ambiguous."
    adjudication_path = tmp_path / "adjudication.jsonl"
    adjudication_path.write_bytes(builder.jsonl_bytes(adjudication))
    seals = _json(payloads["label-seals.template.json"])
    seals["reviewer_a"] = {
        "human_reviewer_id": "human-a",
        "file_sha256": builder.sha256_file(a_path),
        "independent": True,
        "completed_without_access_to_other_labels": True,
        "completed_without_access_to_sealed_key": True,
    }
    seals["reviewer_b"] = {
        "human_reviewer_id": "human-b",
        "file_sha256": builder.sha256_file(b_path),
        "independent": True,
        "completed_without_access_to_other_labels": True,
        "completed_without_access_to_sealed_key": True,
    }
    seals["both_reviewer_files_sealed_before_adjudication"] = True
    seals["adjudicator_human_id"] = "human-c"
    seals["adjudication_file_sha256"] = builder.sha256_file(adjudication_path)
    seals_path = tmp_path / "seals.json"
    seals_path.write_bytes(builder.pretty_bytes(seals))

    incomplete = [dict(row) for row in reviewer_b]
    incomplete[-1]["concise_rationale"] = None
    b_path.write_bytes(builder.jsonl_bytes(incomplete))
    with pytest.raises(label_verifier.LabelVerificationError, match="invalid label"):
        label_verifier.verify_and_merge(
            bundle_dir=bundle,
            reviewer_a_path=a_path,
            reviewer_b_path=b_path,
            adjudication_path=adjudication_path,
            seals_path=seals_path,
        )

    b_path.write_bytes(builder.jsonl_bytes(reviewer_b))
    merged, receipt = label_verifier.verify_and_merge(
        bundle_dir=bundle,
        reviewer_a_path=a_path,
        reviewer_b_path=b_path,
        adjudication_path=adjudication_path,
        seals_path=seals_path,
    )
    assert len(merged) == 123
    assert merged[0]["resolution"] == "human_adjudication"
    assert all(row["resolution"] == "reviewer_agreement" for row in merged[1:])
    assert receipt["status"] == "human_label_gate_complete_no_aggregate_computed"
    assert receipt["disagreement_count"] == 1
    assert receipt["aggregate_computed"] is False
