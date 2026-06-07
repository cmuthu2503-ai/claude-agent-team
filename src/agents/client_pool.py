"""LLM client pool — PAM-03.

One process-wide pool of LLM SDK clients, keyed by
``(provider_type, base_url)``. Constructed once at executor boot;
``get_for(model_def)`` returns the cached client for that model's
provider tuple, building it on first use.

Why pool the clients
--------------------
Every SDK client wraps an httpx connection pool. Opening a fresh
client per request costs:
  - One TLS handshake (~100-300ms over WAN)
  - One auth-token refresh (when applicable)
  - Garbage-collector churn on the pool's internal data structures

Multiplied across 9 agents × hundreds of requests/day, that's
real latency the pool eliminates.

Eager vs lazy construction
--------------------------
  - **Anthropic AWS** (current default, every request uses it): EAGER.
    Build at constructor time so the first request doesn't pay the
    bootstrap cost.
  - **Anthropic direct, Bedrock, OpenAI, openai_compat**: LAZY.
    Operators who never assign these providers shouldn't carry the
    cost of constructing their clients. Built on first ``get_for()``
    call for a model whose ``provider_type`` matches.

The (provider_type, base_url) tuple as cache key
------------------------------------------------
``openai_compat`` covers Ollama, vLLM, Together, Groq — all OpenAI-
protocol-compatible but with DIFFERENT endpoints. A single
``AsyncOpenAI`` instance can only target one base_url at a time, so
the pool keeps separate cached instances per base_url. For providers
where base_url is None (SDK default), the tuple collapses to
``(provider_type, None)`` and one instance is shared.

Missing SDK handling
--------------------
The OpenAI SDK isn't installed by default in this container. If an
operator assigns a model that requires it, ``get_for()`` raises
``ProviderUnavailableError`` with an actionable message naming the
missing package. This is the right time to fail loudly — at request
dispatch, not at boot — because operators with no GPT models will
never hit the failure path.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from src.models.catalog import ModelDef

logger = structlog.get_logger()


class ProviderUnavailableError(RuntimeError):
    """Raised when a model is assigned to a provider whose SDK isn't
    importable in this deployment (e.g. `openai` package missing).
    The agent system catches this and falls back to the default model
    so an outage in one provider can't kill the platform."""


# ── Cache key + per-provider construction helpers ───────────────────────


def _cache_key(model: ModelDef) -> tuple[str, str]:
    """The pool's cache key. base_url=None collapses to empty string so
    the dict hashes consistently regardless of how YAML represented
    null."""
    return (model.provider_type, model.base_url or "")


def _build_anthropic_aws_client(model: ModelDef) -> Any:
    """Build an AsyncAnthropicAWS for Claude Platform on AWS. Uses the
    same env vars + workspace_id resolution as the legacy executor
    so the migration is bit-for-bit compatible with the existing
    auth flow."""
    from anthropic import AsyncAnthropicAWS  # type: ignore[attr-defined]
    from src.utils.secrets import read_secret

    api_key = read_secret(
        "anthropic_aws_api_key",
        model.api_key_env or "ANTHROPIC_AWS_API_KEY",
    )
    workspace_id = read_secret(
        "anthropic_aws_workspace_id",
        "ANTHROPIC_AWS_WORKSPACE_ID",
    )
    if not api_key or not workspace_id:
        raise ProviderUnavailableError(
            "Claude Platform on AWS requires both ANTHROPIC_AWS_API_KEY "
            "and ANTHROPIC_AWS_WORKSPACE_ID to be set. Provider type "
            f"'{model.provider_type}' for model '{model.id}' can't be "
            "constructed without them."
        )
    return AsyncAnthropicAWS(
        api_key=api_key,
        workspace_id=workspace_id,
    )


def _build_anthropic_direct_client(model: ModelDef) -> Any:
    """Build an AsyncAnthropic for the direct API. Used as a failover
    path when Claude Platform on AWS has an outage."""
    from anthropic import AsyncAnthropic

    api_key = os.getenv(model.api_key_env or "ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ProviderUnavailableError(
            f"Anthropic direct API requires {model.api_key_env or 'ANTHROPIC_API_KEY'} "
            f"to be set. Model '{model.id}' can't be constructed without it."
        )
    return AsyncAnthropic(api_key=api_key)


