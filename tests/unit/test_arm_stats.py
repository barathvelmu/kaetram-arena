from pathlib import Path

from scripts import arm_stats


def test_collect_arm_treats_missing_agent_root_as_missing_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    assert arm_stats.collect_arm("r10-base-9B", tmp_path) == []
    assert "arm quarantined" in capsys.readouterr().err


def test_collect_arm_quarantines_any_partial_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    def incomplete(*args, **kwargs):
        raise arm_stats.MissingEvidenceError("one declared lane is missing")

    monkeypatch.setattr(arm_stats, "require_agent_run_logs", incomplete)
    assert arm_stats.collect_arm("opd-r2", tmp_path) == []
    stderr = capsys.readouterr().err
    assert "opd-r2" in stderr
    assert "one declared lane is missing" in stderr


def test_verify_fails_closed_before_parsing_when_artifacts_are_missing(
    tmp_path: Path,
    capsys,
) -> None:
    assert arm_stats.main(["--verify", "--raw-root", str(tmp_path)]) == 2
    stderr = capsys.readouterr().err
    assert "ERROR:" in stderr
    assert "arm_stats r10 verification" in stderr
