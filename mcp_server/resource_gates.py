"""Static resource → skill+level gate lookup loaded from Kaetram-Open data.

Used by `gather()` (and reusable by other tools) to translate a "no items
collected" outcome into a structured `{ gated, gate_skill, gate_level,
current_level }` answer instead of the agent guessing.

Loaded once at MCP module import. Path is overridable via the
`KAETRAM_DATA_DIR` env var; defaults to the canonical install location.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULT_DATA_DIR = Path.home() / "projects" / "Kaetram-Open" / "packages" / "server" / "data"

# Map data-file → in-game skill name (matches state_extractor.js skill keys).
_FILE_TO_SKILL = {
    "trees.json":    "Lumberjacking",
    "rocks.json":    "Mining",
    "foraging.json": "Foraging",
    "fishing.json":  "Fishing",
}


def _load_gates() -> dict:
    """Build {display_name_lower: {skill, level, item}} from all data files.

    Returns {} (and warns silently) if files aren't present — callers should
    treat absence as "we don't know the gate" rather than blowing up.
    """
    data_dir = Path(os.environ.get("KAETRAM_DATA_DIR", _DEFAULT_DATA_DIR))
    out: dict[str, dict] = {}
    for fname, skill in _FILE_TO_SKILL.items():
        path = data_dir / fname
        if not path.is_file():
            continue
        try:
            for key, entry in json.loads(path.read_text()).items():
                if not isinstance(entry, dict):
                    continue
                display = (entry.get("name") or key).lower()
                out[display] = {
                    "skill": skill,
                    "level": int(entry.get("levelRequirement", 1) or 1),
                    "item":  entry.get("item"),
                }
        except (OSError, ValueError):
            continue
    return out


_GATES = _load_gates()


def gate_for_resource(name: str) -> dict | None:
    """Look up gate by resource display name (case-insensitive substring).

    Tries exact match first, then prefix, then substring — since the agent
    might call `gather('Paprika')` for a resource actually named 'Paprika Bush'.
    """
    if not name:
        return None
    q = name.lower().strip()
    if q in _GATES:
        return _GATES[q]
    # Prefix match.
    for k, v in _GATES.items():
        if k.startswith(q) or q.startswith(k):
            return v
    # Substring match (last resort).
    for k, v in _GATES.items():
        if q in k or k in q:
            return v
    return None
