"""HAI-23 (FR-031) — POST /api/v1/proposals (create)."""

import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import proposals as proposals_route
from src.auth.service import get_principal
from src.core.events import EventEmitter
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStateStore(db_path=str(Path(tmp) / "p.db"))
        await s.initialize()
        try:
            yield s
        finally:
            await s.close()


def _make_app(store, principal: dict | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(proposals_route.router)
    app.state.state_store = store
    events = EventEmitter()
    app.state.events = events
    app.state.captured: list = []

    async def _cap(event_type: str, data: dict) -> None:
        app.state.captured.append((event_type, data))

    events.on(_cap)

    def _principal() -> dict[str, Any]:
        return principal or {
            "sub": "stok-1", "token_id": "stok-1", "username": "hermes-operator",
            "role": "developer", "is_service_token": True,
        }

    app.dependency_overrides[get_principal] = _principal
    return app


async def test_create_pending_proposal_persists_and_emits(store):
    client = TestClient(app := _make_app(store))
    r = client.post(
        "/api/v1/proposals",
        json={"action_type": "project.create", "payload": {"name": "Atlas"}, "target_ref": "proj-1"},
    )
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["status"] == "pending"
    assert data["action_type"] == "project.create"
    assert data["proposed_by"] == "service:hermes-operator"   # principal_actor of the service token
    assert data["payload"] == {"name": "Atlas"}
    pid = data["proposal_id"]

    assert await store.get_proposal(pid) is not None            # persisted
    assert any(et == "proposal.created" and d["proposal_id"] == pid for et, d in app.state.captured)


async def test_missing_action_type_400(store):
    client = TestClient(_make_app(store))
    assert client.post("/api/v1/proposals", json={"action_type": "   "}).status_code == 400


async def test_idempotent_replay_returns_same_proposal(store):
    client = TestClient(_make_app(store))
    r1 = client.post("/api/v1/proposals", json={"action_type": "deploy", "idempotency_key": "idem-1"})
    r2 = client.post("/api/v1/proposals", json={"action_type": "deploy", "idempotency_key": "idem-1"})
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["data"]["proposal_id"] == r2.json()["data"]["proposal_id"]
    assert r2.json()["meta"]["idempotent_replay"] is True
    assert len(await store.list_proposals()) == 1               # no duplicate row


async def test_human_proposer_attribution(store):
    client = TestClient(_make_app(store, principal={"sub": "u1", "username": "alice", "role": "admin"}))
    r = client.post("/api/v1/proposals", json={"action_type": "deploy"})
    assert r.json()["data"]["proposed_by"] == "alice"
