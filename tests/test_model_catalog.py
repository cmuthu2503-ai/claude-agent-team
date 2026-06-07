"""PAM-02 / PAM-08 — Model catalog Pydantic validator + lookup tests.

Pinned contracts:
  - models.yaml parses and validates on load (boot-time integrity)
  - Lookup API (get/has/list_all/list_by_tier) round-trips against the
    seeded entries
  - Legacy provider alias resolution maps pre-PAM strings to model ids
  - Three-layer resolve_id falls through agent → yaml → env → default
  - Validators reject obviously-broken catalogs (openai_compat without
    base_url, tier='local' with nonzero pricing, unknown default_model,
    duplicate aliases pointing at missing models)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.models.catalog import (
    ModelCatalog,
    ModelDef,
    ModelPricing,
    default_catalog_path,
)


# ── Load the real config/models.yaml ────────────────────────────────────


@pytest.fixture(scope="module")
def catalog() -> ModelCatalog:
    return ModelCatalog.load(default_catalog_path())


def test_catalog_loads_and_has_default_model(catalog):
    assert isinstance(catalog, ModelCatalog)
    assert catalog.default_model
    assert catalog.has(catalog.default_model)


def test_catalog_contains_all_three_tiers():
    cat = ModelCatalog.load(default_catalog_path())
    for tier in ("frontier", "workhorse", "fast"):
        rows = cat.list_by_tier(tier)
        assert len(rows) >= 1, f"no models in tier {tier!r}"


def test_every_model_has_required_fields(catalog):
    for m in catalog.list_all():
        assert m.id
        assert m.model_id
        assert m.provider_type in {
            "anthropic", "anthropic_aws", "bedrock", "openai", "openai_compat",
        }
        assert m.tool_calling_mode in {"native", "prompted"}
        assert m.tier in {"frontier", "workhorse", "fast", "local"}
        assert m.pricing_per_million.input >= 0
        assert m.pricing_per_million.output >= 0


def test_legacy_aliases_all_resolve_to_known_models(catalog):
    for alias, target in catalog.legacy_provider_aliases.items():
        # If the catalog loaded, every alias target must exist
        if target in catalog.models:
            assert catalog.resolve_legacy_provider(alias) == target


def test_resolve_legacy_provider_handles_direct_model_id(catalog):
    """When old data stored the catalog id directly (not via alias),
    resolve_legacy_provider should still return it."""
    direct_id = catalog.default_model
    assert catalog.resolve_legacy_provider(direct_id) == direct_id


def test_resolve_legacy_provider_returns_none_for_unknown(catalog):
    assert catalog.resolve_legacy_provider("not_a_provider") is None
    assert catalog.resolve_legacy_provider(None) is None
    assert catalog.resolve_legacy_provider("") is None


def test_resolve_id_precedence_chain(catalog):
    """agent_override beats yaml_default beats env beats catalog default."""
    other = next(iter(catalog.models)) if catalog.models else None
    # Layer 1 — agent_override picked
    assert catalog.resolve_id(agent_override=other) == other
    # Layer 2 — yaml_default when agent is None
    assert catalog.resolve_id(yaml_default=other) == other
    # Layer 3 — env_default
    assert catalog.resolve_id(env_default=other) == other
    # Layer 4 — final fallback
    assert catalog.resolve_id() == catalog.default_model
    # Unknown ids at every layer get skipped; falls to default
    assert (
        catalog.resolve_id(
            agent_override="bogus",
            yaml_default="also_bogus",
            env_default="still_bogus",
        )
        == catalog.default_model
    )


def test_resolve_id_accepts_legacy_string_at_yaml_layer(catalog):
    """Old agent YAMLs may carry `model: anthropic_aws` (a legacy
    provider name). The resolver should map it through the alias
    table rather than falling through to default_model."""
    if "anthropic_aws" in catalog.legacy_provider_aliases:
        target = catalog.legacy_provider_aliases["anthropic_aws"]
        assert catalog.resolve_id(yaml_default="anthropic_aws") == target


def test_find_by_vendor_id_returns_first_sorted_match(catalog):
    vendor_id = catalog.get(catalog.default_model).model_id
    found = catalog.find_by_vendor_id(vendor_id)
    assert found is not None
    assert found.model_id == vendor_id


def test_get_raises_keyerror_on_unknown(catalog):
    with pytest.raises(KeyError):
        catalog.get("definitely_not_a_model")


# ── Boot-time validator failures ────────────────────────────────────────


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "models.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_rejects_openai_compat_without_base_url(tmp_path):
    p = _write(tmp_path, """
