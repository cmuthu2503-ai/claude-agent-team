"""Phase-B unit tests for the three-pass generation helpers (BPD-19).

Tests the pure helpers — prompt builders, JSON-array parser, truncation
detector, normalizers. The endpoint integration (which requires the
full FastAPI lifespan + an LLM client) is exercised by the end-to-end
smoke (BPD-41) in Phase E.

This file pins:
  - JSON-array parser handles fenced + raw + malformed inputs
  - Truncation detector keys off stop_reason='max_tokens'
  - Epic / feature / task normalizers coerce + validate
  - Pass-3 normalizer surfaces over/under-decomposition warnings
  - Prompt builders include the right context (epic name, sibling list,
    previous-list-for-revision when supplied)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.api.routes.projects import (
    _build_epic_generation_prompt,
    _build_feature_generation_prompt,
    _build_task_generation_prompt,
    _check_truncation,
    _extract_json_array,
    _normalize_epic_dicts,
    _normalize_feature_dicts,
    _normalize_task_emission_dicts,
)
from src.models.base import (
    ArtifactStatus, Epic, Feature, ProjectTask, TaskStatus,
)


# ── _extract_json_array ────────────────────────────────────────────────────


def test_extract_json_array_fenced_block() -> None:
    text = '''Here are the epics:

```json
[
  {"title": "Auth", "description": "x", "acceptance_criteria": "y"},
  {"title": "Dashboard", "description": "x", "acceptance_criteria": "y"}
]
```

That's it.'''
    parsed, mode = _extract_json_array(text)
    assert mode == "json"
    assert parsed is not None and len(parsed) == 2
    assert parsed[0]["title"] == "Auth"


def test_extract_json_array_raw_block_no_fence() -> None:
    text = '[{"title": "X", "description": "x", "acceptance_criteria": "y"}]'
    parsed, mode = _extract_json_array(text)
    assert mode == "raw"
    assert parsed is not None and len(parsed) == 1


def test_extract_json_array_prefers_fenced_over_raw() -> None:
    """If both a fenced block AND a raw array appear, fenced wins."""
    text = '''```json
[{"title": "fenced", "description": "", "acceptance_criteria": ""}]
```
[{"title": "raw", "description": "", "acceptance_criteria": ""}]'''
    parsed, mode = _extract_json_array(text)
    assert mode == "json"
    assert parsed is not None and parsed[0]["title"] == "fenced"


def test_extract_json_array_malformed_returns_none() -> None:
    """Fenced block present but JSON is broken → malformed mode."""
    text = "```json\n[{not valid json}]\n```"
    parsed, mode = _extract_json_array(text)
    assert parsed is None
    assert mode == "malformed"


def test_extract_json_array_empty_returns_none() -> None:
    parsed, mode = _extract_json_array("no json here at all, just prose")
    assert parsed is None and mode == "empty"


def test_extract_json_array_drops_non_dict_entries() -> None:
    """Heterogeneous arrays: drop strings/ints, keep dicts."""
    text = '```json\n["a string", {"title": "real"}, 42]\n```'
    parsed, mode = _extract_json_array(text)
    assert mode == "json"
    assert parsed is not None and len(parsed) == 1
    assert parsed[0]["title"] == "real"


# ── _check_truncation ──────────────────────────────────────────────────────


def test_truncation_hint_for_max_tokens_stop() -> None:
    hint = _check_truncation({"stop_reason": "max_tokens", "text": "..."})
    assert hint is not None
    assert "truncated" in hint.lower()


def test_no_truncation_hint_for_end_turn() -> None:
    assert _check_truncation({"stop_reason": "end_turn", "text": "..."}) is None


def test_no_truncation_hint_when_stop_reason_missing() -> None:
    """Defensive: legacy responses that don't carry stop_reason should
    NOT trigger a false truncation warning."""
    assert _check_truncation({"text": "..."}) is None
    assert _check_truncation({"stop_reason": None}) is None


# ── _normalize_epic_dicts ──────────────────────────────────────────────────


def test_normalize_epics_keeps_valid_rows() -> None:
    raw = [
        {"title": "Auth", "description": "y", "acceptance_criteria": "z"},
        {"title": "Dashboard", "description": "y", "acceptance_criteria": "z"},
    ]
    out = _normalize_epic_dicts(raw)
    assert len(out) == 2
    assert out[0]["title"] == "Auth"


def test_normalize_epics_drops_rows_without_title() -> None:
    raw = [
        {"description": "no title"},
        {"title": "Real"},
        {"title": "  "},  # whitespace only
    ]
    out = _normalize_epic_dicts(raw)
    assert len(out) == 1
    assert out[0]["title"] == "Real"


def test_normalize_epics_caps_long_strings() -> None:
    raw = [{
        "title": "x" * 500,
        "description": "y" * 10_000,
        "acceptance_criteria": "z" * 5000,
    }]
    out = _normalize_epic_dicts(raw)
    assert len(out[0]["title"]) == 200
    assert len(out[0]["description"]) == 4000
    assert len(out[0]["acceptance_criteria"]) == 1000


# ── _normalize_feature_dicts ───────────────────────────────────────────────


def test_normalize_features_preserves_depends_on_titles() -> None:
    raw = [{
        "title": "Login",
        "description": "x",
        "acceptance_criteria": "y",
        "depends_on_features": ["Session mgmt", "Token store"],
    }]
    out = _normalize_feature_dicts(raw)
    assert out[0]["depends_on_feature_titles"] == ["Session mgmt", "Token store"]


def test_normalize_features_handles_legacy_depends_on_key() -> None:
    """Accept either 'depends_on_features' or 'depends_on' for robustness."""
    raw = [{
        "title": "X", "description": "", "acceptance_criteria": "",
        "depends_on": ["Y"],
    }]
    out = _normalize_feature_dicts(raw)
    assert out[0]["depends_on_feature_titles"] == ["Y"]


# ── _normalize_task_emission_dicts (atomic-task contract) ──────────────────


def test_normalize_tasks_keeps_valid_atomic_rows() -> None:
    raw = [{
        "title": "Add endpoint",
        "description": "x",
        "primary_file": "backend/app/api/v1/foo.py",
        "expected_loc": 120,
        "acceptance_test": "GET /foo returns 200",
        "depends_on_indices": [0, 1],
        "task_type": "feature_request",
        "priority": "high",
        "estimated_agent": "backend_specialist",
    }]
    out = _normalize_task_emission_dicts(raw)
    assert len(out) == 1
    t = out[0]
    assert t["primary_file"] == "backend/app/api/v1/foo.py"
    assert t["expected_loc"] == 120
    assert t["depends_on_raw"] == [0, 1]
    assert t["_warnings"] == []


def test_normalize_tasks_warns_missing_primary_file() -> None:
    raw = [{"title": "X"}]
    out = _normalize_task_emission_dicts(raw)
    assert "missing primary_file" in out[0]["_warnings"]


def test_normalize_tasks_warns_too_small_loc() -> None:
    raw = [{"title": "X", "primary_file": "x.py", "expected_loc": 10}]
    out = _normalize_task_emission_dicts(raw)
    assert any("suspiciously small" in w for w in out[0]["_warnings"])


def test_normalize_tasks_warns_too_large_loc() -> None:
    raw = [{"title": "X", "primary_file": "x.py", "expected_loc": 800}]
    out = _normalize_task_emission_dicts(raw)
    assert any("suspiciously large" in w for w in out[0]["_warnings"])


def test_normalize_tasks_coerces_invalid_task_type() -> None:
    raw = [{
        "title": "X", "primary_file": "x.py",
        "task_type": "made_up_type", "priority": "URGENT_BAD",
    }]
    out = _normalize_task_emission_dicts(raw)
    assert out[0]["task_type"] == "feature_request"  # coerced default
    assert out[0]["priority"] == "medium"            # coerced default


def test_normalize_tasks_accepts_string_feature_dep() -> None:
    """Cross-feature dep syntax: 'feature:<title>' is kept as-is in the
    raw list; the endpoint resolves it on persist."""
    raw = [{
        "title": "X", "primary_file": "x.py",
        "depends_on_indices": [0, "feature:Login flow"],
    }]
    out = _normalize_task_emission_dicts(raw)
    assert out[0]["depends_on_raw"] == [0, "feature:Login flow"]


# ── Prompt builders ────────────────────────────────────────────────────────


def _make_epic(title: str = "Epic Auth") -> Epic:
    return Epic(
        epic_id="E-001",
        project_id="proj-x",
        list_version=1,
        list_status=ArtifactStatus.DRAFT,
        ordinal=1,
        title=title,
        description="x",
        acceptance_criteria="z",
    )


def _make_feature(title: str = "Login flow") -> Feature:
    return Feature(
        feature_id="F-001",
        epic_id="E-001",
        project_id="proj-x",
        list_version=1,
        list_status=ArtifactStatus.DRAFT,
        ordinal=1,
        title=title,
        description="x",
        acceptance_criteria="y",
    )


def test_epic_prompt_fresh_includes_prd() -> None:
    """Fresh epic-generation prompt embeds PRD content and explains the
    output format."""
    prompt = _build_epic_generation_prompt(
        prd_content="# Test PRD content body",
        api_spec_content="",
        review_comments="",
        previous_epics=None,
    )
    assert "Test PRD content body" in prompt
    assert "fenced ```json``` block" in prompt
    assert "REVISING" not in prompt  # not in revision mode


def test_epic_prompt_revision_includes_previous_and_comments() -> None:
    """Revision mode includes the previous epics + reviewer comments."""
    prev = [_make_epic("OldEpic")]
    prompt = _build_epic_generation_prompt(
        prd_content="...",
        api_spec_content="",
        review_comments="Add a Billing epic",
        previous_epics=prev,
    )
    assert "REVISING" in prompt
    assert "OldEpic" in prompt
    assert "Add a Billing epic" in prompt


def test_feature_prompt_includes_sibling_epic_titles() -> None:
    """Pass-2 prompt must include OTHER epic titles so the agent doesn't
    duplicate work that belongs in a different epic."""
    epic = _make_epic("Authentication")
    prompt = _build_feature_generation_prompt(
        epic=epic,
        sibling_epic_titles=["Authentication", "Dashboard", "Billing"],
        prd_excerpt="",
        review_comments="",
        previous_features=None,
    )
    # Parent epic itself excluded from sibling block; only siblings shown
    assert "Dashboard" in prompt
    assert "Billing" in prompt


def test_task_prompt_includes_sibling_feature_titles() -> None:
    """Pass-3 prompt includes sibling features under same epic so the
    agent doesn't duplicate atomic tasks across features."""
    feature = _make_feature("Login flow")
    epic = _make_epic("Authentication")
    prompt = _build_task_generation_prompt(
        feature=feature,
        epic=epic,
        sibling_feature_titles=["Login flow", "Password reset", "Session mgmt"],
        review_comments="",
        previous_tasks=None,
    )
    assert "Password reset" in prompt
    assert "Session mgmt" in prompt
    # Atomic-task contract surfaced
    assert "primary_file" in prompt
    assert "acceptance_test" in prompt
    assert "depends_on_indices" in prompt
    assert "50-300" in prompt  # LOC guidance


def test_task_prompt_revision_includes_previous_tasks() -> None:
    feature = _make_feature()
    epic = _make_epic()
    prev = [ProjectTask(
        task_id="T-old",
        project_id="proj-x",
        list_version=1,
        list_status=ArtifactStatus.DRAFT,
        ordinal=1,
        title="OldTask",
        task_status=TaskStatus.BACKLOG,
        primary_file="x.py",
        expected_loc=100,
        acceptance_test="x works",
    )]
    prompt = _build_task_generation_prompt(
        feature=feature, epic=epic, sibling_feature_titles=[],
        review_comments="Split T-old into 2",
        previous_tasks=prev,
    )
    assert "REVISING" in prompt
    assert "OldTask" in prompt
    assert "Split T-old into 2" in prompt
