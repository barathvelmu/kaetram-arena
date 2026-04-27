"""Static mob → {level, max_hp, aggressive} lookup loaded from Kaetram-Open data.

Used by `observe()` to enrich each nearby mob entry with `level` and an
`aggressive` flag — so the agent can immediately compare its own level to
the mob's, instead of having to recall the MOB PROGRESSION table by name
from prompt context.

Same loading pattern as `resource_gates.py`. Path is overridable via the
`KAETRAM_DATA_DIR` env var; defaults to the canonical install location.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULT_DATA_DIR = Path.home() / "projects" / "Kaetram-Open" / "packages" / "server" / "data"


def _load_mobs() -> dict:
    """Build {display_name_lower: {level, max_hp, aggressive}}.

    Returns {} if the data file isn't present — callers treat absence as
    "we don't know" rather than blowing up.
    """
    data_dir = Path(os.environ.get("KAETRAM_DATA_DIR", _DEFAULT_DATA_DIR))
    path = data_dir / "mobs.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        display = (entry.get("name") or key).lower()
        out[display] = {
            "level":      int(entry.get("level", 1) or 1),
            "max_hp":     int(entry.get("hitPoints", 0) or 0),
            "aggressive": bool(entry.get("aggressive", False)),
        }
    return out


_MOBS = _load_mobs()


def mob_info(name: str) -> dict | None:
    """Look up mob stats by display name (case-insensitive). Returns None
    when the mob isn't in the data file (e.g. custom/event mobs)."""
    if not name:
        return None
    return _MOBS.get(name.lower())
