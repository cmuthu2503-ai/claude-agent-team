"""Model catalog — PAM-02.

Pydantic-validated loader for ``config/models.yaml`` (PAM-01). Provides
the canonical lookup surface every other PAM module uses:

  - ``ModelDef``     — one entry from the catalog (a model the platform
                        knows about)
  - ``ModelCatalog`` — collection wrapper with id lookup, vendor-id
                        lookup, legacy-provider alias resolution, and
                        the global default_model fallback

Load semantics
--------------
The catalog is loaded ONCE per process via ``ModelCatalog.load(path)``.
The Pydantic models validate every field at parse time so a malformed
``models.yaml`` raises on backend boot — better than failing 30
minutes into the first request because a tier name was misspelled.

PAM-12's ``POST /api/v1/models/reload`` endpoint will call ``load()``
again at admin request to pick up edits without a backend restart;
that's a separate task.

Why not just use raw dicts
--------------------------
Three reasons:
  1. Validation catches typos on boot (a `tier: forntier` typo would
     silently render the model in the wrong UI bucket without this).
  2. Type-narrowed access in the resolver — ``model.tool_calling_mode``
     beats ``model['tool_calling_mode']`` for IDE help and refactor safety.
  3. Centralized rules (pricing must be non-negative; tier must be in
     the allowed set; openai_compat MUST have base_url). The Pydantic
     validators below are the single place these rules live.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


# ── Literal types for enum-style fields ──────────────────────────────────


# Wire-protocol family. Keeping it a Literal (not a free-form str) means
# the resolver + client_pool can switch on it exhaustively and the type
# checker will complain if a new provider is added without handling.
ProviderType = Literal[
    "anthropic",
    "anthropic_aws",
    "bedrock",
    "openai",
    "openai_compat",
]

# Tool calling mode — drives whether the agent system uses the SDK's
# native tool-use API or the PromptedToolAdapter (PAM-04).
ToolCallingMode = Literal["native", "prompted"]

# Tier label for UI grouping and cost-dashboard coloring. The set is
# small on purpose — operators picking from a 12-tier dropdown would
# regret it.
Tier = Literal["frontier", "workhorse", "fast", "local"]


# ── Pricing sub-model ────────────────────────────────────────────────────


class ModelPricing(BaseModel):
    """USD per million tokens. Both fields required; 0.00 is valid
    (local models). Negative values are caught at validate time."""

    input: float = Field(..., ge=0.0)
    output: float = Field(..., ge=0.0)


# ── Single model definition ──────────────────────────────────────────────


class ModelDef(BaseModel):
    """One entry from the catalog. The ``id`` field is the key in the
    parent ``models:`` dict in models.yaml; the loader sets it after
    parsing the dict so users don't have to repeat it in YAML."""

    id: str = Field(..., min_length=1, max_length=80)
    provider_type: ProviderType
    model_id: str = Field(..., min_length=1)
    api_key_env: str | None = None
    base_url: str | None = None
    tool_calling_mode: ToolCallingMode
    tier: Tier
    display_name: str = ""
    pricing_per_million: ModelPricing

    @model_validator(mode="after")
    def _enforce_openai_compat_base_url(self) -> "ModelDef":
        """openai_compat endpoints have NO SDK default — without an
        explicit base_url the OpenAI SDK silently hits api.openai.com,
        which would route a "local" Ollama model to OpenAI's billed
        endpoint. Catch this loudly at boot."""
        if self.provider_type == "openai_compat" and not self.base_url:
            raise ValueError(
                f"model '{self.id}' has provider_type='openai_compat' "
                f"but no base_url — openai_compat REQUIRES an explicit "
                f"endpoint URL (e.g. http://host.docker.internal:11434/v1 "
                f"for Ollama)."
            )
        return self

    @model_validator(mode="after")
    def _enforce_local_tier_pricing(self) -> "ModelDef":
        """`tier: local` means the operator's own hardware — zero cost
        per token. If pricing isn't 0.0 we'd misreport savings on the
        cost dashboard. Catch the inconsistency at boot."""
        if self.tier == "local":
            if (self.pricing_per_million.input != 0.0
                    or self.pricing_per_million.output != 0.0):
                raise ValueError(
                    f"model '{self.id}' has tier='local' but nonzero "
                    f"pricing — local models run on operator hardware "
                    f"and MUST have pricing_per_million.{{input,output}}=0.00."
                )
        return self

    @model_validator(mode="after")
    def _default_display_name(self) -> "ModelDef":
        """If display_name is blank, fall back to the catalog id so the
        UI dropdown always has something readable. Done as a model
        validator because field-level validators in Pydantic v2 can't
        reliably reach other fields via ``info.data``."""
        if not self.display_name or not self.display_name.strip():
            # Bypass frozen-instance protections via __dict__ assignment;
            # safe here because this validator runs during construction.
            object.__setattr__(self, "display_name", self.id or self.model_id)
        return self