def _build_bedrock_client(model: ModelDef) -> Any:
    """Build an AsyncAnthropicBedrock. boto3 resolves AWS creds + region
    from the standard chain (env, ~/.aws/credentials, IAM role) — we
    don't pass them explicitly."""
    from anthropic import AsyncAnthropicBedrock

    return AsyncAnthropicBedrock(
        aws_region=os.getenv("AWS_REGION", "us-west-2"),
    )


def _build_openai_client(model: ModelDef) -> Any:
    """Build an AsyncOpenAI for the OpenAI direct API. Imports the SDK
    lazily — operators who never assign an OpenAI model don't need
    the package installed."""
    try:
        from openai import AsyncOpenAI  # type: ignore[import-not-found]
    except ImportError as e:
        raise ProviderUnavailableError(
            f"Model '{model.id}' requires the 'openai' package but it's "
            f"not installed. Add 'openai>=1.50' to pyproject.toml "
            f"dependencies and rebuild the backend image."
        ) from e

    api_key = os.getenv(model.api_key_env or "OPENAI_API_KEY", "")
    if not api_key:
        raise ProviderUnavailableError(
            f"OpenAI provider requires {model.api_key_env or 'OPENAI_API_KEY'} "
            f"to be set. Model '{model.id}' can't be constructed without it."
        )
    return AsyncOpenAI(api_key=api_key)


def _build_openai_compat_client(model: ModelDef) -> Any:
    """Build an AsyncOpenAI pointed at a custom base_url — Ollama, vLLM,
    Together, Groq, etc. all speak the OpenAI protocol but at their
    own endpoints.

    api_key handling differs from the direct OpenAI path:
      - Some endpoints (Ollama on localhost) accept any string
      - Some (Together, Groq) require a real key
      - When ``api_key_env`` is null in the catalog, we send a dummy
        key so the SDK doesn't complain about None.
    """
    try:
        from openai import AsyncOpenAI  # type: ignore[import-not-found]
    except ImportError as e:
        raise ProviderUnavailableError(
            f"Model '{model.id}' (provider_type=openai_compat) requires "
            f"the 'openai' package but it's not installed."
        ) from e

    if not model.base_url:
        # ModelDef validators in PAM-02 already enforce this; defensive
        # belt-and-braces here so the SDK doesn't silently fall through
        # to api.openai.com.
        raise ProviderUnavailableError(
            f"Model '{model.id}' provider_type=openai_compat but base_url "
            f"is empty. Set base_url in config/models.yaml."
        )

    api_key = (
        os.getenv(model.api_key_env, "")
        if model.api_key_env
        else "openai-compat-stub"  # Ollama et al. ignore this
    )
    return AsyncOpenAI(api_key=api_key, base_url=model.base_url)


# Dispatch table — one builder per provider_type. Adding a new
# provider type means adding one entry here AND a Literal value in
# src/models/catalog.py::ProviderType.
_BUILDERS = {
    "anthropic_aws":  _build_anthropic_aws_client,
    "anthropic":      _build_anthropic_direct_client,
    "bedrock":        _build_bedrock_client,
    "openai":         _build_openai_client,
    "openai_compat":  _build_openai_compat_client,
}


# ── Pool ────────────────────────────────────────────────────────────────


