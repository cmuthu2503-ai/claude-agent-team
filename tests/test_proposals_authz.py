"""HAI-60 (FR-038, NFR-003) — authorization boundary on the approval gate.

The earlier write-block test (test_service_token_write_block.py) exercises the
middleware against *stub* routes. This one wires the REAL middleware together with
the REAL proposals router and asserts the security invariant end-to-end:

  * A live SERVICE token (Hermes) may ONLY create a proposal. It is 403'd on
    /confirm, /reject AND /approve — it can never decide a proposal.
  * The create response never leaks the one-time approval token (HAI-30), so the
    proposer can't self-approve even via the token path.
  * Only a human (JWT) or the one-time channel token may actually confirm/reject.

NFR-003: defense-in-depth — the block is global middleware, independent of any
route's own guard.
"""

import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.service_token_write_block import ServiceTokenWriteBlockMiddleware
from src.api.routes import proposals as proposals_route
from src.auth.service import get_current_user, get_principal, hash_service_token
from src.core.events import EventEmitter
from src.core.proposal_registry import ProposalActionRegistry
from src.state.sqlite_store import SQLiteStateStore

SERVICE_RAW = "hermes_secret_token_value"


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStateStore(db_path=str(Path(tmp) / "authz.db"))
        await s.initialize()
        # A live service principal Hermes would authenticate with.
        await s.create_service_token("stok-1", "hermes", hash_service_token(SERVICE_RAW), "developer")
        try:
            yield s
        finally:
            await s.close()


def _registry() -> ProposalActionRegistry:
    reg = ProposalActionRegistry()

    async def handler(proposal, ctx):
        return {"result_ref": "DONE"}

    reg.register("deploy", handler)
    return reg


def _app(store) -> FastAPI:
    app = FastAPI()
    # REAL middleware + REAL router — the combination under test.
    app.add_middleware(ServiceTokenWriteBlockMiddleware)
    app.include_router(proposals_route.router)
    app.state.state_store = store
    app.state.proposal_registry = _registry()
    events = EventEmitter()
    app.state.events = events
    app.state.captured: list = []

    async def _cap(event_type: str, data: dict) -> None:
        app.state.captured.append((event_type, data))

    events.on(_cap)

    # The proposer is the service principal (matches the service bearer the test
    # sends); confirm/reject resolve a human when they get that far.
    def _principal() -> dict[str, Any]:
        return {
            "sub": "stok-1", "token_id": "stok-1", "username": "hermes",
            "role": "developer", "is_service_token": True,
        }

    app.dependency_overrides[get_principal] = _principal
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u1", "username": "alice", "role": "admin"}
    return app


def _svc() -> dict[str, str]:
    return {"Authorization": f"Bearer {SERVICE_RAW}"}


def _create_as_service(client) -> tuple[str, dict]:
    r = client.post("/api/v1/proposals", json={"action_type": "deploy"}, headers=_svc())
    assert r.status_code == 201, r.text
    return r.json()["data"]["proposal_id"], r.json()["data"]


# ── service token: allowed to propose, forbidden to decide ───────────────────

async def test_service_token_can_create_but_token_not_leaked(store):
    """Create is the ONE write a service token may do — and the response must not
    contain the approval token (so the proposer can't self-approve)."""
    client = TestClient(_app(store))
    _pid, data = _create_as_service(client)
    assert "approval_token" not in data


async def test_service_token_403_on_confirm(store):
    client = TestClient(app := _app(store))
    pid, _ = _create_as_service(client)
    r = client.post(f"/api/v1/proposals/{pid}/confirm", headers=_svc())
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "service_token_write_forbidden"
    # and the proposal is untouched — still pending
    assert (await store.get_proposal(pid)).status.value == "pending"
    # nothing executed
    assert "proposal.executed" not in [et for et, _ in app.state.captured]


async def test_service_token_403_on_reject(store):
    client = TestClient(_app(store))
    pid, _ = _create_as_service(client)
    r = client.post(f"/api/v1/proposals/{pid}/reject", json={"reason": "x"}, headers=_svc())
    assert r.status_code == 403
    assert (await store.get_proposal(pid)).status.value == "pending"


async def test_service_token_403_on_token_approve(store):
    """Even the one-time-token path is barred to a service bearer: /approve is a
    POST that isn't the allow-listed create path, so the middleware 403s it before
    the token is ever checked."""
    client = TestClient(_app(store))
    pid, _ = _create_as_service(client)
    r = client.post(f"/api/v1/proposals/{pid}/approve", json={"token": "anything"}, headers=_svc())
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "service_token_write_forbidden"


# ── positive controls: human + one-time token CAN decide ─────────────────────

async def test_human_jwt_can_confirm(store):
    # No service bearer → middleware passes; the human (get_current_user) decides.
    client = TestClient(_app(store))
    pid, _ = _create_as_service(client)
    r = client.post(f"/api/v1/proposals/{pid}/confirm")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "executed"
    assert r.json()["data"]["decided_by"] == "alice"


async def test_one_time_token_can_approve(store):
    client = TestClient(app := _app(store))
    pid, _ = _create_as_service(client)
    # The raw token rode the proposal.created event (never the response).
    created = [d for et, d in app.state.captured if et == "proposal.created" and d["proposal_id"] == pid]
    token = created[0]["approval_token"]
    # No bearer at all (token IS the authority) → middleware passes, approve runs.
    r = client.post(f"/api/v1/proposals/{pid}/approve", json={"token": token})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "executed"
    assert r.json()["data"]["decided_by"] == "channel:one-time-token"
