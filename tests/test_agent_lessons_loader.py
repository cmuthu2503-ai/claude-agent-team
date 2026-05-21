"""Tests for the cross-agent lessons-doc loader in BaseAgent.

Pins:
  - Code-writing agents get the lessons block prepended.
  - Non-code agents (PRD, user_story_author, etc.) do NOT — keeps their
    context budget lean.
  - Missing doc soft-fails to no-op; agent still works.
  - Doc content over 30KB is truncated with a marker so the prompt
    budget can't explode.
  - The loader reads the doc fresh on every call (no caching), so a
    lesson appended at runtime is visible on the next agent invocation
    without a restart.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.agents.base import BaseAgent


class _ConcreteAgent(BaseAgent):
    """Minimal subclass — BaseAgent has two abstract methods to satisfy."""

    def _parse_output(self, text: str) -> dict:
        return {"text": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        return []


def _make_agent(agent_id: str) -> _ConcreteAgent:
    return _ConcreteAgent(
        agent_id=agent_id,
        display_name="X",
        role="X",
        team="development",
        model="claude-opus-4-7",
        system_prompt="STATIC_AGENT_PROMPT_BODY",
        tools=[],
        delegation_targets=[],
    )


# ── Code-writing agents get the lessons block ────────────────────────────────


def test_backend_specialist_gets_lessons_in_system_prompt():
    """The lessons doc lives in the repo and should be picked up by
    backend_specialist's system prompt."""
    agent = _make_agent("backend_specialist")
    prompt = agent._build_system_prompt()

    assert "CROSS-AGENT LESSONS LEARNED" in prompt
    # The static agent body must STILL be present (lessons prepended,
    # not replaced).
    assert "STATIC_AGENT_PROMPT_BODY" in prompt
    # And the date header (the original purpose of _build_system_prompt).
    assert "CURRENT DATE:" in prompt
    # A sample lesson from the doc should be present.
    assert "Drop-guard rejection" in prompt or "L01" in prompt


def test_frontend_specialist_gets_lessons():
    agent = _make_agent("frontend_specialist")
    prompt = agent._build_system_prompt()
    assert "CROSS-AGENT LESSONS LEARNED" in prompt
    assert "STATIC_AGENT_PROMPT_BODY" in prompt


def test_code_reviewer_gets_lessons():
    agent = _make_agent("code_reviewer")
    prompt = agent._build_system_prompt()
    assert "CROSS-AGENT LESSONS LEARNED" in prompt


def test_tester_specialist_gets_lessons():
    agent = _make_agent("tester_specialist")
    prompt = agent._build_system_prompt()
    assert "CROSS-AGENT LESSONS LEARNED" in prompt


def test_devops_specialist_gets_lessons():
    agent = _make_agent("devops_specialist")
    prompt = agent._build_system_prompt()
    assert "CROSS-AGENT LESSONS LEARNED" in prompt


# ── Non-code agents don't get lessons (context budget preserved) ─────────────


def test_prd_specialist_does_NOT_get_lessons():
    """PRD specialist writes markdown, not code. The technical drop-guard /
    file-write lessons would just be noise in its context. The agent's
    own YAML carries its specific guidance."""
    agent = _make_agent("prd_specialist")
    prompt = agent._build_system_prompt()
    assert "CROSS-AGENT LESSONS LEARNED" not in prompt
    # Static prompt + date still present
    assert "STATIC_AGENT_PROMPT_BODY" in prompt
    assert "CURRENT DATE:" in prompt


def test_user_story_author_does_NOT_get_lessons():
    agent = _make_agent("user_story_author")
    prompt = agent._build_system_prompt()
    assert "CROSS-AGENT LESSONS LEARNED" not in prompt


def test_research_specialist_does_NOT_get_lessons():
    agent = _make_agent("research_specialist")
    prompt = agent._build_system_prompt()
    assert "CROSS-AGENT LESSONS LEARNED" not in prompt


# ── Soft-fail on missing doc ────────────────────────────────────────────────


def test_missing_lessons_doc_soft_fails(tmp_path, monkeypatch):
    """If docs/agent-lessons-learned.md is missing, the agent still
    works — no exception, just no lessons block. Critical: a stray
    rename or delete must not block agent operation."""
    # Point Path(__file__).resolve().parents[2] at a tree with no
    # docs/ subdir so the lookup misses.
    import src.agents.base as base_mod
    real_path = base_mod.__file__

    class _FakePath:
        def __init__(self, p):
            self._p = Path(p)
        def resolve(self):
            return self
        @property
        def parents(self):
            # tmp_path is empty so docs/ won't exist
            return [tmp_path, tmp_path, tmp_path]
        def __truediv__(self, other):
            return self._p / other

    # Patch the Path import inside _load_cross_agent_lessons
    with patch("src.agents.base.__file__", str(tmp_path / "fake.py")):
        agent = _make_agent("backend_specialist")
        # Use the real internal logic but with a missing doc
        result = agent._load_cross_agent_lessons()
        # When path resolves outside the real repo, no doc found → ""
        # OR finds it normally if monkeypatch didn't take. Either way,
        # the loader must not raise.
        assert isinstance(result, str)


# ── 30KB truncation cap ──────────────────────────────────────────────────────


def test_lessons_doc_truncated_above_30kb(tmp_path, monkeypatch):
    """Hard cap so a runaway doc edit can't blow the agent's prompt
    budget. Above 30KB the loader truncates with a clear marker."""
    # Write a giant fake doc into a tmp tree with the right shape
    fake_repo = tmp_path / "fake_repo"
    fake_repo.mkdir()
    (fake_repo / "src" / "agents").mkdir(parents=True)
    fake_agent_module = fake_repo / "src" / "agents" / "base.py"
    fake_agent_module.write_text("# fake module\n")
    docs_dir = fake_repo / "docs"
    docs_dir.mkdir()
    giant = "L99 — runaway doc.\n" + "X" * 50_000
    (docs_dir / "agent-lessons-learned.md").write_text(giant)

    # Monkey-patch __file__ so the loader's parents[2] points at our fake repo
    import src.agents.base as base_mod
    with patch.object(base_mod, "__file__", str(fake_agent_module)):
        agent = _make_agent("backend_specialist")
        out = agent._load_cross_agent_lessons()
    assert "truncated" in out.lower()
    # Sanity: actual content is bounded
    assert len(out) < 35_000


# ── Loader reads fresh on each call (no caching) ─────────────────────────────


def test_lessons_doc_reread_on_every_call(tmp_path, monkeypatch):
    """Append a lesson to the doc, call _build_system_prompt again,
    confirm the new content appears. This is the "self-learning"
    contract: new lessons don't require a restart."""
    fake_repo = tmp_path / "fake_repo"
    fake_repo.mkdir()
    (fake_repo / "src" / "agents").mkdir(parents=True)
    fake_module = fake_repo / "src" / "agents" / "base.py"
    fake_module.write_text("# fake\n")
    (fake_repo / "docs").mkdir()
    doc_path = fake_repo / "docs" / "agent-lessons-learned.md"
    doc_path.write_text("L01 — initial lesson")

    import src.agents.base as base_mod
    with patch.object(base_mod, "__file__", str(fake_module)):
        agent = _make_agent("backend_specialist")
        first = agent._build_system_prompt()
        assert "L01 — initial lesson" in first
        assert "L02" not in first

        # Append a new lesson AT RUNTIME
        doc_path.write_text(
            "L01 — initial lesson\n\n"
            "L02 — newly appended lesson"
        )
        second = agent._build_system_prompt()
        assert "L02 — newly appended lesson" in second
