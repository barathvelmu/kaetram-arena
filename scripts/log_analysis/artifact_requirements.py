"""Fail-closed checks for log-derived research claims.

Research scripts must never turn an absent run bundle into a zero-valued
observation.  These helpers validate the expected agent/run/session layout
before an analysis starts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


class MissingEvidenceError(RuntimeError):
    """Raised when a claimed analysis input is absent or incomplete."""


def missing_agent_run_logs(
    raw_root: str | Path,
    *,
    agents: Iterable[str],
    run_ids: Iterable[str],
) -> list[str]:
    """Return missing run directories or session-log globs in stable order."""
    root = Path(raw_root)
    missing: list[str] = []
    for agent in sorted(set(agents)):
        for run_id in sorted(set(run_ids)):
            run_dir = root / agent / "runs" / run_id
            if not run_dir.is_dir():
                missing.append(str(run_dir))
            elif not any(
                path.is_file() and path.stat().st_size > 0
                for path in run_dir.glob("session_*.log")
            ):
                missing.append(str(run_dir / "session_*.log"))
    return missing


def require_agent_run_logs(
    raw_root: str | Path,
    *,
    agents: Iterable[str],
    run_ids: Iterable[str],
    analysis: str,
) -> None:
    """Require every declared agent/run bundle before computing statistics."""
    missing = missing_agent_run_logs(raw_root, agents=agents, run_ids=run_ids)
    if not missing:
        return
    preview = "\n".join(f"  - {path}" for path in missing[:20])
    remainder = len(missing) - min(len(missing), 20)
    suffix = f"\n  - ... and {remainder} more" if remainder else ""
    raise MissingEvidenceError(
        f"{analysis} blocked: {len(missing)} required raw-log bundle(s) are missing.\n"
        f"Restore the immutable artifacts before reporting statistics:\n"
        f"{preview}{suffix}"
    )


def require_files(paths: Iterable[str | Path], *, analysis: str) -> None:
    """Require non-empty supporting artifacts such as a training dataset."""
    missing = [str(Path(path)) for path in paths if not Path(path).is_file() or Path(path).stat().st_size == 0]
    if missing:
        rendered = "\n".join(f"  - {path}" for path in sorted(missing))
        raise MissingEvidenceError(
            f"{analysis} blocked: required supporting artifact(s) are missing or empty:\n"
            f"{rendered}"
        )
