"""PAM-05 / PAM-08 — ModelResolver tests.

Pinned contracts:
  - 5-layer precedence chain in resolution order
  - DB-override layer gracefully degrades when state_store=None
  - DB-override lookup exceptions are swallowed (DB hiccup MUST NOT
    block agent dispatch)
  - Legacy provider strings resolve at any layer
  - Unknown candidates at intermediate layers fall through, not crash
  - Final fallback is always the catalog's default_model
  - ResolvedModel carries all four fields the agent system reads
  - resolution_source label matches the layer that won
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.client_pool import LLMClientPool, ProviderUnavailableError
from src.agents.model_resolver import ModelResolver, ResolvedModel
from src.models.catalog import ModelCatalog, default_catalog_path


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def catalog() -> ModelCatalog:
    return ModelCatalog.load(default_catalog_path())


class _FakeClientPool:
    """Stand-in for LLMClientPool that returns sentinel client objects.
    Lets us verify the resolver hands BACK what the pool would give
    without paying for real client construction."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_for(self, model) -> Any:  # type: ignore[no-untyped-def]
        self.calls.append(model.id)
        return f"<client for {model.id}>"


class _FakeState:
    """Stub state store with controllable get_agent_model_override."""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self._m = mapping or {}
        self.lookups: list[str] = []

    async def get_agent_model_override(self, agent_id: str) -> str | None:
        self.lookups.append(agent_id)
        return self._m.get(agent_id)


class _BrokenState:
    """State store that always raises — exercises the DB-error path."""

    async def get_agent_model_override(self, agent_id: str) -> str | None:
        raise RuntimeError("database connection lost")


@pytest.fixture
def resolver_fixture(catalog):
    pool = _FakeClientPool()
    state = _FakeState({})
    agents_cfg = {
        "backend_specialist": {"model": "claude-opus-4-7"},
        "tester_specialist":  {"model": "claude-sonnet-4-7"},
        "legacy_agent":       {"model": "anthropic_aws_sonnet"},  # legacy alias
        "typo_agent":         {"model": "claude-not-real"},        # unknown
        "no_model_agent":     {},                                  # YAML omits model
    }
    return ModelResolver(
        catalog=catalog,
        client_pool=pool,
        agents_config=agents_cfg,
        state_store=state,
    ), pool, state


# ── Layer-by-layer precedence ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_layer1_request_override_beats_everything(resolver_fixture):
    resolver, _, state = resolver_fixture
    state._m["backend_specialist"] = "claude-sonnet-4-7"  # DB says sonnet
    r = await resolver.resolve(
        agent_id="backend_specialist",
        request_provider="claude-haiku-4-7",  # request says haiku — wins
    )
    assert r.catalog_id == "claude-haiku-4-7"
    assert r.resolution_source == "request_override"


@pytest.mark.asyncio
async def test_layer2_db_override_beats_yaml(resolver_fixture):
    resolver, _, state = resolver_fixture
    state._m["backend_specialist"] = "claude-sonnet-4-7"
    r = await resolver.resolve(agent_id="backend_specialist")
    assert r.catalog_id == "claude-sonnet-4-7"
    assert r.resolution_source == "db_override"


@pytest.mark.asyncio
async def test_layer3_yaml_default_when_no_db_override(resolver_fixture):
    resolver, _, _ = resolver_fixture
    r = await resolver.resolve(agent_id="backend_specialist")
    assert r.catalog_id == "claude-opus-4-7"
    assert r.resolution_source == "agent_yaml"


@pytest.mark.asyncio
async def test_layer4_env_default_when_yaml_omitted(catalog, monkeypatch):
    monkeypatch.setenv("DEFAULT_AGENT_MODEL", "claude-haiku-4-7")
    resolver = ModelResolver(
        catalog=catalog,
        client_pool=_FakeClientPool(),
        agents_config={"some_agent": {}},  # no model field
        state_store=None,
    )
    r = await resolver.resolve(agent_id="some_agent")
    assert r.catalog_id == "claude-haiku-4-7"
    assert r.resolution_source == "env_default"


@pytest.mark.asyncio
async def test_layer5_catalog_default_when_all_layers_silent(catalog, monkeypatch):
    monkeypatch.delenv("DEFAULT_AGENT_MODEL", raising=False)
    resolver = ModelResolver(
        catalog=catalog,
        client_pool=_FakeClientPool(),
        agents_config={},
        state_store=None,
    )
    r = await resolver.resolve(agent_id="unknown_agent")
    assert r.catalog_id == catalog.default_model
    assert r.resolution_source == "catalog_default"


