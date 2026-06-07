"""PAM-07 — Executor uses ModelResolver + threads kwargs to process_task.

Pinned contracts:
  - executor builds catalog + pool + resolver at boot (soft-fails if catalog
    is missing — legacy path still works)
  - execute() calls resolver.resolve(), threads (client, model, mode) into
    process_task as kwargs
  - inputs["provider"] feeds layer 1 of the resolver chain
  - single_agent_call() also resolves per-call
  - The legacy fallback path (no resolver) keeps using the AnthropicAWS
    client + agent.model — back-compat for environments where
    config/models.yaml fails to load
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeAgent:
    """Stand-in for a BaseAgent instance."""

    def __init__(self, agent_id: str = "backend_specialist", model: str = "claude-opus-4-7") -> None:
        self.agent_id = agent_id
        self.model = model
        # Records what process_task was called with.
        self.last_kwargs: dict[str, Any] = {}

    async def process_task(self, request_id: str, inputs: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.last_kwargs = kwargs
        return {
            "status": "completed",
            "text": "ok",
            "outputs": {},
            "artifacts": [],
            "llm_calls": 1,
            "tool_calls": 0,
            "input_tokens": 1, "output_tokens": 1,
            "model": kwargs.get("model") or self.model,
        }

    async def single_call(self, prompt: str, max_tokens: int | None = None, **kwargs: Any) -> dict[str, Any]:
        self.last_kwargs = kwargs
        return {
            "text": "ok",
            "input_tokens": 1, "output_tokens": 1,
            "model": kwargs.get("model") or self.model,
        }


class _StubExecutor:
    """Tests `_resolve_model_for_agent` and `execute` in isolation
    without paying for the full executor init (which loads YAML, builds
    SDK clients, registers tools, etc.)."""

    def __init__(self, resolver: Any, anthropic_client: Any, agents: dict[str, _FakeAgent]) -> None:
        # Mimic the AgentSystemExecutor surface the methods read from.
        self.model_resolver = resolver
        self.anthropic_client = anthropic_client
        self.client = anthropic_client
        self.inference_geo = None
        self._busy_agents: dict[str, Any] = {}
        self._agents = agents
        # KB-09: execute() calls _resolve_kb_for_request, which reads
        # self.kb_subsystem. None → KB grounding skipped (no behaviour change).
        self.kb_subsystem = None

        # Bind the real methods off AgentSystemExecutor so we test the
        # actual code, not a copy of it.
        from src.agents.executor import AgentSystemExecutor
        self._resolve_model_for_agent = AgentSystemExecutor._resolve_model_for_agent.__get__(self)
        self._resolve_kb_for_request = AgentSystemExecutor._resolve_kb_for_request.__get__(self)
        self.execute = AgentSystemExecutor.execute.__get__(self)
        self._resolve_project_root_for_request = AsyncMock(return_value=None)
        # KB-20: execute() calls _auto_record_decision after process_task.
        # Stub it out — provenance recording is not under test here.
        self._auto_record_decision = AsyncMock(return_value=None)

        # Provide a registry-shaped lookup.
        class _Reg:
            def __init__(self, agents: dict[str, _FakeAgent]) -> None:
                self._a = agents
            def get(self, k: str) -> Any:
                return self._a.get(k)
        self.registry = _Reg(agents)


# ── Tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_threads_resolved_kwargs_to_process_task():
    """resolver.resolve() runs first; its (client, vendor_model_id,
    tool_calling_mode) flow into process_task as keyword arguments."""
    resolved = MagicMock()
    resolved.client = "RESOLVED_CLIENT"
    resolved.vendor_model_id = "claude-haiku-4-7"
    resolved.tool_calling_mode = "native"
    resolved.resolution_source = "agent_yaml"

    resolver = MagicMock()
    resolver.resolve = AsyncMock(return_value=resolved)

    agent = _FakeAgent("backend_specialist", model="claude-opus-4-7")
    executor = _StubExecutor(resolver, anthropic_client="DEFAULT", agents={"backend_specialist": agent})

    await executor.execute("backend_specialist", "req-1", {"task": "x"})

    # Resolver was consulted, NOT the legacy fallback.
    resolver.resolve.assert_awaited_once_with(
        agent_id="backend_specialist", request_provider=None,
    )
    # Threaded values landed on process_task.
    assert agent.last_kwargs["llm_client"] == "RESOLVED_CLIENT"
    assert agent.last_kwargs["model"] == "claude-haiku-4-7"
    assert agent.last_kwargs["tool_calling_mode"] == "native"


@pytest.mark.asyncio
async def test_request_provider_in_inputs_feeds_layer1():
    """A `provider` key in inputs becomes the resolver's
    request_provider — layer 1 of the 5-layer chain."""
    resolved = MagicMock()
    resolved.client = "C"
    resolved.vendor_model_id = "claude-sonnet-4-7"
    resolved.tool_calling_mode = "native"
    resolved.resolution_source = "request_override"

    resolver = MagicMock()
    resolver.resolve = AsyncMock(return_value=resolved)

    agent = _FakeAgent("x")
    executor = _StubExecutor(resolver, "DEFAULT", {"x": agent})

    await executor.execute("x", "req-1", {"provider": "claude-sonnet-4-7", "task": "y"})

    resolver.resolve.assert_awaited_once_with(
        agent_id="x", request_provider="claude-sonnet-4-7",
    )
    assert agent.last_kwargs["model"] == "claude-sonnet-4-7"


@pytest.mark.asyncio
async def test_resolver_failure_falls_back_to_legacy_client():
    """If resolver.resolve() raises, we MUST NOT block dispatch — fall
    through to the AnthropicAWS client + the agent's YAML model."""
    resolver = MagicMock()
    resolver.resolve = AsyncMock(side_effect=RuntimeError("catalog blew up"))

    agent = _FakeAgent("backend_specialist", model="claude-opus-4-7")
    executor = _StubExecutor(resolver, "ANTHROPIC_AWS_CLIENT", {"backend_specialist": agent})

    result = await executor.execute("backend_specialist", "req-2", {})

    assert result["status"] == "completed"
    # Legacy fallback wiring: anthropic_client + agent.model.
    assert agent.last_kwargs["llm_client"] == "ANTHROPIC_AWS_CLIENT"
    assert agent.last_kwargs["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_no_resolver_uses_legacy_path():
    """When model_resolver is None (catalog load failed at boot), the
    executor uses the legacy single-client path."""
    agent = _FakeAgent("backend_specialist", model="claude-opus-4-7")
    executor = _StubExecutor(resolver=None, anthropic_client="LEGACY", agents={"backend_specialist": agent})

    await executor.execute("backend_specialist", "req-3", {})

    assert agent.last_kwargs["llm_client"] == "LEGACY"
    assert agent.last_kwargs["model"] == "claude-opus-4-7"
    assert agent.last_kwargs["tool_calling_mode"] == "native"


@pytest.mark.asyncio
async def test_unknown_agent_returns_failed_without_calling_resolver():
    """Agent not in registry — short-circuits before any resolution."""
    resolver = MagicMock()
    resolver.resolve = AsyncMock()
    executor = _StubExecutor(resolver, "C", agents={})  # empty registry

    result = await executor.execute("ghost_agent", "req-4", {})
    assert result["status"] == "failed"
    assert "not found" in result["error"]
    resolver.resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_client_resolved_falls_to_mock_branch():
    """resolver returns (None, ...) → process_task called with
    llm_client=None (BaseAgent's mock path)."""
    resolved = MagicMock()
    resolved.client = None
    resolved.vendor_model_id = "claude-opus-4-7"
    resolved.tool_calling_mode = "native"
    resolved.resolution_source = "catalog_default"

    resolver = MagicMock()
    resolver.resolve = AsyncMock(return_value=resolved)

    agent = _FakeAgent("x")
    executor = _StubExecutor(resolver, anthropic_client=None, agents={"x": agent})

    result = await executor.execute("x", "req-5", {})
    assert result["status"] == "completed"
    assert agent.last_kwargs["llm_client"] is None
