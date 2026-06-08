"""PAM-13 — enriched /agents GET + override mutation routes.

Pinned contracts:
  - GET / surfaces default_model / assigned_model / override_active / tool_count
  - GET / reflects DB overrides in assigned_model + override_active
  - PATCH /{id}/model validates catalog membership, upserts, emits event
  - PATCH on unknown agent → 404
  - PATCH with bad model_id → 422
  - PATCH with legacy provider string → canonicalised to catalog id
  - DELETE /{id}/model returns 204 on success, 404 when none
  - DELETE /model-overrides clears the table and reports the count
  - Admin gate active on all mutations
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import agents as agents_route
from src.auth.service import get_current_user, get_principal
from src.models.catalog import ModelCatalog, default_catalog_path
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStateStore(db_path=str(Path(tmp) / "test.db"))
        await s.initialize()
        try:
            yield s
        finally:
            await s.close()


@pytest.fixture
def catalog() -> ModelCatalog:
    return ModelCatalog.load(default_catalog_path())


def _make_app(
    store: Any,
    catalog: ModelCatalog,
    *,
    as_admin: bool,
    agents_cfg: dict[str, dict] | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(agents_route.router)

    # State
    app.state.state_store = store
    config = MagicMock()
    config.agents = agents_cfg or {
        "backend_specialist": {
            "display_name": "Backend Specialist", "role": "backend",
            "team": "engineering", "model": "claude-opus-4-7",
            "tools": ["file_read", "file_write", "git_operations"],
        },
        "tester_specialist": {
            "display_name": "Tester", "role": "tester",
            "team": "engineering", "model": "claude-sonnet-4-7",
            "tools": ["test_runner"],
        },
    }
    app.state.config = config

    # Executor with catalog (PATCH validation reads it)
    executor = MagicMock()
    executor.model_catalog = catalog
    executor.tool_registry = None
    executor.get_busy_agents = MagicMock(return_value={})
    app.state.agent_executor = executor

    # Event emitter — capture calls so tests can assert on emissions.
    events = MagicMock()
    events.emit = AsyncMock()
    app.state.events = events
    app.state.captured_events = events  # alias for asserts

    # Auth: bypass to a viewer or admin user as the test needs.
    role = "admin" if as_admin else "viewer"
    def _user() -> dict[str, Any]:
        return {"sub": "u1", "username": "tester", "role": role}
    app.dependency_overrides[get_current_user] = _user
    # HAI-16 — list_agents now authenticates via get_principal (JWT or service
    # token); override it too so the no-auth TestClient calls resolve to _user.
    app.dependency_overrides[get_principal] = _user

    # Override every require_role('admin') dependable so admin-gated
    # routes resolve to the same user.
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for dep in (dependant.dependencies or []):
            if getattr(dep.call, "__name__", "") == "role_checker":
                # As admin: bypass; as viewer: forbid by leaving the
                # default in place (will 401 because no token).
                if as_admin:
                    app.dependency_overrides[dep.call] = _user
    return app


# ── GET / surfaces enriched fields ──────────────────────────────────────


async def test_list_agents_includes_new_fields(store, catalog):
    app = _make_app(store, catalog, as_admin=False)
    # Active subtasks / counters — stub the calls the route makes.
    store.get_active_subtasks = AsyncMock(return_value=[])
    store.count_subtasks_by_agent = AsyncMock(return_value={})

    client = TestClient(app)
    r = client.get("/api/v1/agents")
    assert r.status_code == 200
    data = r.json()["data"]
    backend = next(a for a in data if a["agent_id"] == "backend_specialist")
    assert backend["default_model"] == "claude-opus-4-7"
    assert backend["assigned_model"] == "claude-opus-4-7"
    assert backend["override_active"] is False
    assert backend["tool_count"] == 3  # from config fallback
    # Legacy ``model`` field still present + accurate.
    assert backend["model"] == "claude-opus-4-7"


async def test_list_agents_reflects_db_override(store, catalog):
    await store.set_agent_model_override("backend_specialist", "claude-haiku-4-7", "alice")
    app = _make_app(store, catalog, as_admin=False)
    store.get_active_subtasks = AsyncMock(return_value=[])
    store.count_subtasks_by_agent = AsyncMock(return_value={})

    client = TestClient(app)
    backend = next(
        a for a in client.get("/api/v1/agents").json()["data"]
        if a["agent_id"] == "backend_specialist"
    )
    assert backend["default_model"] == "claude-opus-4-7"
    assert backend["assigned_model"] == "claude-haiku-4-7"
    assert backend["override_active"] is True
    assert backend["model"] == "claude-haiku-4-7"  # legacy field follows assigned


# ── PATCH /{id}/model ───────────────────────────────────────────────────


async def test_patch_assigns_model_and_emits_event(store, catalog):
    app = _make_app(store, catalog, as_admin=True)
    client = TestClient(app)

    r = client.patch(
        "/api/v1/agents/backend_specialist/model",
        json={"model_id": "claude-haiku-4-7"},
    )
    assert r.status_code == 200, r.json()
    body = r.json()["data"]
    assert body["agent_id"] == "backend_specialist"
    assert body["model_id"] == "claude-haiku-4-7"
    assert body["override_active"] is True

    # Persisted in DB.
    assert await store.get_agent_model_override("backend_specialist") == "claude-haiku-4-7"

    # Event emitted with the right shape.
    events = app.state.captured_events
    events.emit.assert_awaited()
    args = events.emit.await_args
    assert args.args[0] == "agent.model_changed"
    assert args.args[1]["agent_id"] == "backend_specialist"
    assert args.args[1]["action"] == "assigned"


async def test_patch_404_on_unknown_agent(store, catalog):
    app = _make_app(store, catalog, as_admin=True)
    client = TestClient(app)
    r = client.patch("/api/v1/agents/ghost/model", json={"model_id": "claude-opus-4-7"})
    assert r.status_code == 404


async def test_patch_422_on_unknown_model(store, catalog):
    app = _make_app(store, catalog, as_admin=True)
    client = TestClient(app)
    r = client.patch(
        "/api/v1/agents/backend_specialist/model",
        json={"model_id": "claude-not-a-real-model"},
    )
    assert r.status_code == 422
    assert "not in the catalog" in r.json()["detail"]


async def test_patch_400_on_empty_body(store, catalog):
    app = _make_app(store, catalog, as_admin=True)
    client = TestClient(app)
    r = client.patch("/api/v1/agents/backend_specialist/model", json={"model_id": "  "})
    assert r.status_code == 400


async def test_patch_canonicalises_legacy_provider_string(store, catalog):
    """Legacy YAML strings like 'anthropic_aws_sonnet' map to the
    canonical catalog id via resolve_legacy_provider — must work via
    the API too so old persisted Request.provider rows can be set."""
    app = _make_app(store, catalog, as_admin=True)
    client = TestClient(app)
    r = client.patch(
        "/api/v1/agents/backend_specialist/model",
        json={"model_id": "anthropic_aws_sonnet"},  # legacy alias
    )
    assert r.status_code == 200, r.json()
    assert r.json()["data"]["model_id"] == "claude-sonnet-4-7"  # canonicalised
    assert await store.get_agent_model_override("backend_specialist") == "claude-sonnet-4-7"


# ── DELETE /{id}/model ──────────────────────────────────────────────────


async def test_delete_clears_existing_override(store, catalog):
    await store.set_agent_model_override("backend_specialist", "claude-haiku-4-7", "alice")
    app = _make_app(store, catalog, as_admin=True)
    client = TestClient(app)
    r = client.delete("/api/v1/agents/backend_specialist/model")
    assert r.status_code == 200
    assert r.json()["data"]["override_active"] is False
    assert await store.get_agent_model_override("backend_specialist") is None


async def test_delete_404_when_nothing_to_clear(store, catalog):
    app = _make_app(store, catalog, as_admin=True)
    client = TestClient(app)
    r = client.delete("/api/v1/agents/backend_specialist/model")
    assert r.status_code == 404


# ── DELETE /model-overrides (bulk) ──────────────────────────────────────


async def test_bulk_clear_returns_count(store, catalog):
    await store.set_agent_model_override("a", "claude-opus-4-7")
    await store.set_agent_model_override("b", "claude-haiku-4-7")
    app = _make_app(store, catalog, as_admin=True)
    client = TestClient(app)
    r = client.delete("/api/v1/agents/model-overrides")
    assert r.status_code == 200
    assert r.json()["data"]["cleared"] == 2
    assert await store.list_agent_model_overrides() == []


async def test_bulk_clear_idempotent(store, catalog):
    app = _make_app(store, catalog, as_admin=True)
    client = TestClient(app)
    r = client.delete("/api/v1/agents/model-overrides")
    assert r.status_code == 200
    assert r.json()["data"]["cleared"] == 0