class LLMClientPool:
    """Process-wide cache of LLM SDK clients keyed by
    ``(provider_type, base_url)``. One instance per process; the
    AgentSystemExecutor holds it.

    Thread/asyncio safety: the underlying dict is populated lazily
    inside ``get_for()``, which is sync. Concurrent first-use for the
    same key may build two clients and one wins — both are valid
    instances and the loser is GC'd. We don't lock because the cost
    of double-construction (~50ms once) is tiny vs. the cost of a
    lock contended by every agent call (held forever).
    """

    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], Any] = {}
        # Track which keys we've LOGGED a build event for so the warn
        # path in get_for can be one-shot per key (otherwise an outage
        # spams logs).
        self._build_failures: dict[tuple[str, str], str] = {}

    # ── Eager warm-up ────────────────────────────────────────────────────

    def warm_up_anthropic_aws(self, model: ModelDef | None = None) -> Any | None:
        """Eagerly build the Claude Platform on AWS client at executor
        boot. Returns the client (or None when env isn't set — the
        legacy executor degrades to mock mode in that case)."""
        if model is None:
            # Synthesize a minimal ModelDef-shaped object for the build
            # path; we just need provider_type + api_key_env.
            class _Stub:
                provider_type = "anthropic_aws"
                base_url = None
                api_key_env = "ANTHROPIC_AWS_API_KEY"
                id = "warmup"
            model = _Stub()  # type: ignore[assignment]
        try:
            client = _build_anthropic_aws_client(model)  # type: ignore[arg-type]
        except ProviderUnavailableError as e:
            logger.warning(
                "anthropic_aws_warmup_skipped",
                reason=str(e),
                hint="executor will fall back to mock mode for default model",
            )
            return None
        key = ("anthropic_aws", "")
        self._clients[key] = client
        logger.info("llm_client_built", provider_type="anthropic_aws", eager=True)
        return client

    def register_prebuilt(
        self,
        provider_type: str,
        client: Any,
        base_url: str | None = None,
    ) -> None:
        """Inject a pre-built client into the pool's cache.

        PAM-07: the executor already constructs the AnthropicAWS client
        at boot (eagerly, with env-var-driven config). Registering it
        here lets ``get_for(model)`` return that same instance for any
        ``anthropic_aws`` model — no duplicate construction, no second
        SDK handle. Lazy paths still apply for any provider that wasn't
        pre-registered.
        """
        key = (provider_type, base_url or "")
        self._clients[key] = client
        logger.info(
            "llm_client_prebuilt_registered",
            provider_type=provider_type, base_url=base_url or "",
        )

    # ── Lazy get ─────────────────────────────────────────────────────────

    def get_for(self, model: ModelDef) -> Any:
        """Return the SDK client for *model*'s provider, building it on
        first use. Raises ``ProviderUnavailableError`` when the
        provider's SDK is missing or required env vars are absent."""
        key = _cache_key(model)
        existing = self._clients.get(key)
        if existing is not None:
            return existing

        # If we previously failed to build this exact key, short-circuit
        # with the cached error message — avoids spamming logs on every
        # agent call when a provider is permanently broken.
        prior_fail = self._build_failures.get(key)
        if prior_fail:
            raise ProviderUnavailableError(prior_fail)

        builder = _BUILDERS.get(model.provider_type)
        if builder is None:
            raise ProviderUnavailableError(
                f"unknown provider_type '{model.provider_type}' for "
                f"model '{model.id}' (no builder registered)"
            )

        try:
            client = builder(model)
        except ProviderUnavailableError as e:
            # Cache the failure so subsequent calls fail fast.
            self._build_failures[key] = str(e)
            logger.warning(
                "llm_client_build_failed",
                provider_type=model.provider_type,
                base_url=model.base_url,
                model_id=model.id,
                error=str(e),
            )
            raise

        self._clients[key] = client
        logger.info(
            "llm_client_built",
            provider_type=model.provider_type,
            base_url=model.base_url or "(default)",
            model_id=model.id,
            eager=False,
        )
        return client

    # ── Introspection helpers (for tests + the /models endpoint) ─────────

    def cached_keys(self) -> list[tuple[str, str]]:
        """List of keys currently in the cache. Stable order for tests."""
        return sorted(self._clients.keys())

    def is_cached(self, model: ModelDef) -> bool:
        return _cache_key(model) in self._clients

    def clear(self) -> None:
        """Drop all cached clients. Used by tests and by
        ``POST /api/v1/models/reload`` (PAM-12) — operators editing
        models.yaml may have changed the auth env vars."""
        self._clients.clear()
        self._build_failures.clear()
