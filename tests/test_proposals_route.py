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
from src.models.base import ProposalStatus
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


# ── HAI-30 — one-time channel-approval token ─────────────────────────────────

def _create_and_grab_token(client, app, action_type="deploy"):
    """Create a proposal and return (pid, raw_token). The raw token is delivered
    ONLY via the proposal.created event — never the API response."""
    body = client.post("/api/v1/proposals", json={"action_type": action_type}).json()
    pid = body["data"]["proposal_id"]
    # SECURITY: raw token must NOT be in the create response (the proposer is Hermes).
    assert "approval_token" not in body["data"]
    created = [d for et, d in app.state.captured if et == "proposal.created" and d["proposal_id"] == pid]
    assert created and created[0].get("approval_token"), "raw token must ride the event"
    return pid, created[0]["approval_token"]


async def test_create_token_in_event_not_in_response(store):
    app = _make_app(store)
    client = TestClient(app)
    pid, token = _create_and_grab_token(client, app)
    assert isinstance(token, str) and len(token) > 20
    # Stored only as a hash; the public projection never leaks it.
    stored = await store.get_proposal(pid)
    assert stored.approval_token_hash and stored.approval_token_hash != token
    assert "approval_token" not in client.get(f"/api/v1/proposals/{pid}").json()["data"]


async def test_approve_with_token_confirms_and_executes(store):
    app = _make_app(store, registry=_registry_with_handler("DEPLOY-OK"))
    client = TestClient(app)
    pid, token = _create_and_grab_token(client, app)

    r = client.post(f"/api/v1/proposals/{pid}/approve", json={"token": token, "decision": "confirm"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "executed"
    assert data["result_ref"] == "DEPLOY-OK"
    assert data["decided_by"] == "channel:one-time-token"
    assert "proposal.executed" in [et for et, _ in app.state.captured]


async def test_approve_with_token_can_reject(store):
    app = _make_app(store)
    client = TestClient(app)
    pid, token = _create_and_grab_token(client, app)

    r = client.post(
        f"/api/v1/proposals/{pid}/approve",
        json={"token": token, "decision": "reject", "reason": "nope"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "rejected"
    assert data["error"] == "nope"
    assert data["decided_by"] == "channel:one-time-token"


async def test_approve_wrong_token_403(store):
    app = _make_app(store, registry=_registry_with_handler())
    client = TestClient(app)
    pid, _token = _create_and_grab_token(client, app)
    r = client.post(f"/api/v1/proposals/{pid}/approve", json={"token": "not-the-token"})
    assert r.status_code == 403
    # proposal untouched — still pending
    assert (await store.get_proposal(pid)).status == ProposalStatus.PENDING


async def test_approve_token_is_single_use(store):
    app = _make_app(store, registry=_registry_with_handler("OK"))
    client = TestClient(app)
    pid, token = _create_and_grab_token(client, app)
    assert client.post(f"/api/v1/proposals/{pid}/approve", json={"token": token}).status_code == 200
    # reuse → 403 (token already spent), even though the proposal is now executed
    assert client.post(f"/api/v1/proposals/{pid}/approve", json={"token": token}).status_code == 403


async def test_approve_unknown_proposal_404(store):
    client = TestClient(_make_app(store))
    assert client.post("/api/v1/proposals/nope/approve", json={"token": "x"}).status_code == 404
