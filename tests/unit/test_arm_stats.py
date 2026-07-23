from pathlib import Path

from scripts import arm_stats


def test_collect_arm_treats_missing_agent_root_as_missing_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    assert arm_stats.collect_arm("r10-base-9B", tmp_path) == []
    assert "required directory" in capsys.readouterr().err


def test_verify_fails_closed_before_parsing_when_artifacts_are_missing(
    tmp_path: Path,
    capsys,
) -> None:
    assert arm_stats.main(["--verify", "--raw-root", str(tmp_path)]) == 2
    stderr = capsys.readouterr().err
    assert "ERROR:" in stderr
    assert "arm_stats r10 verification" in stderr