# ── Robustness ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_state_store_none_skips_db_layer_cleanly(catalog):
    """state_store=None must not crash; falls through to YAML."""
    resolver = ModelResolver(
        catalog=catalog,
        client_pool=_FakeClientPool(),
        agents_config={"backend_specialist": {"model": "claude-opus-4-7"}},
        state_store=None,
    )
    r = await resolver.resolve(agent_id="backend_specialist")
    assert r.resolution_source == "agent_yaml"
    assert r.catalog_id == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_db_error_does_not_block_dispatch(catalog):
    """A DB outage during override lookup MUST NOT prevent the agent
    from running — fall through to YAML default."""
    resolver = ModelResolver(
        catalog=catalog,
        client_pool=_FakeClientPool(),
        agents_config={"backend_specialist": {"model": "claude-opus-4-7"}},
        state_store=_BrokenState(),
    )
    r = await resolver.resolve(agent_id="backend_specialist")
    assert r.resolution_source == "agent_yaml"
    assert r.catalog_id == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_legacy_provider_string_resolves_at_yaml_layer(resolver_fixture):
    """Old YAML files may carry `model: anthropic_aws_sonnet`. The
    legacy alias map translates it to claude-sonnet-4-7."""
    resolver, _, _ = resolver_fixture
    r = await resolver.resolve(agent_id="legacy_agent")
    assert r.catalog_id == "claude-sonnet-4-7"
    assert r.resolution_source == "agent_yaml"


@pytest.mark.asyncio
async def test_legacy_provider_string_resolves_at_request_layer(resolver_fixture):
    resolver, _, _ = resolver_fixture
    r = await resolver.resolve(
        agent_id="backend_specialist",
        request_provider="anthropic_aws",  # legacy default alias
    )
    assert r.catalog_id == "claude-opus-4-7"
    assert r.resolution_source == "request_override"


@pytest.mark.asyncio
async def test_unknown_yaml_value_falls_through(resolver_fixture):
    """A typo in YAML's model: field shouldn't crash — falls through
    to env_default (unset) and then catalog_default."""
    resolver, _, _ = resolver_fixture
    r = await resolver.resolve(agent_id="typo_agent")
    assert r.catalog_id == resolver.catalog.default_model
    assert r.resolution_source == "catalog_default"


@pytest.mark.asyncio
async def test_unknown_request_provider_falls_through(resolver_fixture):
    resolver, _, _ = resolver_fixture
    r = await resolver.resolve(
        agent_id="backend_specialist",
        request_provider="not-a-real-model",
    )
    # request_override was unknown → next layer (db_override absent) →
    # yaml_default (claude-opus-4-7) wins.
    assert r.catalog_id == "claude-opus-4-7"
    assert r.resolution_source == "agent_yaml"


@pytest.mark.asyncio
async def test_empty_strings_skipped(catalog):
    """Empty / whitespace values at any layer are treated as absent."""
    resolver = ModelResolver(
        catalog=catalog,
        client_pool=_FakeClientPool(),
        agents_config={"a": {"model": "   "}},  # whitespace only
        state_store=_FakeState({"a": ""}),       # empty string
    )
    r = await resolver.resolve(agent_id="a", request_provider="")
    assert r.resolution_source == "catalog_default"
    assert r.catalog_id == catalog.default_model


# ── ResolvedModel shape ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolved_model_carries_all_four_runtime_fields(resolver_fixture):
    resolver, pool, _ = resolver_fixture
    r = await resolver.resolve(agent_id="backend_specialist")
    assert isinstance(r, ResolvedModel)
    assert r.client is not None
    assert r.vendor_model_id  # vendor SDK string
    assert r.model_def
    assert r.tool_calling_mode in ("native", "prompted")
    # And the client_pool was actually called
    assert pool.calls == ["claude-opus-4-7"]


@pytest.mark.asyncio
async def test_vendor_id_distinct_from_catalog_id(catalog):
    """A catalog entry like claude-opus-4-7-direct wraps vendor id
    claude-opus-4-7. The resolver must report BOTH."""
    resolver = ModelResolver(
        catalog=catalog,
        client_pool=_FakeClientPool(),
        agents_config={"x": {"model": "claude-opus-4-7-direct"}},
    )
    r = await resolver.resolve(agent_id="x")
    assert r.catalog_id == "claude-opus-4-7-direct"
    assert r.vendor_model_id == "claude-opus-4-7"


# ── DB-layer lookup is awaited (smoke check the async path) ─────────────


@pytest.mark.asyncio
async def test_db_lookup_happens_when_state_store_present(resolver_fixture):
    resolver, _, state = resolver_fixture
    await resolver.resolve(agent_id="backend_specialist")
    # Exactly one lookup per resolve call
    assert state.lookups == ["backend_specialist"]


@pytest.mark.asyncio
async def test_db_lookup_skipped_when_request_override_set(resolver_fixture):
    """Short-circuit at layer 1 — DB shouldn't even be hit."""
    resolver, _, state = resolver_fixture
    await resolver.resolve(
        agent_id="backend_specialist",
        request_provider="claude-haiku-4-7",
    )
    assert state.lookups == []
