"""Model resolver — PAM-05.

The single entry point the agent executor calls to figure out which
model + client + tool-calling mode to use for ONE agent invocation.

Wraps the three foundation pieces from PR-1:
  - ``ModelCatalog`` (PAM-02) — what models exist
  - ``LLMClientPool`` (PAM-03) — how to get a client for a model
  - The agent YAML configs (loaded by ConfigLoader) — per-agent defaults

Resolution precedence (lowest priority last)
--------------------------------------------

    1. request_provider               (per-request override from
                                       Command Center submit form,
                                       Request.provider column for
                                       in-flight retries, etc.)

    2. agent_model_overrides DB row   (PAM-09/10 — operator-set via
                                       Team Status UI). Reads only
                                       when state_store is wired
                                       AND the row exists.

    3. agent YAML `model:` field      (config/agents/<id>.yaml)

    4. DEFAULT_AGENT_MODEL env var    (deployment-wide pin)

    5. ModelCatalog.default_model     (last-resort fallback)

Each layer is SKIPPED when its candidate value:
  - is None / empty string, OR
  - resolves to an unknown model id (caught by catalog membership check)

Legacy-string handling
----------------------
Layers 1-4 accept either a catalog id (``claude-opus-4-7``) OR a pre-
PAM provider string (``anthropic_aws_sonnet``). The latter goes through
``catalog.resolve_legacy_provider`` so old persisted Request rows and
old YAML model fields keep working without a data migration.

State-store coupling
--------------------
``state_store`` is passed in at constructor time so the resolver can
remain pure-Python (no FastAPI dependency, no `Request` import). When
state_store is None (mock mode, unit tests, or PAM-09 hasn't landed
yet), the DB-override layer is silently skipped — falling through to
the YAML default. This is the L25 ("INSUFFICIENT_DATA is a verdict")
pattern: missing state isn't an error, it's a known fallback path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import structlog

from src.models.catalog import ModelCatalog, ModelDef

logger = structlog.get_logger()


# ── Public result type ──────────────────────────────────────────────────


# Where the resolved model came from. Surfaced in the result so the
# /agents endpoint can show "override active" badges in the UI, and so
# logs explain why a particular agent ran on a particular model.
ResolutionSource = Literal[
    "request_override",
    "db_override",
    "agent_yaml",
    "env_default",
    "catalog_default",
]


@dataclass
class ResolvedModel:
    """Everything BaseAgent._call_llm needs for ONE invocation.

    Threading these through kwargs (PAM-06) rather than mutating
    ``self.model`` / ``self._llm_client`` is the L24-style concurrency
    fix — two concurrent calls on the same agent with different models
    can't cross-pollinate when the values live on the call stack
    instead of the instance."""

    client: Any
    """The SDK client to make the call with (AsyncAnthropicAWS,
    AsyncOpenAI, etc. — produced by LLMClientPool.get_for)."""

    vendor_model_id: str
    """The string to pass to the SDK's ``model=`` parameter. Distinct
    from the catalog id: the catalog might call an entry
    ``claude-opus-4-7-direct`` while the vendor model id is just
    ``claude-opus-4-7``."""

    model_def: ModelDef
    """The full catalog entry — carries tool_calling_mode, tier,
    pricing, etc. that the agent system reads per call."""

    tool_calling_mode: Literal["native", "prompted"]
    """Convenience surface for the agent's tool wiring code — duplicates
    ``model_def.tool_calling_mode`` so the most-read field doesn't
    require dotting into model_def."""

    resolution_source: ResolutionSource
    """Where this model came from. Used by the /agents endpoint to
    show the "override active" badge and by structlog audit lines."""

    @property
    def catalog_id(self) -> str:
        """The catalog's id for this model (the key in models.yaml).
        Shortcut so agent traces can log a stable identifier."""
        return self.model_def.id


# ── State-store protocol (no FastAPI dependency) ───────────────────────


class _StateStoreProto(Protocol):
    """Minimal surface ModelResolver depends on. Lets unit tests pass
    a stub without importing SQLiteStateStore."""

    async def get_agent_model_override(self, agent_id: str) -> str | None:
        ...


# ── Resolver ────────────────────────────────────────────────────────────


class ModelResolver:
    """One per executor. Holds references to the catalog + client pool +
    optional state store; resolves per agent invocation."""

    def __init__(
        self,
        catalog: ModelCatalog,
        client_pool: Any,  # LLMClientPool — Any avoids circular import
        agents_config: dict[str, dict[str, Any]] | None = None,
        state_store: _StateStoreProto | None = None,
    ) -> None:
        self.catalog = catalog
        self.client_pool = client_pool
        # agents_config is the dict from ConfigLoader.agents — maps
        # agent_id → its YAML body. Stored verbatim so a YAML
        # hot-reload (out of scope for PR-1) updates the resolver
        # without reconstructing it.
        self.agents_config = agents_config or {}
        self.state_store = state_store

    # ── Public API ──────────────────────────────────────────────────────

    async def resolve(
        self,
        agent_id: str,
        request_provider: str | None = None,
    ) -> ResolvedModel:
        """Resolve the model for ONE agent invocation.

        Args:
            agent_id: The agent making the call. Used to look up
                YAML default + DB override.
            request_provider: Per-request override from the Command
                Center submit form (or in-flight retry's stored
                provider). May be a catalog id OR a legacy provider
                string; both are accepted.

        Returns:
            A fully populated ``ResolvedModel``. Always returns —
            unknown / broken layers are skipped, never raised. The
            final fallback is the catalog's ``default_model`` which
            ModelCatalog.load already validated to exist.

        Raises:
            ``ProviderUnavailableError`` (from LLMClientPool) when
            the resolved model's provider can't be constructed (e.g.
            missing SDK or env var). Caller is expected to either
            fall back to a different model or surface the error.
        """
        catalog_id, source = await self._resolve_catalog_id(
            agent_id=agent_id,
            request_provider=request_provider,
        )
        model_def = self.catalog.get(catalog_id)
        client = self.client_pool.get_for(model_def)

        resolved = ResolvedModel(
            client=client,
            vendor_model_id=model_def.model_id,
            model_def=model_def,
            tool_calling_mode=model_def.tool_calling_mode,
            resolution_source=source,
        )
        logger.info(
            "model_resolved",
            agent_id=agent_id,
            catalog_id=catalog_id,
            vendor_model_id=model_def.model_id,
            provider_type=model_def.provider_type,
            tool_calling_mode=model_def.tool_calling_mode,
            source=source,
        )
        return resolved

    # ── Internal: walk the precedence chain ─────────────────────────────

    async def _resolve_catalog_id(
        self,
        agent_id: str,
        request_provider: str | None,
    ) -> tuple[str, ResolutionSource]:
        """Returns (catalog_id, resolution_source). Caller materialises
        the ModelDef via the catalog. Pure-string layer so it's easy
        to unit-test without constructing clients."""

        # Layer 1 — explicit per-request override
        candidate = self._normalize(request_provider)
        if candidate:
            return candidate, "request_override"

        # Layer 2 — DB override (PAM-09/10). Silently skipped when
        # state_store is None or the lookup raises (resolver MUST
        # NOT block on DB issues).
        if self.state_store is not None:
            try:
                db_val = await self.state_store.get_agent_model_override(agent_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "model_resolver_db_lookup_failed",
                    agent_id=agent_id, error=str(e),
                )
                db_val = None
            candidate = self._normalize(db_val)
            if candidate:
                return candidate, "db_override"

        # Layer 3 — agent YAML's ``model:`` field
        agent_cfg = self.agents_config.get(agent_id) or {}
        candidate = self._normalize(agent_cfg.get("model"))
        if candidate:
            return candidate, "agent_yaml"

        # Layer 4 — DEFAULT_AGENT_MODEL env var
        candidate = self._normalize(os.getenv("DEFAULT_AGENT_MODEL"))
        if candidate:
            return candidate, "env_default"

        # Layer 5 — catalog's documented default
        return self.catalog.default_model, "catalog_default"

    def _normalize(self, value: str | None) -> str | None:
        """Accept either a catalog id directly OR a legacy provider
        string. Returns the canonical catalog id, or None when the
        input doesn't resolve to anything known."""
        if not value or not isinstance(value, str):
            return None
        value = value.strip()
        if not value:
            return None
        if self.catalog.has(value):
            return value
        # Try legacy alias map
        mapped = self.catalog.resolve_legacy_provider(value)
        if mapped:
            return mapped
        # Unknown — skip this layer rather than crash. Logged so
        # operators with a typo in YAML can spot it in traces.
        logger.warning(
            "model_resolver_unknown_candidate",
            candidate=value,
            hint="skipping this resolution layer; falling through to next",
        )
        return None
