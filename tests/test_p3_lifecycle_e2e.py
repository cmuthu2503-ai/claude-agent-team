"""HAI-44 (FR-014, M2) — full P3 lifecycle E2E through the gate.

Walks the real create -> confirm -> execute loop across MULTIPLE gated actions via
the real proposals HTTP routes + the real handlers + a real store: a service
principal proposes, a human confirms, the action runs. Proves the milestone that an
agent can drive the project lifecycle but only with a human in the loop at every
step.

The generate steps (prd/apispec/...) invoke LLM agents, so the E2E uses the two
deterministic actions (project.create with create_repo off + project.brief.set)
which exercise the full path without a model call.
"""

import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import proposals as proposals_route
from src.auth.service import get_current_user, get_principal
from src.core.events import EventEmitter
from src.core.proposal_handlers import register_all
from src.core.proposal_registry import ProposalActionRegistry
from src.models.base import ArtifactKind, ProjectStatus
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStateStore(db_path=str(Path(tmp) / "e2e.db"))
        await s.initialize()
        try:
            yield s
        finally:
            await s.close()


def _app(store) -> FastAPI:
    app = FastAPI()
    app.include_router(proposals_route.router)
    app.state.state_store = store
    reg = ProposalActionRegistry()
    register_all(reg)                       # the REAL P3 handlers
    app.state.proposal_registry = reg
    app.state.events = EventEmitter()
    app.state.captured: list = []

    async def _cap(et: str, d: dict) -> None:
        app.state.captured.append((et, d))

    app.state.events.on(_cap)

    # Hermes (service) proposes; a human confirms.
    app.dependency_overrides[get_principal] = lambda: {
        "sub": "stok-1", "username": "hermes-operator", "role": "developer",
        "is_service_token": True,
    }
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "u1", "user_id": "u1", "username": "alice", "role": "admin",
    }
    return app


def _propose(
    client, action_type: str, *, target_ref: str | None = None, payload: dict | None = None
) -> str:
    body: dict[str, Any] = {"action_type": action_type}
    if target_ref:
        body["target_ref"] = target_ref
    if payload:
        body["payload"] = payload
    r = client.post("/api/v1/proposals", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]["proposal_id"]


async def test_full_lifecycle_propose_confirm_execute(store):
    app = _app(store)
    client = TestClient(app)

    # ── Step 1: propose + confirm project.create ──
    pid1 = _propose(client, "project.create", payload={"name": "Atlas-E2E", "create_repo": False})
    # nothing happened yet — still pending, no project
    assert (await store.get_proposal(pid1)).status.value == "pending"
    r1 = client.post(f"/api/v1/proposals/{pid1}/confirm")
    assert r1.status_code == 200
    d1 = r1.json()["data"]
    assert d1["status"] == "executed"
    project_id = d1["result_ref"]
    project = await store.get_project(project_id)
    assert project is not None and project.status == ProjectStatus.ACTIVE
    assert project.name == "Atlas-E2E"

    # ── Step 2: propose + confirm project.brief.set against the new project ──
    pid2 = _propose(
        client, "project.brief.set", target_ref=project_id,
        payload={"content": "Mission: " + "x" * 80},
    )
    r2 = client.post(f"/api/v1/proposals/{pid2}/confirm")
    assert r2.json()["data"]["status"] == "executed"
    art = await store.get_artifact(project_id, ArtifactKind.BRIEF)
    assert art is not None and art.content.startswith("Mission:")

    # ── the audit trail: every step emitted created -> confirmed -> executed ──
    types = [et for et, _ in app.state.captured]
    assert types.count("proposal.created") == 2
    assert types.count("proposal.confirmed") == 2
    assert types.count("proposal.executed") == 2


async def test_nothing_executes_until_confirm(store):
    """The gate's whole point: a proposed lifecycle action does NOT run until a
    human confirms — the project must not exist while the proposal is pending."""
    app = _app(store)
    client = TestClient(app)
    _propose(client, "project.create", payload={"name": "Ghost", "create_repo": False})
    # proposed but unconfirmed → no project created
    assert await store.find_project_by_name("Ghost", active_only=True) is None
