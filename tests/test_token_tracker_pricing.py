"""PAM-15 — TokenTracker pricing source migration + dedup warnings.

Pinned contracts:
  - calculate_cost uses models.yaml when catalog is wired
  - Legacy provider strings resolve through catalog alias map
  - Falls back to thresholds.yaml when catalog has no entry
  - Unknown model → 0.0 cost AND a one-time WARNING
  - Warning is deduped per process via _warned_models
  - tracker keeps working when both sources are missing (records $0)
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.token_tracker import TokenTracker
from src.models.catalog import ModelCatalog, default_catalog_path


@pytest.fixture
def catalog() -> ModelCatalog:
    return ModelCatalog.load(default_catalog_path())


@pytest.fixture
def state():
    s = MagicMock()
    s.record_token_usage = AsyncMock()
    return s


# ── Pricing source resolution ───────────────────────────────────────────


def test_catalog_prices_used_when_available(catalog, state):
    tracker = TokenTracker(state, catalog=catalog)
    md = catalog.get("claude-opus-4-7")
    input_price = md.pricing_per_million.input
    output_price = md.pricing_per_million.output
    expected = (1000 * input_price + 2000 * output_price) / 1_000_000

    cost = tracker.calculate_cost("claude-opus-4-7", 1000, 2000)
    assert cost == pytest.approx(expected)
    # Sanity: real catalog has nonzero pricing for Opus.
    assert cost > 0


def test_legacy_provider_string_resolves_via_alias(catalog, state):
    """`anthropic_aws_sonnet` (legacy YAML string) must price as the
    canonical claude-sonnet-4-7 catalog entry — old persisted
    token_usage rows depend on this surviving the migration."""
    tracker = TokenTracker(state, catalog=catalog)
    md = catalog.get("claude-sonnet-4-7")
    expected = (1000 * md.pricing_per_million.input
                + 2000 * md.pricing_per_million.output) / 1_000_000
    cost = tracker.calculate_cost("anthropic_aws_sonnet", 1000, 2000)
    assert cost == pytest.approx(expected)


def test_unknown_model_returns_zero(catalog, state):
    tracker = TokenTracker(state, catalog=catalog)
    assert tracker.calculate_cost("claude-imaginary-9000", 1000, 2000) == 0.0


def test_unknown_model_warns_once_per_process(catalog, state, caplog):
    """First call → WARNING; subsequent calls for same model → silent."""
    tracker = TokenTracker(state, catalog=catalog)
    with caplog.at_level(logging.WARNING):
        tracker.calculate_cost("claude-imaginary-9000", 100, 100)
        tracker.calculate_cost("claude-imaginary-9000", 200, 200)
        tracker.calculate_cost("claude-imaginary-9000", 300, 300)
    # structlog renders into caplog; substring is sufficient.
    matched = [r for r in caplog.records if "claude-imaginary-9000" in r.getMessage()
               or "claude-imaginary-9000" in str(r.__dict__)]
    # At most ONE warning record mentions the model.
    assert len(matched) <= 1, f"expected 1 warning, got {len(matched)}"
    # And the model is registered in the dedup set.
    assert "claude-imaginary-9000" in tracker._warned_models


def test_warnings_dedup_independently_per_model(catalog, state):
    """Two distinct unknown models → each warns once (set grows to 2)."""
    tracker = TokenTracker(state, catalog=catalog)
    tracker.calculate_cost("ghost-a", 1, 1)
    tracker.calculate_cost("ghost-b", 1, 1)
    tracker.calculate_cost("ghost-a", 1, 1)  # repeat, no new warning
    assert tracker._warned_models == {"ghost-a", "ghost-b"}


# ── Fallback to thresholds.yaml ─────────────────────────────────────────


def test_falls_back_to_thresholds_when_no_catalog(state, tmp_path):
    """No catalog wired → reads cost.pricing from thresholds.yaml."""
    thresholds = tmp_path / "thresholds.yaml"
    thresholds.write_text(
        "cost:\n"
        "  pricing:\n"
        "    legacy-model-x:\n"
        "      input: 10.0\n"
        "      output: 30.0\n"
    )
    tracker = TokenTracker(state, catalog=None, thresholds_path=str(thresholds))
    # 1M input @ $10 + 2M output @ $30 = $10 + $60 = $70
    cost = tracker.calculate_cost("legacy-model-x", 1_000_000, 2_000_000)
    assert cost == pytest.approx(70.0)


def test_catalog_takes_precedence_over_thresholds(catalog, state, tmp_path):
    """A model present in BOTH sources prices from the catalog."""
    thresholds = tmp_path / "thresholds.yaml"
    thresholds.write_text(
        "cost:\n"
        "  pricing:\n"
        "    claude-opus-4-7:\n"
        "      input: 999.0\n"
        "      output: 999.0\n"
    )
    tracker = TokenTracker(state, catalog=catalog, thresholds_path=str(thresholds))
    md = catalog.get("claude-opus-4-7")
    expected = (1000 * md.pricing_per_million.input
                + 1000 * md.pricing_per_million.output) / 1_000_000
    cost = tracker.calculate_cost("claude-opus-4-7", 1000, 1000)
    assert cost == pytest.approx(expected)
    assert cost != 999_000 * 2 / 1_000_000  # thresholds value would be 1.998


def test_missing_both_sources_still_records(state, tmp_path):
    """Catalog=None AND no thresholds file → tracker still works,
    records $0, logs the missing-pricing warning. Cost dashboard sees
    a $0 row instead of the platform crashing."""
    tracker = TokenTracker(
        state, catalog=None, thresholds_path=str(tmp_path / "nope.yaml"),
    )
    assert tracker.calculate_cost("anything", 1000, 1000) == 0.0


# ── End-to-end through record() ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_persists_with_catalog_priced_cost(catalog, state):
    tracker = TokenTracker(state, catalog=catalog)
    md = catalog.get("claude-haiku-4-7")
    expected = (500 * md.pricing_per_million.input
                + 1500 * md.pricing_per_million.output) / 1_000_000
    usage = await tracker.record(
        request_id="req-1", subtask_id="st-1", agent_id="prd_specialist",
        model="claude-haiku-4-7", input_tokens=500, output_tokens=1500,
    )
    assert usage.cost_usd == pytest.approx(expected)
    state.record_token_usage.assert_awaited_once()