default_model: x
models:
  x:
    provider_type: openai_compat
    model_id: m
    tool_calling_mode: prompted
    tier: local
    pricing_per_million: {input: 0.0, output: 0.0}
""")
    with pytest.raises(ValueError) as exc:
        ModelCatalog.load(p)
    assert "base_url" in str(exc.value)


def test_load_rejects_local_tier_with_nonzero_pricing(tmp_path):
    p = _write(tmp_path, """
default_model: x
models:
  x:
    provider_type: anthropic_aws
    model_id: m
    tool_calling_mode: native
    tier: local
    pricing_per_million: {input: 1.0, output: 1.0}
""")
    with pytest.raises(ValueError) as exc:
        ModelCatalog.load(p)
    assert "local" in str(exc.value).lower()


def test_load_rejects_unknown_default_model(tmp_path):
    p = _write(tmp_path, """
default_model: nope
models:
  real:
    provider_type: anthropic_aws
    model_id: m
    tool_calling_mode: native
    tier: frontier
    pricing_per_million: {input: 1.0, output: 1.0}
""")
    with pytest.raises(ValueError) as exc:
        ModelCatalog.load(p)
    assert "default_model" in str(exc.value)


def test_load_drops_alias_pointing_at_missing_model(tmp_path):
    """Forward-looking aliases (target not yet shipped, e.g. PAM-23's
    ollama target during PR-1) are SKIPPED, not raised. Surviving
    aliases still resolve."""
    p = _write(tmp_path, """
default_model: real
legacy_provider_aliases:
  old_thing: nonexistent      # dropped — not in catalog yet
  valid_thing: real           # kept
models:
  real:
    provider_type: anthropic_aws
    model_id: m
    tool_calling_mode: native
    tier: frontier
    pricing_per_million: {input: 1.0, output: 1.0}
""")
    cat = ModelCatalog.load(p)
    assert cat.resolve_legacy_provider("old_thing") is None
    assert cat.resolve_legacy_provider("valid_thing") == "real"
    # Orphan dropped from the loaded map entirely
    assert "old_thing" not in cat.legacy_provider_aliases
    assert "valid_thing" in cat.legacy_provider_aliases


def test_load_rejects_negative_pricing(tmp_path):
    p = _write(tmp_path, """
default_model: x
models:
  x:
    provider_type: anthropic_aws
    model_id: m
    tool_calling_mode: native
    tier: frontier
    pricing_per_million: {input: -1.0, output: 1.0}
""")
    with pytest.raises(ValueError):
        ModelCatalog.load(p)


def test_load_rejects_missing_file(tmp_path):
    with pytest.raises(ValueError) as exc:
        ModelCatalog.load(tmp_path / "nope.yaml")
    assert "not found" in str(exc.value)


def test_load_rejects_empty_models_section(tmp_path):
    p = _write(tmp_path, "default_model: x\nmodels: {}\n")
    with pytest.raises(ValueError):
        ModelCatalog.load(p)


def test_display_name_defaults_to_id_when_blank():
    m = ModelDef(
        id="my-model",
        provider_type="anthropic_aws",
        model_id="m",
        tool_calling_mode="native",
        tier="frontier",
        pricing_per_million=ModelPricing(input=1.0, output=1.0),
    )
    assert m.display_name == "my-model"
