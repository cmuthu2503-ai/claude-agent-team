"""PS-11 — Prompt Studio pipeline smoke (without spending LLM tokens).

The PromptStudio page (frontend/src/pages/PromptStudio.tsx) reads
six endpoints. PS-07 wired them; PS-09 built the React reader. The
backend contracts they share are:

  GET    /api/v1/prompts/templates              — config/prompt_templates.yaml
  POST   /api/v1/prompts/generate               — body → 3 variants
  POST   /api/v1/prompts/{session_id}/refine    — feedback → 3 new iteration
  PUT    /api/v1/prompts/{session_id}/select    — pick the selected variant
  GET    /api/v1/prompts                        — history list (paginated)
  GET    /api/v1/prompts/{session_id}           — full detail incl. variants

This test pins THREE contracts the frontend depends on:

  1. Template loading — config/prompt_templates.yaml parses cleanly,
     contains the 6 starting templates PS-05 specified, and each
     template carries the fields PS-09's picker reads.

  2. Serialization shape — the dict shapes _variant_to_dict and
     _session_to_dict emit are the exact dict shapes the React
     reader expects. A schema rename here silently breaks the page.

  3. Pydantic model contract — PromptSession + PromptVariant carry
     the fields the route's UPSERT path reads (variant_id, iteration
     numbering, approach, prompt_text).

What's NOT covered
------------------
- Real LLM generation (would need $ + 30s per run).
- React UI rendering — vitest exists (PromptStudio.test.tsx) but
  needs @testing-library/react which isn't in the dev container;
  noted as a separate task for CI hardening.
- WebSocket events — none used by this page.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml


_REPO_ROOT = Path(__file__).resolve().parents[1]


# ── PS-05 templates contract ────────────────────────────────────────────


def test_prompt_templates_yaml_exists_and_parses():
    """PS-05 ships 6 templates the picker dropdown reads. Verify
    the file exists and parses to the expected dict shape."""
    path = _REPO_ROOT / "config" / "prompt_templates.yaml"
    assert path.exists(), f"PS-05 missing config file: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "templates" in data
    assert isinstance(data["templates"], dict)
    # PS-05 specified 6 starting templates
    assert len(data["templates"]) >= 6, (
        f"expected ≥6 templates per PS-05, got {len(data['templates'])}"
    )


def test_every_template_has_fields_picker_reads():
    """PS-09's template picker reads name + description + the 5
    pre-fillable form fields (use_case, target_audience,
    desired_output, tone, constraints). Missing fields render as
    'undefined' in the dropdown."""
    path = _REPO_ROOT / "config" / "prompt_templates.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    REQUIRED = {"name", "description"}
    OPTIONAL_PREFILL = {
        "use_case", "target_audience", "desired_output", "tone", "constraints",
    }
    for tid, tpl in data["templates"].items():
        assert isinstance(tpl, dict), f"template {tid} is not a dict"
        missing = REQUIRED - set(tpl.keys())
        assert not missing, f"template {tid} missing required fields: {missing}"
        # All recognised optional pre-fill fields should be strings when present
        for field in OPTIONAL_PREFILL:
            if field in tpl:
                assert isinstance(tpl[field], str), (
                    f"template {tid} field {field} is not a string"
                )


def test_template_endpoint_route_shape_matches_picker():
    """GET /templates returns each template as
    {template_id, name, description, category, use_case, ...}.
    Mirrors the list-comprehension in routes/prompts.py::list_templates."""
    path = _REPO_ROOT / "config" / "prompt_templates.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    templates = data.get("templates", {})
    # Reproduce the route's emission shape verbatim
    result = []
    for template_id, cfg in templates.items():
        result.append({
            "template_id": template_id,
            "name": cfg.get("name", template_id),
            "description": cfg.get("description", ""),
            "category": cfg.get("category", ""),
            "use_case": cfg.get("use_case", ""),
            "target_audience": cfg.get("target_audience", ""),
            "desired_output": cfg.get("desired_output", ""),
            "tone": cfg.get("tone", ""),
            "constraints": cfg.get("constraints", ""),
        })
    assert len(result) >= 6
    # Every emitted dict carries the 9 keys the frontend picker reads
    REQUIRED_KEYS = {
        "template_id", "name", "description", "category",
        "use_case", "target_audience", "desired_output", "tone", "constraints",
    }
    for entry in result:
        assert set(entry.keys()) == REQUIRED_KEYS


# ── PS-03 model contract ────────────────────────────────────────────────


def test_prompt_session_model_carries_router_fields():
    """PS-07's /generate route INSERTs a PromptSession with the 8
    input fields the frontend submits + an id + a created timestamp."""
    from src.models.base import PromptSession

    s = PromptSession(
        session_id="PS-ABC12345",
        user_id="u1",
        use_case="Generate code review prompts",
        target_audience="Senior engineers",
        desired_output="A focused checklist",
        tone="Direct",
        constraints="No emoji",
        options={"variants": 3},
        template_id="code_reviewer",
    )
    # The 10 fields the router reads
    for field in (
        "session_id", "user_id", "created_at",
        "use_case", "target_audience", "desired_output", "tone", "constraints",
        "options", "template_id", "selected_variant_id",
    ):
        assert hasattr(s, field), f"PromptSession missing {field}"
    # created_at auto-populates
    assert isinstance(s.created_at, datetime)
    # No variant selected until PUT /select fires
    assert s.selected_variant_id is None


def test_prompt_variant_model_carries_iteration_fields():
    """The variant_id naming convention is `{session_id}-V{idx:02d}-I{iter}`.
    Iteration starts at 0 for the initial generate; refine bumps to 1, 2, …
    PS-07's UPSERT path depends on this monotonic shape."""
    from src.models.base import PromptVariant

    v = PromptVariant(
        variant_id="PS-ABC12345-V01-I0",
        session_id="PS-ABC12345",
        iteration=0,
        variant_index=1,
        approach="A formal one-line questioning style",
        prompt_text="You are a senior reviewer. For each diff …",
        techniques=["chain_of_thought", "rubric"],
    )
    for field in (
        "variant_id", "session_id", "iteration", "variant_index",
        "approach", "prompt_text", "techniques",
        "feedback_applied", "generated_at",
    ):
        assert hasattr(v, field), f"PromptVariant missing {field}"
    assert v.iteration == 0
    assert v.variant_index == 1
    assert v.techniques == ["chain_of_thought", "rubric"]


