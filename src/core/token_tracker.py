"""Token tracker — captures LLM usage and calculates cost.

PAM-15 — pricing source migration
---------------------------------
The runtime pricing source is now ``config/models.yaml`` (the PAM-01
catalog). Each ``ModelDef`` carries ``pricing_per_million.input`` and
``.output`` as cents-per-million-tokens, indexed by catalog id (e.g.
``claude-opus-4-7``).

Why move pricing into the catalog
  - It's the single source of truth for "which models exist." Pricing
    that lived in ``thresholds.yaml`` would drift behind a new model
    being added to the catalog — the silent-$0 bug.
  - The Team Status / cost dashboard reads the same yaml the resolver
    does, so an operator who adds a model to the catalog gets accurate
    cost columns immediately.

Back-compat
  - If a row's ``model`` field is a legacy provider string
    (``anthropic_aws_sonnet``), we resolve it through the catalog's
    alias map before lookup. That keeps old persisted ``token_usage``
    rows costing correctly after the migration.
  - If the catalog can't load, we fall back to ``thresholds.yaml``'s
    legacy ``cost.pricing`` block exactly as before. The platform
    never crashes on cost calculation — at worst we log a warning
    and record a row with ``cost_usd=0``.

Silent-$0 bug fix
  - Before PAM-15, an unknown ``model`` string silently produced
    ``$0`` and the cost dashboard showed zeros forever. Now the first
    occurrence of an unknown id logs a WARNING with the agent + model
    so the operator can spot the typo / missing catalog entry. The
    warning is **deduped per process via ``_warned_models``** so a
    misconfigured agent doesn't spam logs on every dispatch.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
import yaml

from src.models.base import TokenUsage
from src.state.base import StateStore

logger = structlog.get_logger()


class TokenTracker:
    """Records token usage from LLM SDK responses and calculates cost."""

    def __init__(
        self,
        state: StateStore,
        catalog: Any | None = None,
        thresholds_path: str = "config/thresholds.yaml",
    ) -> None:
        """Construct a tracker.

        Args:
            state: Where to persist ``TokenUsage`` rows.
            catalog: Optional ``ModelCatalog`` (PAM-02). When provided,
                pricing is read from the catalog's per-model
                ``pricing_per_million``. When ``None`` (mock-mode boot,
                or catalog load failed), the tracker falls back to
                ``config/thresholds.yaml`` ``cost.pricing`` for
                continuity with pre-PAM-15 deployments.
            thresholds_path: The legacy pricing file. Read only when
                the catalog isn't wired or when the catalog has no
                entry for a given model id (e.g. legacy provider
                strings that haven't been mapped yet).

        Per-process state:
            _warned_models: set of model ids we've already logged a
                "no pricing available" warning for. One warning per
                process keeps the log readable when a misconfigured
                agent runs hundreds of dispatches.
        """
        self.state = state
        self._catalog = catalog
        self._fallback_pricing = self._load_thresholds_pricing(thresholds_path)
        self._warned_models: set[str] = set()

    # ── Pricing sources ─────────────────────────────────────────────────

    @staticmethod
    def _load_thresholds_pricing(path: str) -> dict[str, dict[str, float]]:
        """Read the legacy ``cost.pricing`` block. Soft-fails on a
        missing file (returns ``{}``) so a misplaced thresholds.yaml
        can't crash boot."""
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            return data.get("cost", {}).get("pricing", {})
        except FileNotFoundError:
            logger.warning("pricing_config_not_found", path=path)
            return {}

    def _resolve_pricing(self, model: str) -> tuple[float, float] | None:
        """Return ``(input_price_per_million, output_price_per_million)``
        for *model*, or ``None`` when we can't price it.

        Resolution order:
          1. ``models.yaml`` catalog by catalog id (exact match)
          2. ``models.yaml`` catalog after legacy-alias resolution
             (so old ``anthropic_aws_sonnet`` strings still cost right)
          3. ``thresholds.yaml`` legacy ``cost.pricing`` map
          4. ``None`` — caller will log the dedup'd warning and zero.
        """
        # 1 & 2 — catalog. The catalog's pricing dataclass exposes
        # ``input`` / ``output`` floats (cents per million tokens).
        if self._catalog is not None:
            md = None
            try:
                if self._catalog.has(model):
                    md = self._catalog.get(model)
                else:
                    canonical = self._catalog.resolve_legacy_provider(model)
                    if canonical and self._catalog.has(canonical):
                        md = self._catalog.get(canonical)
            except Exception:  # noqa: BLE001
                # Defensive: a broken catalog must not break cost calc.
                md = None
            if md is not None:
                return (md.pricing_per_million.input, md.pricing_per_million.output)

        # 3 — legacy thresholds.yaml.
        entry = self._fallback_pricing.get(model)
        if entry:
            try:
                return (float(entry.get("input", 0.0)), float(entry.get("output", 0.0)))
            except (TypeError, ValueError):
                return None
        return None

    # ── Cost calc + record ──────────────────────────────────────────────

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Cents per million → USD per token. Returns 0.0 (and emits a
        dedup'd WARNING the first time) for an unknown model. Zero is
        the safe answer: it under-attributes rather than guessing wrong,
        and the warning surfaces the gap so operators can add the entry."""
        prices = self._resolve_pricing(model)
        if prices is None:
            if model and model not in self._warned_models:
                self._warned_models.add(model)
                logger.warning(
                    "token_tracker_no_pricing_for_model",
                    model=model,
                    hint=(
                        "model has no entry in config/models.yaml "
                        "(pricing_per_million) or config/thresholds.yaml "
                        "(cost.pricing). Cost will record as $0.00 until "
                        "the entry is added. This warning is logged once "
                        "per model per process."
                    ),
                )
            return 0.0
        input_price, output_price = prices
        return (input_tokens * input_price + output_tokens * output_price) / 1_000_000

    async def record(
        self, request_id: str, subtask_id: str, agent_id: str,
        model: str, input_tokens: int, output_tokens: int,
    ) -> TokenUsage:
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        usage = TokenUsage(
            usage_id=str(uuid.uuid4()),
            request_id=request_id,
            subtask_id=subtask_id,
            agent_id=agent_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        await self.state.record_token_usage(usage)
        logger.debug(
            "token_usage_recorded", agent=agent_id, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost,
        )
        return usage
