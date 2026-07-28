#!/usr/bin/env python3
"""Fail-closed local launcher primitives for the live routing diagnostic.

The checked-in registration remains design-only, so this module cannot launch
the study yet.  It provides the audited game/source preflight, sanitized worker
environment, and create-only Mongo ownership layer required by the future
result-bearing session orchestrator.  No service is contacted on import.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from canonical_start import CANONICAL_DB_QUESTS  # noqa: E402
from scripts.opd.live_routing_diagnostic import (  # noqa: E402
    load_registration_strict,
    validate_registration,
)
from scripts.opd.live_routing_prelaunch import (  # noqa: E402
    EXPECTED_LANE,
    READY_STATUS,
    PrelaunchError,
    validate_lane,
)


GAME_BUNDLE_RELATIVE_PATH = Path("packages/server/dist/main.js")
MONGO_COLLECTIONS = (
    "player_info",
    "player_inventory",
    "player_bank",
    "player_equipment",
    "player_quests",
    "player_achievements",
    "player_skills",
    "player_statistics",
    "player_abilities",
)
LOCK_COLLECTION = "live_routing_diagnostic_locks"
USERNAME_RE = re.compile(r"[a-z0-9_]{1,16}")
SESSION_RE = re.compile(r"[a-z0-9-]{8,80}")
FORBIDDEN_ENV_RE = re.compile(
    r"(API[_-]?KEY|TOKEN|SECRET|ENDPOINT|MODAL|OPENAI|ANTHROPIC|WANDB|COMET)",
    re.IGNORECASE,
)


class LauncherError(RuntimeError):
    pass


class PartialSeedError(LauncherError):
    def __init__(self, message: str, receipt: dict[str, Any]):
        super().__init__(message)
        self.receipt = receipt


@dataclass(frozen=True)
class LaneConfig:
    client_url: str = "http://127.0.0.1:9000"
    game_ws_url: str = "ws://127.0.0.1:9191"
    mongo_uri: str = "mongodb://127.0.0.1:27017/kaetram_e2e"
    mongo_database: str = "kaetram_e2e"

    def validate(self) -> None:
        validate_lane(
            {
                **EXPECTED_LANE,
                "client_url": self.client_url,
                "game_ws_url": self.game_ws_url,
                "mongo_uri": self.mongo_uri,
                "mongo_database": self.mongo_database,
            }
        )


@dataclass(frozen=True)
class SessionSpec:
    trial_id: str
    session_id: str
    phase: str
    username: str
    arm: str

    def validate(self) -> None:
        if not USERNAME_RE.fullmatch(self.username):
            raise LauncherError("username violates the registered Kaetram limit")
        if not SESSION_RE.fullmatch(self.session_id):
            raise LauncherError("session identifier is malformed")
        if self.phase not in ("treatment", "reconnect"):
            raise LauncherError("session phase must be treatment or reconnect")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repo: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LauncherError(f"game Git inspection failed: {' '.join(arguments)}") from exc


def attest_game_checkout(game_root: Path, registration: dict[str, Any]) -> dict[str, Any]:
    """Bind a clean game checkout and built server entrypoint to registration."""

    game_root = game_root.resolve()
    top = Path(_git(game_root, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != game_root:
        raise LauncherError("game_root is not the exact Git toplevel")
    head = _git(game_root, "rev-parse", "--verify", "HEAD^{commit}").strip()
    expected = registration.get("live_contract", {})
    if head != expected.get("game_revision"):
        raise LauncherError(f"game revision drift: expected={expected.get('game_revision')}, actual={head}")
    if _git(game_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise LauncherError("game checkout is not completely clean")
    bundle = game_root / GAME_BUNDLE_RELATIVE_PATH
    if bundle.is_symlink() or not bundle.is_file():
        raise LauncherError("registered game bundle is missing or symlinked")
    bundle_sha = _sha256_file(bundle)
    if bundle_sha != expected.get("game_bundle_sha256"):
        raise LauncherError("built game bundle digest drift")
    return {
        "game_root": str(game_root),
        "git_head": head,
        "worktree_clean": True,
        "bundle_path": GAME_BUNDLE_RELATIVE_PATH.as_posix(),
        "bundle_size_bytes": bundle.stat().st_size,
        "bundle_sha256": bundle_sha,
    }


def sanitized_worker_environment(
    source: Mapping[str, str],
    spec: SessionSpec,
    *,
    lane: LaneConfig | None = None,
    state_dir: Path,
) -> dict[str, str]:
    """Build a minimal environment with no model, remote, or metered credentials."""

    spec.validate()
    lane = lane or LaneConfig()
    lane.validate()
    allowed_inherited = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "DISPLAY")
    environment = {
        key: source[key]
        for key in allowed_inherited
        if key in source and not FORBIDDEN_ENV_RE.search(key)
    }
    environment.update(
        {
            "KAETRAM_USERNAME": spec.username,
            "KAETRAM_PASSWORD": "test",
            "KAETRAM_CLIENT_URL": lane.client_url,
            "KAETRAM_PORT": "9191",
            "GAME_WS_HOST": "127.0.0.1",
            "GAME_WS_PORT": "9191",
            "KAETRAM_MONGO_URI": lane.mongo_uri,
            "KAETRAM_MONGO_DB": lane.mongo_database,
            "KAETRAM_TEST_LANE": "1",
            "KAETRAM_DIAGNOSTIC_LANE": "1",
            "KAETRAM_DIAGNOSTIC_SESSION_ID": spec.session_id,
            "KAETRAM_REQUIRE_EXISTING_ACCOUNT": "1",
            "KAETRAM_DISABLE_HEARTBEATS": "1",
            "KAETRAM_DIAGNOSTIC_LOOPBACK_ONLY": "1",
            "KAETRAM_LIVE_SUITE": "0",
            "KAETRAM_HEADED": "0",
            "KAETRAM_STATE_DIR": str(state_dir.resolve()),
            "PYTHONNOUSERSITE": "1",
        }
    )
    forbidden = [key for key in environment if FORBIDDEN_ENV_RE.search(key)]
    if forbidden:
        raise LauncherError(f"worker environment retained forbidden keys: {forbidden}")
    return environment


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if value.__class__.__name__ == "ObjectId":
        return str(value)
    return value


def canonical_documents(username: str) -> dict[str, dict[str, Any]]:
    """Construct the exact nine Mongo documents without writing them."""

    if not USERNAME_RE.fullmatch(username):
        raise LauncherError("invalid canonical username")
    from tests.e2e.helpers.seed import (  # local, pure constructors only
        FIXED_BCRYPT_HASH,
        STARTER_KIT,
        TUTORIAL_FINISHED_QUEST,
    )

    inventory = [
        {
            "index": index,
            "key": "",
            "count": 0,
            "enchantments": {},
        }
        for index in range(25)
    ]
    for item in STARTER_KIT:
        inventory[item["index"]] = {**item, "enchantments": {}}
    quests = [dict(TUTORIAL_FINISHED_QUEST), *map(dict, CANONICAL_DB_QUESTS)]
    return {
        "player_inventory": {"username": username, "slots": inventory},
        "player_bank": {"username": username, "slots": [
            {"index": index, "key": "", "count": 0, "enchantments": {}}
            for index in range(25)
        ]},
        "player_equipment": {"username": username, "equipments": []},
        "player_quests": {"username": username, "quests": quests},
        "player_achievements": {"username": username, "achievements": []},
        "player_skills": {"username": username, "skills": []},
        "player_statistics": {"username": username},
        "player_abilities": {"username": username, "abilities": []},
        "player_info": {
            "username": username,
            "password": FIXED_BCRYPT_HASH,
            "email": f"{username}@kaetrambench.test",
            "x": 328,
            "y": 892,
            "userAgent": "kaetram-live-routing-diagnostic",
            "rank": 0,
            "poison": {"type": -1, "remaining": -1},
            "effects": {},
            "hitPoints": 69,
            "mana": 20,
            "orientation": 1,
            "ban": 0,
            "jail": 0,
            "mute": 0,
            "lastWarp": 0,
            "mapVersion": -1,
            "regionsLoaded": [],
            "friends": [],
            "lastServerId": 1,
            "lastAddress": "127.0.0.1",
            "lastGlobalChat": 0,
            "guild": "",
            "pet": "",
        },
    }


class CreateOnlyCanonicalStore:
    """Mongo writer that owns only rows inserted by this exact trial."""

    def __init__(
        self,
        *,
        lane: LaneConfig | None = None,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.lane = lane or LaneConfig()
        self.lane.validate()
        if client_factory is None:
            from pymongo import MongoClient

            client_factory = lambda uri: MongoClient(uri, serverSelectionTimeoutMS=3000)
        self.client = client_factory(self.lane.mongo_uri)
        self.db = self.client["kaetram_e2e"]

    def close(self) -> None:
        self.client.close()

    def attest_topology(self) -> dict[str, Any]:
        """Require the discovered Mongo topology to remain numeric loopback."""

        from ipaddress import ip_address

        self.client.admin.command("ping")
        nodes = sorted(getattr(self.client, "nodes", set()))
        if not nodes:
            raise LauncherError("Mongo topology did not expose any connected node")
        normalized = []
        for host, port in nodes:
            try:
                address = ip_address(host)
            except ValueError as exc:
                raise LauncherError("Mongo topology host is not numeric") from exc
            if not address.is_loopback or port != 27017:
                raise LauncherError("Mongo topology escaped registered loopback lane")
            normalized.append({"host": str(address), "port": port})
        return {
            "uri": self.lane.mongo_uri,
            "database": "kaetram_e2e",
            "nodes": normalized,
            "loopback_only": True,
        }

    def prove_absent(self, usernames: Sequence[str]) -> dict[str, Any]:
        rows = {}
        for username in usernames:
            if not USERNAME_RE.fullmatch(username):
                raise LauncherError("absence check received invalid username")
            counts = {
                collection: int(
                    self.db[collection].count_documents({"username": username}, limit=1)
                )
                for collection in MONGO_COLLECTIONS
            }
            rows[username] = counts
        return {
            "database": "kaetram_e2e",
            "counts": rows,
            "all_absent": all(
                count == 0
                for counts in rows.values()
                for count in counts.values()
            ),
        }

    def insert_canonical(self, username: str, trial_id: str) -> dict[str, Any]:
        absence = self.prove_absent([username])
        if absence["all_absent"] is not True:
            raise LauncherError("create-only seed refused: username already exists")
        inserted: dict[str, str] = {}
        receipt = {
            "database": "kaetram_e2e",
            "username": username,
            "trial_id": trial_id,
            "absence": absence,
            "inserted_ids": inserted,
            "player_info_inserted_last": False,
        }
        try:
            lock = self.db[LOCK_COLLECTION].insert_one(
                {"_id": username, "trial_id": trial_id}
            )
            inserted[LOCK_COLLECTION] = str(lock.inserted_id)
            documents = canonical_documents(username)
            order = [name for name in MONGO_COLLECTIONS if name != "player_info"]
            order.append("player_info")
            for collection in order:
                result = self.db[collection].insert_one(documents[collection])
                inserted[collection] = str(result.inserted_id)
            receipt["player_info_inserted_last"] = True
        except Exception as exc:
            raise PartialSeedError(f"create-only seed stopped after partial write: {exc}", receipt) from exc
        return receipt

    def snapshot_owned(self, username: str, inserted_ids: Mapping[str, str]) -> dict[str, Any]:
        documents = {}
        for collection in MONGO_COLLECTIONS:
            expected_id = inserted_ids.get(collection)
            document = self.db[collection].find_one({"username": username})
            if document is None or str(document.get("_id")) != expected_id:
                raise LauncherError(f"owned database identity drift: {collection}")
            documents[collection] = _json_safe(document)
        return {"database": "kaetram_e2e", "username": username, "documents": documents}

    def cleanup_owned(
        self,
        username: str,
        trial_id: str,
        inserted_ids: Mapping[str, str],
    ) -> dict[str, Any]:
        try:
            from bson import ObjectId
        except ImportError as exc:
            raise LauncherError("bson is required for ownership-checked cleanup") from exc
        deleted = {}
        for collection in MONGO_COLLECTIONS:
            identifier = inserted_ids.get(collection)
            if not isinstance(identifier, str):
                raise LauncherError(f"missing owned identifier: {collection}")
            result = self.db[collection].delete_one(
                {"_id": ObjectId(identifier), "username": username}
            )
            deleted[collection] = int(result.deleted_count)
        lock_result = self.db[LOCK_COLLECTION].delete_one(
            {"_id": username, "trial_id": trial_id}
        )
        absence = self.prove_absent([username])
        return {
            "database": "kaetram_e2e",
            "deleted": deleted,
            "lock_deleted": int(lock_result.deleted_count),
            "absence": absence,
            "complete": all(count == 1 for count in deleted.values())
            and absence["all_absent"],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--game-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        registration = load_registration_strict(args.registration)
        errors = validate_registration(registration)
        if errors:
            raise LauncherError("design registration invalid: " + "; ".join(errors))
        game = attest_game_checkout(args.game_root, registration)
        if registration.get("status") != READY_STATUS:
            raise LauncherError(
                "live execution refused: registration remains design-only; "
                "the result-bearing 18-session orchestrator is not yet sealed"
            )
    except (LauncherError, PrelaunchError, OSError, ValueError) as exc:
        print(f"live routing launcher refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"game": game, "status": "preflight_only"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
