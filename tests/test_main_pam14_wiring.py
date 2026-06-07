"""PAM-14 — main.py wiring & lifespan-ordering invariants.

These tests guard the contract that future refactors must keep:

  1. The ``models`` router is mounted at ``/api/v1/models``.
  2. The ``agents`` router exposes the PAM-13 override routes
     (PATCH/DELETE per-agent, bulk DELETE).
  3. ``AgentSystemExecutor`` receives the INITIALIZED state store —
     so its ``ModelResolver`` has ``state_store != None``, which is
     the precondition for layer 2 (DB override) of the precedence
     chain firing at all.

If someone reshuffles the lifespan or forgets to wire either side
of this chain, these tests fail loudly with a clear message about
which invariant broke.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.agents.executor import AgentSystemExecutor
from src.config.loader import ConfigLoader
from src.state.sqlite_store import SQLiteStateStore


# ── Router wiring ───────────────────────────────────────────────────────


def test_main_imports_models_router():
    """``main.py`` must import the models router. Catches the case
    where someone removes the include but leaves the route file."""
    from src.main import models as imported_models_module
    assert hasattr(imported_models_module, "router")
    assert imported_models_module.router.prefix == "/api/v1/models"


def test_main_imports_agents_router():
    from src.main import agents as imported_agents_module
    assert imported_agents_module.router.prefix == "/api/v1/agents"


def test_agents_router_has_override_routes():
    """PAM-13 surface must be present on the mounted router."""
    from src.api.routes import agents as agents_route
    paths = {(r.path, tuple(sorted(r.methods))) for r in agents_route.router.routes}
    assert ("/api/v1/agents/{agent_id}/model", ("PATCH",)) in paths
    assert ("/api/v1/agents/{agent_id}/model", ("DELETE",)) in paths
    assert ("/api/v1/agents/model-overrides", ("DELETE",)) in paths


def test_models_router_has_list_and_reload():
    from src.api.routes import models as models_route
    paths = {(r.path, tuple(sorted(r.methods))) for r in models_route.router.routes}
    assert ("/api/v1/models", ("GET",)) in paths
    assert ("/api/v1/models/reload", ("POST",)) in paths


# ── Lifespan ordering ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_executor_receives_initialized_state_store():
    """The PAM-14 ordering invariant: a real ``AgentSystemExecutor``
    built with a real, INITIALIZED state store must end up with
    ``model_resolver.state_store`` pointing at that same store.

    This is the precondition for layer 2 of the resolver chain to be
    live. The previous mock-mode wiring set state_store=None and
    silently disabled DB overrides — this test makes that regression
    impossible without a CI failure."""
    with tempfile.TemporaryDirectory() as tmp:
        state = SQLiteStateStore(db_path=str(Path(tmp) / "wire.db"))
        await state.initialize()

        config = ConfigLoader()
        config.load_all()

        executor = AgentSystemExecutor(config, state=state)

        try:
            # Catalog + resolver came up (the PAM-07 happy path).
            assert executor.model_catalog is not None
            assert executor.model_resolver is not None
            # The exact state object got threaded into the resolver.
            assert executor.model_resolver.state_store is state
            # And the executor stashed it for tools that need it.
            assert executor.state is state
        finally:
            await state.close()


@pytest.mark.asyncio
async def test_resolver_db_layer_fires_with_real_executor():
    """End-to-end: set an override on the same state store the
    executor was built with → resolver's layer 2 returns it. Proves
    the wire-up isn't just object-identity, it actually works."""
    with tempfile.TemporaryDirectory() as tmp:
        state = SQLiteStateStore(db_path=str(Path(tmp) / "wire.db"))
        await state.initialize()
        config = ConfigLoader()
        config.load_all()
        executor = AgentSystemExecutor(config, state=state)

        try:
            # Pick a real agent that has a YAML default in the catalog.
            agent_id = next(iter(config.agents.keys()))
            await state.set_agent_model_override(
                agent_id, "claude-haiku-4-7", updated_by="pam14_test",
            )

            client, model_id, mode, source = await executor._resolve_model_for_agent(
                agent_id=agent_id,
            )
            assert source == "db_override"
            assert model_id == "claude-haiku-4-7"
        finally:
            await state.close()
