"""PAM-08 — end-to-end concurrency stress test.

Pinned invariant
----------------
Two simultaneous workflow dispatches to the SAME agent with DIFFERENT
``request_provider`` overrides must each see their own model. With the
pre-PAM-06 ``self.model`` mutation pattern, this would race; with the
PAM-06 kwarg threading and PAM-07 per-call resolution, each call carries
its own (client, model, mode) on the stack.

This is the integration-level version of test_base_agent_kwargs.py's
``test_concurrent_calls_dont_cross_pollinate`` — that one exercises
``BaseAgent.process_task`` in isolation; this one exercises the full
path ``executor.execute → resolver.resolve → process_task → client``.
Both matter: the in-process fix could regress on either side independently.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.client_pool import LLMClientPool
from src.agents.executor import AgentSystemExecutor
from src.agents.model_resolver import ModelResolver
from src.models.catalog import ModelCatalog, default_catalog_path


class _RecordingMessages:
    """Records every model used on .create() calls."""

    def __init__(self, label: str, calls: list[tuple[str, str]]) -> None:
        self.label = label
        # Shared list across all clients so test can assert the FULL
        # interleaving — proves the race window was actually exercised.
        self._calls = calls

    async def create(self, **kwargs: Any) -> Any:
        # Latency window so concurrent calls interleave (the race window).
        await asyncio.sleep(0.05)
        self._calls.append((self.label, kwargs["model"]))
        block = MagicMock()
        block.type = "text"
        block.text = f"reply from {self.label} using {kwargs['model']}"
        usage = MagicMock()
        usage.input_tokens = 5
        usage.output_tokens = 7
        resp = MagicMock()
        resp.content = [block]
        resp.usage = usage
        resp.stop_reason = "end_turn"
        return resp


class _RecordingClient:
    def __init__(self, label: str, calls: list[tuple[str, str]]) -> None:
        self.label = label
        self.messages = _RecordingMessages(label, calls)


@pytest.fixture
def catalog() -> ModelCatalog:
    return ModelCatalog.load(default_catalog_path())


def _stub_executor_with_real_resolver(
    catalog: ModelCatalog,
    agent_id_to_default_yaml_model: dict[str, str],
    shared_calls: list[tuple[str, str]],
) -> tuple[AgentSystemExecutor, dict[str, _RecordingClient]]:
    """Build an executor skeleton wired with the REAL ModelResolver +
    a fake LLMClientPool that hands out recording clients keyed by
    catalog id. Bypasses the full AgentSystemExecutor.__init__ (which
    needs AnthropicAWS creds) by constructing the instance manually."""

    # One recording client per distinct catalog id we expect to see.
    clients: dict[str, _RecordingClient] = {}

    class _FakePool:
        def get_for(self, model: Any) -> Any:
            mid = model.id
            if mid not in clients:
                clients[mid] = _RecordingClient(label=mid, calls=shared_calls)
            return clients[mid]

    # Real resolver, fake pool.
    agents_cfg = {
        agent_id: {"model": yaml_model}
        for agent_id, yaml_model in agent_id_to_default_yaml_model.items()
    }
    resolver = ModelResolver(
        catalog=catalog,
        client_pool=_FakePool(),
        agents_config=agents_cfg,
        state_store=None,
    )

    # Construct executor without calling __init__ (avoids AWS creds, tool
    # registration, agent factory, etc.). We just need the surface that
    # execute() / _resolve_model_for_agent() touch.
    ex = AgentSystemExecutor.__new__(AgentSystemExecutor)
    ex.model_resolver = resolver
    ex.model_catalog = catalog
    ex.client_pool = None
    ex.anthropic_client = None
    ex.client = None
    ex.inference_geo = None
    ex._busy_agents = {}
    # KB-09: __init__ is bypassed, so set the attribute execute() reads via
    # _resolve_kb_for_request. None → KB grounding is skipped (no behaviour change).
    ex.kb_subsystem = None

    # Minimal registry that returns a real BaseAgent subclass instance
    # for each agent_id — the agent's process_task does the actual
    # _call_anthropic with the threaded client.
    from src.agents.base import BaseAgent

    class _StressAgent(BaseAgent):
        def _parse_output(self, text: str) -> dict[str, Any]:
            return {"text": text}
        def _extract_artifacts(self, text: str) -> list[str]:
            return []

    agents: dict[str, _StressAgent] = {}
    for agent_id, yaml_model in agent_id_to_default_yaml_model.items():
        agents[agent_id] = _StressAgent(
            agent_id=agent_id, display_name=agent_id, role="x", team="x",
            model=yaml_model, system_prompt="sys", tools=[], delegation_targets=[],
        )

    class _Reg:
        def get(self, k: str) -> Any:
            return agents.get(k)
    ex.registry = _Reg()
    # Skip project_root lookup (no state store in this stub).
    ex._resolve_project_root_for_request = AsyncMock(return_value=None)

    return ex, clients


# ── Tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_concurrent_executes_dont_cross_pollinate(catalog):
    """The PAM-06/07 invariant: two simultaneous execute() calls on the
    SAME agent with DIFFERENT request_provider overrides each carry
    their own model through to the SDK call."""
    shared_calls: list[tuple[str, str]] = []
    ex, _ = _stub_executor_with_real_resolver(
        catalog,
        {"prd_specialist": "claude-opus-4-7"},
        shared_calls,
    )

    # Two requests for the same agent, with different per-request overrides.
    a_task = ex.execute(
        "prd_specialist", "req-A",
        {"provider": "claude-haiku-4-7", "task": "do A"},
    )
    b_task = ex.execute(
        "prd_specialist", "req-B",
        {"provider": "claude-sonnet-4-7", "task": "do B"},
    )

    result_a, result_b = await asyncio.gather(a_task, b_task)

    # Each call carried its own model end-to-end.
    assert result_a["model"] == "claude-haiku-4-7"
    assert result_b["model"] == "claude-sonnet-4-7"

    # The shared call log proves NO cross-pollination: each label
    # (catalog id of the resolved client) only ever paired with its
    # own model. If self.model had been mutated mid-call, we'd see
    # mismatched pairs here.
    for client_label, model_used in shared_calls:
        assert client_label == model_used, (
            f"client {client_label} was used with model {model_used} "
            f"— cross-pollination detected in {shared_calls}"
        )


@pytest.mark.asyncio
async def test_stress_10_concurrent_executes(catalog):
    """Higher-pressure version: 10 simultaneous dispatches alternating
    across 3 models. None must leak into another."""
    shared_calls: list[tuple[str, str]] = []
    ex, _ = _stub_executor_with_real_resolver(
        catalog,
        {"prd_specialist": "claude-opus-4-7"},
        shared_calls,
    )

    models = ["claude-opus-4-7", "claude-sonnet-4-7", "claude-haiku-4-7"]

    async def one(i: int) -> dict[str, Any]:
        m = models[i % len(models)]
        return await ex.execute(
            "prd_specialist", f"req-{i}",
            {"provider": m, "k": f"task {i}"},
        )

    results = await asyncio.gather(*(one(i) for i in range(10)))

    for i, r in enumerate(results):
        assert r["model"] == models[i % len(models)]

    # Same end-to-end invariant on the shared call log.
    for client_label, model_used in shared_calls:
        assert client_label == model_used


@pytest.mark.asyncio
async def test_yaml_default_used_when_no_request_override(catalog):
    """When inputs has no `provider` key, layer 3 (YAML default) wins.
    Each agent's YAML model is honoured even under concurrent dispatch."""
    shared_calls: list[tuple[str, str]] = []
    ex, _ = _stub_executor_with_real_resolver(
        catalog,
        {
            "agent_alpha": "claude-haiku-4-7",
            "agent_beta": "claude-sonnet-4-7",
        },
        shared_calls,
    )

    a_task = ex.execute("agent_alpha", "req-A", {"task": "x"})
    b_task = ex.execute("agent_beta", "req-B", {"task": "y"})

    result_a, result_b = await asyncio.gather(a_task, b_task)
    assert result_a["model"] == "claude-haiku-4-7"
    assert result_b["model"] == "claude-sonnet-4-7"
    for client_label, model_used in shared_calls:
        assert client_label == model_used


@pytest.mark.asyncio
async def test_unknown_request_provider_falls_through_to_yaml(catalog):
    """A garbage request_provider at layer 1 must fall through to the
    YAML default — and that pass-through must hold under concurrency."""
    shared_calls: list[tuple[str, str]] = []
    ex, _ = _stub_executor_with_real_resolver(
        catalog,
        {"prd_specialist": "claude-opus-4-7"},
        shared_calls,
    )

    # Two concurrent calls: one with a typo, one with a valid override.
    bad = ex.execute(
        "prd_specialist", "req-bad",
        {"provider": "claude-not-a-real-model", "task": "x"},
    )
    good = ex.execute(
        "prd_specialist", "req-good",
        {"provider": "claude-haiku-4-7", "task": "y"},
    )
    r_bad, r_good = await asyncio.gather(bad, good)
    # bad → fell through to YAML default
    assert r_bad["model"] == "claude-opus-4-7"
    # good → request override won
    assert r_good["model"] == "claude-haiku-4-7"
    for client_label, model_used in shared_calls:
        assert client_label == model_used
