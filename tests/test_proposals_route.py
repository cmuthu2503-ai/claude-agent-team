"""HAI-23 (FR-031) — POST /api/v1/proposals (create)."""

import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import proposals as proposals_route
from src.auth.service import get_current_user, get_principal
from src.core.events import EventEmitter
from src.core.proposal_registry import ProposalActionRegistry
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


def _make_app(store, principal: dict | None = None, registry: ProposalActionRegistry | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(proposals_route.router)
    app.state.state_store = store
    app.state.proposal_registry = registry or ProposalActionRegistry()
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

    # create → get_principal (JWT or service token); confirm → get_current_user (human).
    app.dependency_overrides[get_principal] = _principal
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u1", "username": "alice", "role": "admin"}
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


# ── HAI-26 — confirm + execute ───────────────────────────────────────────────

def _registry_with_handler(result_ref="REQ-NEW", raises=False):
    reg = ProposalActionRegistry()

    async def handler(proposal, ctx):
        if raises:
            raise RuntimeError("handler boom")
        return {"result_ref": result_ref}

    reg.register("deploy", handler)
    return reg


async def test_confirm_executes_and_emits(store):
    app = _make_app(store, registry=_registry_with_handler("DEPLOY-OK"))
    client = TestClient(app)
    pid = client.post("/api/v1/proposals", json={"action_type": "deploy"}).json()["data"]["proposal_id"]

    r = client.post(f"/api/v1/proposals/{pid}/confirm")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "executed"
    assert data["result_ref"] == "DEPLOY-OK"
    assert data["decided_by"] == "alice"
    assert data["executed_at"] is not None
    types = [et for et, _ in app.state.captured]
    assert "proposal.confirmed" in types and "proposal.executed" in types


async def test_confirm_failing_handler_marks_failed(store):
    app = _make_app(store, registry=_registry_with_handler(raises=True))
    client = TestClient(app)
    pid = client.post("/api/v1/proposals", json={"action_type": "deploy"}).json()["data"]["proposal_id"]

    r = client.post(f"/api/v1/proposals/{pid}/confirm")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "failed"
    assert "boom" in data["error"]
    assert "proposal.failed" in [et for et, _ in app.state.captured]


async def test_confirm_no_handler_fails(store):
    # registry has no handler for 'project.create' → failed (not executed)
    app = _make_app(store)
    client = TestClient(app)
    pid = client.post("/api/v1/proposals", json={"action_type": "project.create"}).json()["data"]["proposal_id"]
    data = client.post(f"/api/v1/proposals/{pid}/confirm").json()["data"]
    assert data["status"] == "failed"
    assert "No handler" in data["error"]


async def test_confirm_unknown_404(store):
    client = TestClient(_make_app(store))
    assert client.post("/api/v1/proposals/nope/confirm").status_code == 404


async def test_confirm_non_pending_409(store):
    app = _make_app(store, registry=_registry_with_handler())
    client = TestClient(app)
    pid = client.post("/api/v1/proposals", json={"action_type": "deploy"}).json()["data"]["proposal_id"]
    assert client.post(f"/api/v1/proposals/{pid}/confirm").status_code == 200   # first confirm OK
    assert client.post(f"/api/v1/proposals/{pid}/confirm").status_code == 409   # already executed


# ── HAI-27 — reject ──────────────────────────────────────────────────────────

async def test_reject_pending_marks_rejected_and_emits(store):
    app = _make_app(store)
    client = TestClient(app)
    pid = client.post("/api/v1/proposals", json={"action_type": "deploy"}).json()["data"]["proposal_id"]

    r = client.post(f"/api/v1/proposals/{pid}/reject", json={"reason": "too risky"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "rejected"
    assert data["decided_by"] == "alice"
    assert data["error"] == "too risky"        # reason stored in error
    assert "proposal.rejected" in [et for et, _ in app.state.captured]


async def test_reject_unknown_404_and_non_pending_409(store):
    app = _make_app(store, registry=_registry_with_handler())
    client = TestClient(app)
    assert client.post("/api/v1/proposals/nope/reject").status_code == 404
    pid = client.post("/api/v1/proposals", json={"action_type": "deploy"}).json()["data"]["proposal_id"]
    client.post(f"/api/v1/proposals/{pid}/confirm")               # now executed
    assert client.post(f"/api/v1/proposals/{pid}/reject").status_code == 409


# ── HAI-28 — list / get ──────────────────────────────────────────────────────

async def test_list_and_filter(store):
    client = TestClient(_make_app(store))
    client.post("/api/v1/proposals", json={"action_type": "deploy"})
    client.post("/api/v1/proposals", json={"action_type": "project.create"})

    all_ = client.get("/api/v1/proposals").json()
    assert all_["meta"]["count"] == 2
    # newest first
    assert all_["data"][0]["action_type"] == "project.create"
    # filter by action_type
    deploys = client.get("/api/v1/proposals", params={"action_type": "deploy"}).json()["data"]
    assert [p["action_type"] for p in deploys] == ["deploy"]
    # filter by status
    pending = client.get("/api/v1/proposals", params={"status": "pending"}).json()["data"]
    assert len(pending) == 2


async def test_get_one_and_404(store):
    client = TestClient(_make_app(store))
    pid = client.post("/api/v1/proposals", json={"action_type": "deploy"}).json()["data"]["proposal_id"]
    r = client.get(f"/api/v1/proposals/{pid}")
    assert r.status_code == 200
    assert r.json()["data"]["proposal_id"] == pid
    assert client.get("/api/v1/proposals/nope").status_code == 404
