"""KB-22 — wire the remaining reasoning agents to the per-app KB.

Config-level checks (no DB): the right agents have the right retrieval mode +
scope, and every reasoning agent actually has the knowledge tools available
(in its tool list AND permitted) so agentic/hybrid retrieval, citation, and
decision-recording can fire — not just be granted in principle.
"""

from __future__ import annotations

import pytest

from src.config.loader import ConfigLoader
from src.tools.registry import ToolRegistry

_KNOWLEDGE_TOOLS = {"knowledge_search", "knowledge_get", "knowledge_cite", "record_decision"}
_REASONING_AGENTS = [
    "research_specialist", "content_creator",
    "architecture_reviewer", "code_reviewer", "prd_specialist",
]


@pytest.fixture(scope="module")
def cfg():
    c = ConfigLoader()
    c.load_all()
    return c


def test_new_agents_have_expected_retrieval_modes(cfg):
    modes = {a: cfg.agents[a]["retrieval"]["mode"] for a in _REASONING_AGENTS}
    # KB-22 adds these three.
    assert modes["architecture_reviewer"] == "agentic"
    assert modes["code_reviewer"] == "agentic"
    assert modes["prd_specialist"] == "hybrid"
    # KB-09/17 set these.
    assert modes["research_specialist"] == "hybrid"
    assert modes["content_creator"] == "hybrid"


def test_all_reasoning_agents_scope_auto(cfg):
    for a in _REASONING_AGENTS:
        assert cfg.agents[a]["retrieval"]["scope"] == "auto", a


def test_reasoning_agents_can_actually_call_knowledge_tools(cfg):
    """The registry only exposes a tool to an agent if it's in the agent's
    tool list AND permitted (available_to). This is the bug KB-22 fixes — the
    tools were granted but not in the agents' lists, so agentic retrieval was a
    no-op."""
    reg = ToolRegistry(cfg)
    for a in _REASONING_AGENTS:
        names = {s["name"] for s in reg.get_schemas_for_agent(a)}
        missing = _KNOWLEDGE_TOOLS - names
        assert not missing, f"{a} missing knowledge tools: {missing}"


def test_non_reasoning_agent_has_no_knowledge_tools(cfg):
    # A code-writing agent isn't wired for retrieval — its schema must not
    # expose the knowledge tools.
    reg = ToolRegistry(cfg)
    names = {s["name"] for s in reg.get_schemas_for_agent("backend_specialist")}
    assert not (_KNOWLEDGE_TOOLS & names)
