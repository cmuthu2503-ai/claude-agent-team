"""PAM-09/10/11 — agent_model_overrides table + CRUD + resolver wiring.

Pinned contracts:
  - Schema created via SCHEMA_SQL at initialize() — no separate migration
  - PK constraint: at most one row per agent_id (upsert semantics)
  - get_* returns None when no row exists
  - delete_* returns True/False to drive 404 vs 204 in the API layer
  - clear_all_* returns the count, enables a one-click reset
  - ModelResolver.resolve() picks up the override at layer 2 when state
    is wired; layer 1 (request_override) still beats it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.agents.client_pool import LLMClientPool
from src.agents.model_resolver import ModelResolver
from src.models.catalog import ModelCatalog, default_catalog_path
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store():
    """Real SQLite store on a temp file — exercises actual schema."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        s = SQLiteStateStore(db_path=db_path)
        await s.initialize()
        try:
            yield s
        finally:
            await s.close()


@pytest.fixture
def catalog() -> ModelCatalog:
    return ModelCatalog.load(default_catalog_path())


# ── CRUD ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_none_when_no_override(store):
    assert await store.get_agent_model_override("backend_specialist") is None


@pytest.mark.asyncio
async def test_set_then_get_roundtrip(store):
    await store.set_agent_model_override(
        "backend_specialist", "claude-haiku-4-7", updated_by="alice",
    )
    assert await store.get_agent_model_override("backend_specialist") == "claude-haiku-4-7"


@pytest.mark.asyncio
async def test_set_upserts_on_conflict(store):
    """Second set on same agent_id REPLACES, doesn't error."""
    await store.set_agent_model_override("x", "claude-opus-4-7", "alice")
    await store.set_agent_model_override("x", "claude-sonnet-4-7", "bob")
    assert await store.get_agent_model_override("x") == "claude-sonnet-4-7"
    rows = await store.list_agent_model_overrides()
    assert len(rows) == 1  # PK constraint
    assert rows[0]["updated_by"] == "bob"


@pytest.mark.asyncio
async def test_delete_existing_returns_true(store):
    await store.set_agent_model_override("x", "claude-opus-4-7")
    assert await store.delete_agent_model_override("x") is True
    assert await store.get_agent_model_override("x") is None


@pytest.mark.asyncio
async def test_delete_missing_returns_false(store):
    assert await store.delete_agent_model_override("ghost") is False


@pytest.mark.asyncio
async def test_list_orders_most_recent_first(store):
    """Used by the Team Status overrides panel — newest at top."""
    import asyncio
    await store.set_agent_model_override("a", "claude-opus-4-7")
    await asyncio.sleep(0.01)  # ensure distinct timestamps
    await store.set_agent_model_override("b", "claude-haiku-4-7")
    rows = await store.list_agent_model_overrides()
    ids = [r["agent_id"] for r in rows]
    assert ids == ["b", "a"]


@pytest.mark.asyncio
async def test_clear_all_returns_count(store):
    await store.set_agent_model_override("a", "claude-opus-4-7")
    await store.set_agent_model_override("b", "claude-haiku-4-7")
    await store.set_agent_model_override("c", "claude-sonnet-4-7")
    n = await store.clear_all_agent_model_overrides()
    assert n == 3
    assert await store.list_agent_model_overrides() == []
    # Idempotent — second clear is a no-op.
    assert await store.clear_all_agent_model_overrides() == 0


# ── End-to-end through the resolver ─────────────────────────────────────


class _FakePool:
    """Returns the model_def itself so we can assert on its id."""
    def get_for(self, model):  # noqa: ANN001
        return f"client-{model.id}"


@pytest.mark.asyncio
async def test_resolver_layer2_picks_up_db_override(store, catalog):
    """End-to-end: with state wired, an override row written via the
    store's set_* method wins layer 2 in the resolver."""
    await store.set_agent_model_override(
        "backend_specialist", "claude-haiku-4-7", updated_by="alice",
    )
    resolver = ModelResolver(
        catalog=catalog,
        client_pool=_FakePool(),
        agents_config={"backend_specialist": {"model": "claude-opus-4-7"}},
        state_store=store,
    )
    r = await resolver.resolve(agent_id="backend_specialist")
    assert r.catalog_id == "claude-haiku-4-7"
    assert r.resolution_source == "db_override"


@pytest.mark.asyncio
async def test_resolver_request_override_still_beats_db(store, catalog):
    """Layer 1 (request_provider) beats layer 2 (DB) — the precedence
    invariant must hold even when a DB row exists."""
    await store.set_agent_model_override("backend_specialist", "claude-haiku-4-7")
    resolver = ModelResolver(
        catalog=catalog,
        client_pool=_FakePool(),
        agents_config={"backend_specialist": {"model": "claude-opus-4-7"}},
        state_store=store,
    )
    r = await resolver.resolve(
        agent_id="backend_specialist",
        request_provider="claude-sonnet-4-7",
    )
    assert r.catalog_id == "claude-sonnet-4-7"
    assert r.resolution_source == "request_override"


@pytest.mark.asyncio
async def test_resolver_falls_through_when_no_db_row(store, catalog):
    """No row → layer 3 (YAML default) wins, just like the in-memory
    PAM-05 tests assert."""
    resolver = ModelResolver(
        catalog=catalog,
        client_pool=_FakePool(),
        agents_config={"backend_specialist": {"model": "claude-opus-4-7"}},
        state_store=store,
    )
    r = await resolver.resolve(agent_id="backend_specialist")
    assert r.catalog_id == "claude-opus-4-7"
    assert r.resolution_source == "agent_yaml"


@pytest.mark.asyncio
async def test_resolver_skips_unknown_db_override(store, catalog):
    """An override pointing at a model that's not in the catalog (e.g.
    a stale row after someone removed a model from models.yaml) must
    fall through — never crash the dispatch."""
    # Write directly without validation to simulate a stale row.
    await store.set_agent_model_override("backend_specialist", "claude-deleted-model")
    resolver = ModelResolver(
        catalog=catalog,
        client_pool=_FakePool(),
        agents_config={"backend_specialist": {"model": "claude-opus-4-7"}},
        state_store=store,
    )
    r = await resolver.resolve(agent_id="backend_specialist")
    # DB override was unknown → fell through to YAML default.
    assert r.catalog_id == "claude-opus-4-7"
    assert r.resolution_source == "agent_yaml"
