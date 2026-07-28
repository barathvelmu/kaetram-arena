from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.opd.live_routing_launcher import (
    LOCK_COLLECTION,
    MONGO_COLLECTIONS,
    CreateOnlyCanonicalStore,
    LaneConfig,
    LauncherError,
    SessionSpec,
    attest_game_checkout,
    canonical_documents,
    sanitized_worker_environment,
)


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_game_checkout_attestation_binds_clean_commit_and_bundle(tmp_path: Path) -> None:
    root = tmp_path / "game"
    bundle = root / "packages/server/dist/main.js"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"reviewed game bundle\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Game Test")
    _git(root, "config", "user.email", "game@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "game")
    head = _git(root, "rev-parse", "HEAD")
    import hashlib

    registration = {
        "live_contract": {
            "game_revision": head,
            "game_bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        }
    }
    receipt = attest_game_checkout(root, registration)
    assert receipt["git_head"] == head
    assert receipt["worktree_clean"] is True
    bundle.write_bytes(b"drift\n")
    with pytest.raises(LauncherError, match="not completely clean"):
        attest_game_checkout(root, registration)


def test_worker_environment_is_local_minimal_and_credential_free(tmp_path: Path) -> None:
    spec = SessionSpec(
        trial_id="trial-0001",
        session_id="llrd-local001-t01-treatment",
        phase="treatment",
        username="lr_local001_01",
        arm="structured_direct",
    )
    environment = sanitized_worker_environment(
        {
            "HOME": "/tmp/home",
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "must-not-survive",
            "KAETRAM_QWEN_ENDPOINT": "https://paid.invalid",
        },
        spec,
        lane=LaneConfig(),
        state_dir=tmp_path / "state",
    )
    assert environment["KAETRAM_CLIENT_URL"] == "http://127.0.0.1:9000"
    assert environment["KAETRAM_PORT"] == "9191"
    assert environment["KAETRAM_MONGO_DB"] == "kaetram_e2e"
    assert environment["KAETRAM_REQUIRE_EXISTING_ACCOUNT"] == "1"
    assert environment["KAETRAM_DISABLE_HEARTBEATS"] == "1"
    assert "OPENAI_API_KEY" not in environment
    assert "KAETRAM_QWEN_ENDPOINT" not in environment


def test_canonical_documents_cover_all_player_collections() -> None:
    documents = canonical_documents("lr_local001_01")
    assert set(documents) == set(MONGO_COLLECTIONS)
    assert documents["player_info"]["x"] == 328
    assert documents["player_info"]["y"] == 892
    assert documents["player_info"]["hitPoints"] == 69
    assert [slot["key"] for slot in documents["player_inventory"]["slots"][:5]] == [
        "bronzeaxe",
        "knife",
        "fishingpole",
        "coppersword",
        "woodenbow",
    ]


class _InsertResult:
    def __init__(self, identifier: str):
        self.inserted_id = identifier


class _Collection:
    def __init__(self, name: str, order: list[str]):
        self.name = name
        self.order = order
        self.documents: list[dict] = []

    def count_documents(self, query: dict, limit: int = 0) -> int:
        return sum(
            all(document.get(key) == value for key, value in query.items())
            for document in self.documents
        )

    def insert_one(self, document: dict) -> _InsertResult:
        if self.name == LOCK_COLLECTION and any(
            row.get("_id") == document.get("_id") for row in self.documents
        ):
            raise RuntimeError("duplicate lock")
        stored = dict(document)
        identifier = stored.setdefault("_id", f"id-{self.name}-{len(self.documents)}")
        self.documents.append(stored)
        self.order.append(self.name)
        return _InsertResult(identifier)

    def find_one(self, query: dict) -> dict | None:
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return dict(document)
        return None


class _Database:
    def __init__(self):
        self.order: list[str] = []
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection(name, self.order))


class _Client:
    def __init__(self):
        self.database = _Database()
        self.nodes = {("127.0.0.1", 27017)}
        self.admin = self

    def __getitem__(self, name: str) -> _Database:
        assert name == "kaetram_e2e"
        return self.database

    def close(self) -> None:
        pass

    def command(self, name: str) -> dict:
        assert name == "ping"
        return {"ok": 1}


def test_create_only_store_refuses_reuse_and_inserts_player_info_last() -> None:
    client = _Client()
    store = CreateOnlyCanonicalStore(client_factory=lambda _: client)
    assert store.attest_topology()["loopback_only"] is True
    receipt = store.insert_canonical("lr_local001_01", "trial-0001")
    assert receipt["absence"]["all_absent"] is True
    assert receipt["player_info_inserted_last"] is True
    assert client.database.order[-1] == "player_info"
    assert set(receipt["inserted_ids"]) == {LOCK_COLLECTION, *MONGO_COLLECTIONS}
    with pytest.raises(LauncherError, match="already exists"):
        store.insert_canonical("lr_local001_01", "trial-0001-retry")
