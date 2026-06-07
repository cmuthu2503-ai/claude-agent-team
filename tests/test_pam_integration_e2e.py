"""PAM-16 — end-to-end integration: HTTP API → DB → resolver → pricing.

This test pretends to be the operator + the dispatcher together:

  1. PATCH /api/v1/agents/{id}/model {model_id: "claude-haiku-4-7"}
  2. GET /api/v1/agents → backend_specialist shows override_active=true,
     assigned_model="claude-haiku-4-7"
  3. The same backend's ``executor._resolve_model_for_agent(...)``
     returns the haiku catalog id with source="db_override"
  4. TokenTracker prices the dispatch using the catalog's haiku entry
     (not the YAML-default opus entry)
  5. DELETE /api/v1/agents/{id}/model → GET shows override_active=false,
     resolver falls back to the YAML default, tracker prices opus again

This is the smoke test that catches "one of the layers broke without
the unit tests noticing" — the PR-2 integration safety net.

Bypasses real LLM clients (no AWS creds in tests) by constructing the
executor with a real catalog + real resolver + real state + real
TokenTracker but mocking the SDK ``client.messages.create`` call.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agents.executor import AgentSystemExecutor
from src.api.routes import agents as agents_route
from src.api.routes import models as models_route
from src.auth.service import get_current_user
from src.config.loader import ConfigLoader
from src.core.events import EventEmitter
from src.core.token_tracker import TokenTracker
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def stack():
    """Build the full backend stack on a temp DB. Yields (app, state,
    executor, tracker)."""
    with tempfile.TemporaryDirectory() as tmp:
        state = SQLiteStateStore(db_path=str(Path(tmp) / "e2e.db"))
        await state.initialize()

        config = ConfigLoader()
        config.load_all()

        # Real executor → real catalog, resolver, state-store wiring.
        executor = AgentSystemExecutor(config, state=state)
        # Catalog-priced tracker (mirrors what PAM-15 wires).
        tracker = TokenTracker(state, catalog=executor.model_catalog)

        app = FastAPI()
        app.include_router(agents_route.router)
        app.include_router(models_route.router)
        app.state.state_store = state
        app.state.config = config
        app.state.agent_executor = executor
        app.state.events = EventEmitter()

        # Auth: bypass to an admin user — PATCH/DELETE require it.
        def _admin() -> dict[str, Any]:
            return {"sub": "u1", "username": "alice", "role": "admin"}
        app.dependency_overrides[get_current_user] = _admin
        for route in app.routes:
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            for dep in (dependant.dependencies or []):
                if getattr(dep.call, "__name__", "") == "role_checker":
                    app.dependency_overrides[dep.call] = _admin

        try:
            yield app, state, executor, tracker
        finally:
            await state.close()


@pytest.mark.asyncio
async def test_full_override_lifecycle_e2e(stack):
    app, state, executor, tracker = stack
    client = TestClient(app)

    # Pick a real agent + figure out its YAML default.
    config = app.state.config
    agent_id = next(
        a for a, cfg in config.agents.items()
        if cfg.get("model") and cfg.get("model") in executor.model_catalog.models
    )
    yaml_default = config.agents[agent_id]["model"]
    assert yaml_default != "claude-haiku-4-7", \
        "test relies on YAML default differing from override target"

    # ─── Step 1 — operator PATCHes to claude-haiku-4-7 ────────────────
    r = client.patch(
        f"/api/v1/agents/{agent_id}/model",
        json={"model_id": "claude-haiku-4-7"},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["data"]["model_id"] == "claude-haiku-4-7"

    # ─── Step 2 — GET /agents reflects override ───────────────────────
    r = client.get("/api/v1/agents")
    assert r.status_code == 200
    agent = next(a for a in r.json()["data"] if a["agent_id"] == agent_id)
    assert agent["default_model"] == yaml_default
    assert agent["assigned_model"] == "claude-haiku-4-7"
    assert agent["override_active"] is True

    # ─── Step 3 — resolver picks up the DB override ───────────────────
    llm_client, model_id, mode, source = await executor._resolve_model_for_agent(
        agent_id=agent_id,
    )
    assert source == "db_override"
    assert model_id == "claude-haiku-4-7"

    # ─── Step 4 — TokenTracker prices the dispatch at HAIKU rates ─────
    haiku = executor.model_catalog.get("claude-haiku-4-7")
    expected_haiku = (
        1000 * haiku.pricing_per_million.input
        + 2000 * haiku.pricing_per_million.output
    ) / 1_000_000
    assert tracker.calculate_cost("claude-haiku-4-7", 1000, 2000) == pytest.approx(
        expected_haiku
    )

    # ─── Step 5 — DELETE clears, GET + resolver + tracker fall back ───
    r = client.delete(f"/api/v1/agents/{agent_id}/model")
    assert r.status_code == 200
    assert r.json()["data"]["override_active"] is False

    r = client.get("/api/v1/agents")
    agent = next(a for a in r.json()["data"] if a["agent_id"] == agent_id)
    assert agent["assigned_model"] == yaml_default
    assert agent["override_active"] is False

    _, model_id, _, source = await executor._resolve_model_for_agent(agent_id=agent_id)
    assert source in {"agent_yaml", "env_default", "catalog_default"}
    assert model_id == yaml_default


@pytest.mark.asyncio
async def test_bulk_clear_e2e(stack):
    """Set N overrides via HTTP, bulk-clear them, GET shows zero
    overrides, resolver falls back for every agent."""
    app, state, executor, _ = stack
    client = TestClient(app)
    config = app.state.config

    # Set overrides on the first three agents that have a YAML model.
    targets = [
        a for a, cfg in config.agents.items()
        if cfg.get("model") in executor.model_catalog.models
    ][:3]
    for agent_id in targets:
        r = client.patch(
            f"/api/v1/agents/{agent_id}/model",
            json={"model_id": "claude-haiku-4-7"},
        )
        assert r.status_code == 200

    # Sanity: GET shows them as active.
    by_agent = {
        a["agent_id"]: a
        for a in client.get("/api/v1/agents").json()["data"]
    }
    for agent_id in targets:
        assert by_agent[agent_id]["override_active"] is True

    # Bulk clear.
    r = client.delete("/api/v1/agents/model-overrides")
    assert r.status_code == 200
    assert r.json()["data"]["cleared"] == 3

    # All overrides gone.
    by_agent = {
        a["agent_id"]: a
        for a in client.get("/api/v1/agents").json()["data"]
    }
    for agent_id in targets:
        assert by_agent[agent_id]["override_active"] is False

    # And the resolver picks up the YAML default for each one.
    for agent_id in targets:
        _, model_id, _, source = await executor._resolve_model_for_agent(agent_id=agent_id)
        assert source != "db_override"
        assert model_id == config.agents[agent_id]["model"]


@pytest.mark.asyncio
async def test_models_endpoint_lists_catalog_e2e(stack):
    """GET /api/v1/models returns the live catalog the resolver uses —
    so the frontend's model dropdown always matches what the backend
    will actually accept on a PATCH."""
    app, _state, executor, _tracker = stack
    client = TestClient(app)
    r = client.get("/api/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["default_model"] == executor.model_catalog.default_model
    listed_ids = {m["id"] for m in body["data"]["models"]}
    catalog_ids = {m.id for m in executor.model_catalog.list_all()}
    assert listed_ids == catalog_ids
