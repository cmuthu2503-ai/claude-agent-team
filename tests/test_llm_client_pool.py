"""PAM-03 / PAM-08 — LLMClientPool tests.

What this pins:
  - get_for caches per (provider_type, base_url) tuple — same model
    returns same instance, different base_urls don't collide
  - Missing OpenAI SDK raises ProviderUnavailableError (not crashes)
  - Failed builds are remembered so the second call doesn't re-spam
  - Missing required env vars raises a clear message
  - clear() drops both cached clients AND failure memory
  - cached_keys() is sorted (for stable test output)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.client_pool import (
    LLMClientPool,
    ProviderUnavailableError,
)
from src.models.catalog import ModelDef, ModelPricing


def _model(
    id: str = "test-model",
    provider_type: str = "anthropic_aws",
    base_url: str | None = None,
    api_key_env: str | None = None,
    tier: str = "frontier",
) -> ModelDef:
    return ModelDef(
        id=id,
        provider_type=provider_type,  # type: ignore[arg-type]
        model_id="m",
        api_key_env=api_key_env,
        base_url=base_url,
        tool_calling_mode="native",
        tier=tier,  # type: ignore[arg-type]
        pricing_per_million=ModelPricing(input=1.0, output=1.0),
    )


# ── Caching behavior ────────────────────────────────────────────────────


def test_get_for_caches_first_build(monkeypatch):
    """Two calls with the same model return the same client object."""
    pool = LLMClientPool()
    # Pretend Anthropic AWS env is set
    monkeypatch.setenv("ANTHROPIC_AWS_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_AWS_WORKSPACE_ID", "wks_x")
    m = _model(provider_type="anthropic_aws")
    c1 = pool.get_for(m)
    c2 = pool.get_for(m)
    assert c1 is c2
    assert pool.is_cached(m)


def test_get_for_separates_openai_compat_by_base_url(monkeypatch):
    """Two openai_compat models with different base_urls must NOT
    share a client — they'd hit the wrong endpoint."""
    pool = LLMClientPool()
    pytest.importorskip("openai")  # skip when SDK absent
    m1 = _model(
        id="ollama-a",
        provider_type="openai_compat",
        base_url="http://host.docker.internal:11434/v1",
    )
    m2 = _model(
        id="ollama-b",
        provider_type="openai_compat",
        base_url="http://other-host:8080/v1",
    )
    c1 = pool.get_for(m1)
    c2 = pool.get_for(m2)
    assert c1 is not c2
    assert len(pool.cached_keys()) == 2


# ── Provider unavailability ─────────────────────────────────────────────


def test_openai_provider_raises_when_sdk_missing(monkeypatch):
    """If the openai package can't be imported, get_for must raise
    ProviderUnavailableError with an actionable message — not crash."""
    pool = LLMClientPool()
    m = _model(provider_type="openai")

    # Simulate the import failure regardless of whether openai is
    # actually installed in this container.
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict
    ) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("simulated missing openai")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(ProviderUnavailableError) as exc:
            pool.get_for(m)
    assert "openai" in str(exc.value).lower()


def test_anthropic_aws_raises_when_env_missing(monkeypatch):
    """No API key env → ProviderUnavailableError, not crash."""
    monkeypatch.delenv("ANTHROPIC_AWS_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AWS_WORKSPACE_ID", raising=False)
    # Also clear secret files mount path so read_secret returns ""
    monkeypatch.setenv("SECRETS_DIR", "/nope/missing")
    pool = LLMClientPool()
    m = _model(provider_type="anthropic_aws")
    with pytest.raises(ProviderUnavailableError) as exc:
        pool.get_for(m)
    assert "ANTHROPIC_AWS" in str(exc.value)


def test_unknown_provider_type_raises(monkeypatch):
    """Provider not in the dispatch table → clean error."""
    pool = LLMClientPool()
    # ModelDef would reject this at construction, so we synthesize
    # a stand-in object that quacks like ModelDef enough for the pool.
    class _Stub:
        id = "x"
        provider_type = "alien_provider"
        base_url = None
        api_key_env = None
    with pytest.raises(ProviderUnavailableError) as exc:
        pool.get_for(_Stub())  # type: ignore[arg-type]
    assert "unknown provider_type" in str(exc.value)


# ── Failure caching (one-shot warn) ─────────────────────────────────────


def test_failure_is_cached_subsequent_calls_short_circuit(monkeypatch):
    """A second get_for for a permanently-broken model must short-
    circuit on the cached failure rather than re-running the builder."""
    monkeypatch.delenv("ANTHROPIC_AWS_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AWS_WORKSPACE_ID", raising=False)
    monkeypatch.setenv("SECRETS_DIR", "/nope")
    pool = LLMClientPool()
    m = _model(provider_type="anthropic_aws")

    call_count = {"n": 0}
    from src.agents import client_pool as cp

    real_builder = cp._build_anthropic_aws_client

    def wrapped(model):
        call_count["n"] += 1
        return real_builder(model)

    monkeypatch.setattr(cp, "_build_anthropic_aws_client", wrapped)
    monkeypatch.setitem(cp._BUILDERS, "anthropic_aws", wrapped)

    with pytest.raises(ProviderUnavailableError):
        pool.get_for(m)
    with pytest.raises(ProviderUnavailableError):
        pool.get_for(m)
    with pytest.raises(ProviderUnavailableError):
        pool.get_for(m)
    assert call_count["n"] == 1


def test_clear_drops_both_clients_and_failures(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_AWS_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AWS_WORKSPACE_ID", raising=False)
    monkeypatch.setenv("SECRETS_DIR", "/nope")
    pool = LLMClientPool()
    m = _model(provider_type="anthropic_aws")
    with pytest.raises(ProviderUnavailableError):
        pool.get_for(m)
    assert "anthropic_aws" in [k[0] for k in pool._build_failures.keys()]
    pool.clear()
    assert pool._build_failures == {}
    assert pool.cached_keys() == []


# ── Eager warm-up path ──────────────────────────────────────────────────


def test_warm_up_anthropic_aws_returns_none_when_env_missing(monkeypatch):
    """Boot path: when ANTHROPIC_AWS_* is unset, warm-up degrades
    gracefully to None so the executor enters mock mode rather than
    raising on import."""
    monkeypatch.delenv("ANTHROPIC_AWS_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AWS_WORKSPACE_ID", raising=False)
    monkeypatch.setenv("SECRETS_DIR", "/nope")
    pool = LLMClientPool()
    result = pool.warm_up_anthropic_aws()
    assert result is None
    # And nothing got cached
    assert ("anthropic_aws", "") not in pool.cached_keys()


# ── Introspection ───────────────────────────────────────────────────────


def test_cached_keys_returns_sorted_list(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AWS_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_AWS_WORKSPACE_ID", "wks_x")
    pool = LLMClientPool()
    pool.get_for(_model(provider_type="anthropic_aws"))
    keys = pool.cached_keys()
    assert keys == sorted(keys)