# ── Catalog wrapper ──────────────────────────────────────────────────────


class ModelCatalog(BaseModel):
    """Parsed view of config/models.yaml. The constructor is private —
    callers use ``ModelCatalog.load(path)`` so the validation path is
    consistent across boot, reload, and tests."""

    default_model: str
    legacy_provider_aliases: dict[str, str] = Field(default_factory=dict)
    models: dict[str, ModelDef]

    # ── Construction ─────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str | Path) -> "ModelCatalog":
        """Read and validate config/models.yaml. Returns a fully
        materialized ModelCatalog or raises ``ValueError`` with a path-
        contextualized message. Never returns a partially-loaded
        catalog — invalid YAML means the backend doesn't boot."""
        p = Path(path)
        if not p.exists():
            raise ValueError(f"model catalog not found at {p}")
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"model catalog at {p} is not valid YAML: {e}") from e
        if not isinstance(raw, dict):
            raise ValueError(f"model catalog at {p} must be a YAML mapping")

        models_raw = raw.get("models") or {}
        if not isinstance(models_raw, dict):
            raise ValueError(f"models: section in {p} must be a mapping")

        # Materialize each entry — set id from the dict key so users
        # don't repeat it in YAML.
        models: dict[str, ModelDef] = {}
        for mid, body in models_raw.items():
            if not isinstance(body, dict):
                raise ValueError(
                    f"model '{mid}' in {p} must be a mapping, got "
                    f"{type(body).__name__}"
                )
            try:
                models[mid] = ModelDef(id=mid, **body)
            except Exception as e:  # noqa: BLE001
                # Re-raise with the offending model id baked into the
                # message — Pydantic's default error doesn't include it.
                raise ValueError(
                    f"model '{mid}' in {p} failed validation: {e}"
                ) from e

        if not models:
            raise ValueError(f"models: section in {p} is empty")

        default_model = raw.get("default_model")
        if not default_model or default_model not in models:
            raise ValueError(
                f"default_model '{default_model}' in {p} must reference "
                f"an entry in the models: section "
                f"(available: {sorted(models.keys())})"
            )

        legacy_map = raw.get("legacy_provider_aliases") or {}
        if not isinstance(legacy_map, dict):
            raise ValueError(
                f"legacy_provider_aliases in {p} must be a mapping"
            )
        # Drop alias entries that point at a model not present in the
        # catalog rather than hard-failing. The original strict-check
        # would block boot whenever the catalog ships fewer models than
        # the alias map references (a normal state during phased
        # rollouts — e.g. PAM-23's Ollama entry lands later than the
        # ollama alias is documented). Surviving aliases are still
        # validated; orphans are silently dropped from the materialized
        # catalog so resolve_legacy_provider returns None for them
        # cleanly. This trades a strict boot guard for a forgiving one;
        # operators who want the strict behaviour can grep the loader
        # logs for "legacy_alias_dropped" warnings via structlog.
        import structlog as _sl
        _logger = _sl.get_logger()
        cleaned_legacy: dict[str, str] = {}
        for alias, target in legacy_map.items():
            if target in models:
                cleaned_legacy[alias] = target
            else:
                _logger.warning(
                    "legacy_alias_dropped",
                    alias=alias, target=target,
                    hint="alias target not in catalog — may be a future model",
                )
        legacy_map = cleaned_legacy

        return cls(
            default_model=default_model,
            legacy_provider_aliases=legacy_map,
            models=models,
        )

    # ── Lookup API ───────────────────────────────────────────────────────

    def get(self, model_id: str) -> ModelDef:
        """Return the ModelDef for the given catalog id. Raises
        ``KeyError`` if not found — caller is responsible for
        falling back to default_model or surfacing the error."""
        try:
            return self.models[model_id]
        except KeyError as e:
            raise KeyError(
                f"unknown model id '{model_id}' (catalog has "
                f"{sorted(self.models.keys())})"
            ) from e

    def has(self, model_id: str) -> bool:
        """Membership test — for validators that need a bool, not an
        exception."""
        return model_id in self.models

    def list_all(self) -> list[ModelDef]:
        """Return all models sorted by id. Used by the
        ``GET /api/v1/models`` endpoint (PAM-12)."""
        return [self.models[mid] for mid in sorted(self.models)]

    def list_by_tier(self, tier: Tier) -> list[ModelDef]:
        """Filter helper for UI groupings."""
        return [m for m in self.list_all() if m.tier == tier]

    def resolve_legacy_provider(self, provider: str | None) -> str | None:
        """Map a pre-PAM `provider` string to a catalog model id.
        Returns ``None`` when the input doesn't match a known alias —
        caller falls back to default_model."""
        if not provider:
            return None
        target = self.legacy_provider_aliases.get(provider)
        if target and target in self.models:
            return target
        # Some legacy rows stored the bare model_id (e.g. "claude-opus-4-7")
        # in the provider column — accept those by direct lookup.
        if provider in self.models:
            return provider
        return None

    def find_by_vendor_id(self, vendor_model_id: str) -> ModelDef | None:
        """Reverse lookup: given the vendor's model_id (e.g.
        ``claude-opus-4-7``), return the catalog entry that wraps it.
        When multiple catalog entries share a vendor id (e.g.
        ``claude-opus-4-7`` exists under both ``claude-opus-4-7`` and
        ``claude-opus-4-7-direct``), the first sorted-id match wins —
        callers who need the AWS-routed entry vs the direct one should
        use ``get(id)`` instead."""
        for mid in sorted(self.models):
            m = self.models[mid]
            if m.model_id == vendor_model_id:
                return m
        return None

    # ── Resolution helpers ───────────────────────────────────────────────

    def resolve_id(
        self,
        agent_override: str | None = None,
        yaml_default: str | None = None,
        env_default: str | None = None,
    ) -> str:
        """Three-layer fallback chain ResolveModel uses (PAM-05 will
        wrap this with the DB-backed agent_model_overrides layer):

            1. agent_override   — explicit per-agent assignment
            2. yaml_default     — agent YAML's ``model:`` field
            3. env_default      — ``DEFAULT_AGENT_MODEL`` env var
            4. catalog default  — ``default_model:`` from models.yaml

        Each layer is skipped when its value is None/empty or points
        at an unknown id. Always returns a valid catalog id."""
        for candidate in (agent_override, yaml_default, env_default):
            if candidate and candidate in self.models:
                return candidate
            # Tolerate legacy provider strings at any layer.
            mapped = self.resolve_legacy_provider(candidate) if candidate else None
            if mapped:
                return mapped
        return self.default_model


# ── Module-level convenience ────────────────────────────────────────────


_DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "models.yaml"
)


def default_catalog_path() -> Path:
    """Resolve the canonical catalog path. Override via
    ``MODELS_CATALOG_PATH`` env var for tests that point at a
    fixture file."""
    override = os.getenv("MODELS_CATALOG_PATH")
    if override:
        return Path(override)
    return _DEFAULT_CATALOG_PATH
