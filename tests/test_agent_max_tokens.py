"""Regression test for the per-agent max_tokens default.

Pins the fix that closed T-6144cc94's 4-attempt death streak. Backend /
frontend / code reviewer / tester / devops agents emit multi-file
``### File:`` blocks in a single response; 8192 tokens (~1K LOC) was
truncating those mid-stream. The 32K default engages the streaming
path and gives ~3-5K LOC of headroom — enough for realistic feature
tasks.

Other agents (PRD, business_analyst, research, content) stay at the
8K default since their outputs are short structured documents and
streaming is unnecessary overhead.
"""

from __future__ import annotations

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
        system_prompt="STATIC",
        tools=[],
        delegation_targets=[],
    )


# ── Code-writing agents get the bigger budget ────────────────────────────────


def test_backend_specialist_gets_32k_budget() -> None:
    assert _make_agent("backend_specialist")._default_max_tokens() == 32_000


def test_frontend_specialist_gets_32k_budget() -> None:
    assert _make_agent("frontend_specialist")._default_max_tokens() == 32_000


def test_code_reviewer_gets_32k_budget() -> None:
    assert _make_agent("code_reviewer")._default_max_tokens() == 32_000


def test_tester_specialist_gets_32k_budget() -> None:
    assert _make_agent("tester_specialist")._default_max_tokens() == 32_000


def test_devops_specialist_gets_32k_budget() -> None:
    assert _make_agent("devops_specialist")._default_max_tokens() == 32_000


# ── 32K is above the streaming threshold (so it engages streaming) ───────────


def test_code_agent_budget_exceeds_streaming_threshold() -> None:
    """Critical: 32K must be > _STREAMING_MAX_TOKENS_THRESHOLD so the
    Anthropic SDK uses messages.stream() instead of messages.create().
    The SDK refuses non-streaming for responses that could exceed
    10 minutes, which long emissions can hit."""
    agent = _make_agent("backend_specialist")
    assert agent._default_max_tokens() > agent._STREAMING_MAX_TOKENS_THRESHOLD


# ── Non-code agents stay lean ────────────────────────────────────────────────


def test_business_analyst_stays_at_8k() -> None:
    """PRD output is structured prose — short, no multi-file blocks.
    Keep the lower cap so non-streaming latency stays fast."""
    assert _make_agent("business_analyst")._default_max_tokens() == 8192


def test_business_analyst_stays_at_8k() -> None:
    assert _make_agent("business_analyst")._default_max_tokens() == 8192


def test_research_specialist_stays_at_8k() -> None:
    assert _make_agent("research_specialist")._default_max_tokens() == 8192


def test_unknown_agent_stays_at_default() -> None:
    """Defensive: a future agent_id we haven't classified should NOT
    accidentally get the high budget. The set is opt-in by agent_id."""
    assert _make_agent("some_future_agent")._default_max_tokens() == 8192
