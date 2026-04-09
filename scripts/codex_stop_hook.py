#!/usr/bin/env python3
"""Codex Stop Hook — forces continuation up to MAX_TURNS.

Codex exec is one-shot: the model runs, decides it's done, and exits.
This hook intercepts the Stop event and returns {"decision": "block"}
to force the agent to keep playing.

The hook tracks turns via a counter file. When the counter reaches
MAX_TURNS, it outputs nothing (lets Codex stop naturally).

Stdin:  JSON with session_id, stop_hook_active, last_assistant_message, etc.
Stdout: {"decision": "block", "reason": "..."} to continue, or nothing to stop.
"""

import json
import os
import sys
from pathlib import Path

MAX_TURNS = int(os.environ.get("CODEX_MAX_TURNS", "150"))
COUNTER_FILE = Path(os.environ.get("CODEX_TURN_COUNTER", ".turn_counter"))


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Can't parse input — let Codex stop
        return

    # Read current turn count
    try:
        count = int(COUNTER_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        count = 0

    count += 1
    COUNTER_FILE.write_text(str(count))

    # Reached limit — let Codex stop
    if count >= MAX_TURNS:
        return

    # Force continuation with a new prompt injected as user message
    json.dump({
        "decision": "block",
        "reason": (
            f"Turn {count}/{MAX_TURNS}. Continue playing. "
            "Call observe to check game state, then decide and act. "
            "Do NOT stop — keep playing."
        ),
    }, sys.stdout)


if __name__ == "__main__":
    main()
