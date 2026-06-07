"""PAM-06 — BaseAgent kwarg-threading & concurrency contract.

Pinned behavior:
  - process_task() accepts llm_client/model/tool_calling_mode as kwargs
  - Per-call kwargs override instance attributes (self._llm_client, self.model)
  - Two concurrent calls on the SAME agent with DIFFERENT models do NOT
    cross-pollinate (the L24-style race we're fixing).
  - Legacy path (no kwargs, instance attrs set via set_llm_client) still works.
  - Result dict's "model" field reflects the per-call model, not self.model.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.agents.base import BaseAgent


class _ConcreteAgent(BaseAgent):
    """Minimal concretion so we can instantiate BaseAgent."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"text": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        return []


def _make_agent(model: str = "claude-opus-4-7") -> _ConcreteAgent:
    return _ConcreteAgent(
        agent_id="test_agent",
        display_name="Test Agent",
        role="tester",
        team="qa",
        model=model,
        system_prompt="You are a tester.",
        tools=[],
        delegation_targets=[],
    )


class _FakeMessages:
    """Records the model used on each create() call."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    async def create(self, **kwargs: Any) -> Any:
        # Simulate real LLM latency so concurrent calls actually interleave.
        await asyncio.sleep(0.05)
        self.calls.append(kwargs["model"])
        # Build a minimal Anthropic-shaped response with one text block, no tools.
        block = MagicMock()
        block.type = "text"
        block.text = f"reply from {self.label} using {kwargs['model']}"
        usage = MagicMock()
        usage.input_tokens = 10
        usage.output_tokens = 20
        resp = MagicMock()
        resp.content = [block]
        resp.usage = usage
        resp.stop_reason = "end_turn"
        return resp


class _FakeAnthropicClient:
    def __init__(self, label: str) -> None:
        self.label = label
        self.messages = _FakeMessages(label)

    @property
    def calls(self) -> list[str]:
        return self.messages.calls


# ── Kwarg precedence ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kwargs_override_instance_attrs():
    """Per-call kwargs win over self.model / self._llm_client."""
    agent = _make_agent(model="claude-opus-4-7")
    instance_client = _FakeAnthropicClient("instance")
    agent.set_llm_client(instance_client)

    call_client = _FakeAnthropicClient("per_call")
    result = await agent.process_task(
        request_id="r1",
        inputs={"task": "do the thing here please"},
        llm_client=call_client,
        model="claude-haiku-4-7",
        tool_calling_mode="native",
    )

    # Per-call client was used; instance client untouched.
    assert call_client.calls == ["claude-haiku-4-7"]
    assert instance_client.calls == []
    # Result reports the per-call model, not self.model.
    assert result["model"] == "claude-haiku-4-7"


@pytest.mark.asyncio
async def test_legacy_path_still_works():
    """Callers that pre-set _llm_client + self.model and pass NO kwargs
    keep working — backward compat for pre-PAM-07 code paths."""
    agent = _make_agent(model="claude-sonnet-4-7")
    client = _FakeAnthropicClient("legacy")
    agent.set_llm_client(client)

    result = await agent.process_task(request_id="r2", inputs={"x": "hello world here"})
    assert client.calls == ["claude-sonnet-4-7"]
    assert result["model"] == "claude-sonnet-4-7"


@pytest.mark.asyncio
async def test_mock_path_when_no_client_anywhere():
    """No instance client AND no kwarg client → mock result, no crash."""
    agent = _make_agent()
    # Don't call set_llm_client
    result = await agent.process_task(request_id="r3", inputs={})
    assert result["status"] == "completed"
    assert result["llm_calls"] == 0


# ── Concurrency: the actual L24-style fix ───────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_calls_dont_cross_pollinate():
    """Two simultaneous process_task() invocations on the SAME agent
    with DIFFERENT models must each see their own model. With the old
    self.model / self._llm_client pattern, this would race; with kwarg
    threading, each call carries its own values on the stack."""
    agent = _make_agent(model="claude-opus-4-7")  # instance default

    client_a = _FakeAnthropicClient("A")
    client_b = _FakeAnthropicClient("B")

    # Launch both at once. The 0.05s sleep inside FakeMessages.create
    # guarantees they overlap — i.e. while task A is awaiting its
    # response, task B starts (and would, in the old code, clobber
    # whatever self.model A had just set).
    result_a, result_b = await asyncio.gather(
        agent.process_task(
            request_id="rA", inputs={"k": "task A is here"},
            llm_client=client_a, model="claude-haiku-4-7",
        ),
        agent.process_task(
            request_id="rB", inputs={"k": "task B is here"},
            llm_client=client_b, model="claude-sonnet-4-7",
        ),
    )

    # Each client only ever saw its own model — no leakage.
    assert client_a.calls == ["claude-haiku-4-7"]
    assert client_b.calls == ["claude-sonnet-4-7"]
    assert result_a["model"] == "claude-haiku-4-7"
    assert result_b["model"] == "claude-sonnet-4-7"
    # Instance default was never used during either call.
    assert agent.model == "claude-opus-4-7"  # unchanged


@pytest.mark.asyncio
async def test_concurrent_calls_with_different_clients_are_isolated():
    """Stress test: 10 concurrent calls, alternating models. None should
    leak into each other's response."""
    agent = _make_agent()
    models = ["claude-opus-4-7", "claude-sonnet-4-7", "claude-haiku-4-7"]

    async def one_call(i: int) -> dict[str, Any]:
        m = models[i % len(models)]
        client = _FakeAnthropicClient(f"c{i}")
        return await agent.process_task(
            request_id=f"r{i}", inputs={"i": f"index is {i}"},
            llm_client=client, model=m,
        )

    results = await asyncio.gather(*(one_call(i) for i in range(10)))
    for i, r in enumerate(results):
        assert r["model"] == models[i % len(models)]