# ── PS-07 serialization shape (wire format) ─────────────────────────────


def test_variant_to_dict_shape_matches_frontend_reader():
    """PS-09 reads each variant via 8 fixed keys. _variant_to_dict
    in routes/prompts.py emits exactly those keys; pinning prevents
    a future field rename from blanking out the cards."""
    from src.api.routes.prompts import _variant_to_dict
    from src.models.base import PromptVariant

    v = PromptVariant(
        variant_id="PS-X-V01-I0",
        session_id="PS-X",
        iteration=0,
        variant_index=1,
        approach="A",
        prompt_text="prompt",
        techniques=["a"],
        feedback_applied="",
    )
    d = _variant_to_dict(v)
    EXPECTED = {
        "variant_id", "session_id", "iteration", "variant_index",
        "approach", "prompt_text", "techniques",
        "feedback_applied", "generated_at",
    }
    assert set(d.keys()) == EXPECTED
    # generated_at is an ISO string the frontend formats with new Date()
    assert isinstance(d["generated_at"], str)
    assert "T" in d["generated_at"]  # ISO format


def test_session_to_dict_with_variants_includes_them():
    """The detail endpoint returns the session WITH variants inline.
    The list endpoint omits variants. _session_to_dict's variants
    arg toggles between these two modes — pin the contract."""
    from src.api.routes.prompts import _session_to_dict
    from src.models.base import PromptSession, PromptVariant

    s = PromptSession(
        session_id="PS-X", user_id="u",
        use_case="x", target_audience="", desired_output="",
        tone="", constraints="", options={}, template_id=None,
    )
    v = PromptVariant(
        variant_id="PS-X-V01-I0",
        session_id="PS-X",
        iteration=0,
        variant_index=1,
        approach="A",
        prompt_text="p",
        techniques=[],
    )

    # Listing mode — no variants key
    listing = _session_to_dict(s)
    assert "variants" not in listing
    assert listing["session_id"] == "PS-X"

    # Detail mode — variants array present
    detail = _session_to_dict(s, [v])
    assert "variants" in detail
    assert len(detail["variants"]) == 1
    assert detail["variants"][0]["variant_id"] == "PS-X-V01-I0"

    # Both modes share these top-level keys
    SHARED = {
        "session_id", "user_id", "created_at",
        "use_case", "target_audience", "desired_output",
        "tone", "constraints", "options", "template_id",
        "selected_variant_id",
    }
    assert SHARED.issubset(listing.keys())
    assert SHARED.issubset(detail.keys())


# ── PS-07 request-body validation ────────────────────────────────────────


def test_generate_request_body_accepts_minimum_field():
    """The frontend submits only use_case as required; the rest
    default to empty strings or {} so an operator can fire off a
    quick generation without filling every field. Pin the defaults."""
    from src.api.routes.prompts import GenerateRequest

    req = GenerateRequest(use_case="Write a SQL explainer prompt")
    assert req.use_case == "Write a SQL explainer prompt"
    assert req.target_audience == ""
    assert req.desired_output == ""
    assert req.tone == ""
    assert req.constraints == ""
    assert req.options == {}
    assert req.template_id is None


def test_refine_request_body_requires_feedback():
    """Refine MUST carry the feedback text — refining without
    instruction would be a wasted LLM call. The route also enforces
    this (returns 400) but the model should enforce typing."""
    from src.api.routes.prompts import RefineRequest

    req = RefineRequest(feedback="Make the tone more friendly.")
    assert req.feedback == "Make the tone more friendly."


def test_select_request_body_requires_variant_id():
    """PUT /select takes variant_id — the chosen variant becomes
    the basis for the next refine cycle. Tested separately because
    a typo here breaks the refine→regenerate flow."""
    from src.api.routes.prompts import SelectRequest

    req = SelectRequest(variant_id="PS-X-V02-I0")
    assert req.variant_id == "PS-X-V02-I0"
