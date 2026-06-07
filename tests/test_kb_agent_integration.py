"""KB-09 — agent integration: forced/hybrid pre-injection + kb_scope threading.

Pure unit tests on BaseAgent (concrete subclass + fakes). The point:
  - When a retriever is wired + retrieval mode pre-injects, the system prompt
    is grounded in ranked retrieval (NOT the wholesale lessons dump).
  - When NO retriever (KB down — the current dev state), behaviour is
    byte-for-byte unchanged: wholesale lessons fallback, no retrieval.
  - kb_scope is threaded to tools via _execute_tool (agent can't widen).
  - Request carries bucket_ids.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base import BaseAgent
from src.knowledge.tools import KbScope
from src.models.base import Request


class _ConcreteAgent(BaseAgent):
    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"text": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        return []


def _agent(agent_id: str = "backend_specialist") -> _ConcreteAgent:
    return _ConcreteAgent(
        agent_id=agent_id, display_name="A", role="r", team="t",
        model="claude-opus-4-7", system_prompt="SYSTEM PROMPT BODY",
        tools=[], delegation_targets=[],
    )


class _Hit:
    def __init__(self, chunk_id, text, title="Doc", doc_id="doc-1"):  # noqa: ANN001
        self.chunk_id = chunk_id
        self.text = text
        self.title = title
        self.doc_id = doc_id


class _FakeRetriever:
    def __init__(self, hits):  # noqa: ANN001
        self._hits = hits
        self.calls: list[dict] = []

    async def retrieve(self, query, namespace, *, bucket_ids=None,  # noqa: ANN001
                       agent_id="", request_id=None, top_k=None):
        self.calls.append({"query": query, "namespace": namespace,
                           "bucket_ids": bucket_ids, "top_k": top_k})
        return self._hits


# ── Request.bucket_ids ────────────────────────────────────────────────────


def test_request_has_bucket_ids():
    r = Request(request_id="r1", description="do a thing")
    assert r.bucket_ids == []
    r2 = Request(request_id="r2", description="x", bucket_ids=["B1", "B2"])
    assert r2.bucket_ids == ["B1", "B2"]


# ── Forced pre-injection ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forced_injection_grounds_system_prompt():
    agent = _agent()
    retr = _FakeRetriever([
        _Hit("c1", "Supervisor runs on the host, not in Docker.", "CLAUDE"),
        _Hit("c2", "Restart containers after editing src/.", "CLAUDE"),
    ])
    scope = KbScope(namespace="kb_platform", bucket_ids=[], agent_id="backend_specialist",
                    request_id="r1")
    await agent._inject_forced_grounding(
        {"description": "add an endpoint"}, retr, scope, {"forced_top_k": 5},
    )
    prompt = agent._build_system_prompt()
    # ranked retrieval block present, with citations
    assert "RELEVANT KNOWLEDGE" in prompt
    assert "[KB#c1]" in prompt and "Supervisor runs on the host" in prompt
    # query came from the description
    assert retr.calls[0]["query"] == "add an endpoint"
    assert retr.calls[0]["top_k"] == 5


@pytest.mark.asyncio
async def test_dual_source_facts_and_craft_sections():
    """KB-17 — a project-scoped agent retrieves FACTS from its project
    namespace (citeable) AND CRAFT from the platform namespace (guidance-only,
    not citeable). The grounding block carries both, distinctly labelled."""

    class _NsRetriever:
        def __init__(self):
            self.namespaces: list[str] = []

        async def retrieve(self, query, namespace, *, bucket_ids=None,  # noqa: ANN001
                           agent_id="", request_id=None, top_k=None):
            self.namespaces.append(namespace)
            if namespace == "kb_project_appA":
                return [_Hit("f1", "App A charges a 2% transaction fee.", "PRD")]
            return [_Hit("c9", "Lead with the recommendation; tables over prose.", "craft")]

    agent = _agent("content_creator")
    retr = _NsRetriever()
    scope = KbScope(
        namespace="kb_project_appA", craft_namespace="kb_platform",
        bucket_ids=[], agent_id="content_creator", request_id="r1",
    )
    await agent._inject_forced_grounding(
        {"description": "write a launch one-pager"}, retr, scope,
        {"forced_top_k": 5, "craft_top_k": 3},
    )
    prompt = agent._build_system_prompt()
    # FACTS section — citeable, from the app namespace.
    assert "RELEVANT KNOWLEDGE" in prompt
    assert "[KB#f1]" in prompt and "2% transaction fee" in prompt
    # CRAFT section — guidance only, NOT a [KB#] citation.
    assert "PLATFORM CRAFT" in prompt
    assert "tables over prose" in prompt
    assert "[KB#c9]" not in prompt  # craft is never citeable
    # Both namespaces were queried.
    assert retr.namespaces == ["kb_project_appA", "kb_platform"]


@pytest.mark.asyncio
async def test_cold_start_sparse_banner_for_empty_project_kb():
    """KB-19 — a project task whose app KB has no grounded facts gets an
    explicit SPARSE banner (lean on PRD/brief, flag ungrounded claims) instead
    of silently falling back to platform lessons."""
    agent = _agent("research_specialist")
    empty = _FakeRetriever([])  # project namespace returns nothing
    scope = KbScope(
        namespace="kb_project_appA", is_project=True,
        agent_id="research_specialist", request_id="r1",
    )
    await agent._inject_forced_grounding(
        {"description": "summarize the app's pricing"}, empty, scope,
        {"forced_top_k": 5},
    )
    prompt = agent._build_system_prompt()
    assert "APP KNOWLEDGE SPARSE" in prompt
    assert "do NOT invent app-specific facts" in prompt
    # Did NOT fall back to wholesale lessons.
    assert "RELEVANT KNOWLEDGE" not in prompt


@pytest.mark.asyncio
async def test_no_retriever_falls_back_to_wholesale_lessons():
    """KB down (no grounding stashed) → _build_system_prompt uses the
    wholesale lessons path, unchanged. Code agents still get lessons."""
    agent = _agent("backend_specialist")
    # no _inject_forced_grounding call → _kb_grounding stays ""
    agent._kb_grounding = ""
    prompt = agent._build_system_prompt()
    # falls back to wholesale lessons (or empty if the doc is absent) — never
    # the KB grounding block
    assert "RELEVANT KNOWLEDGE" not in prompt
    assert "SYSTEM PROMPT BODY" in prompt


@pytest.mark.asyncio
async def test_forced_injection_soft_fails_on_retriever_error():
    agent = _agent()

    class _Boom:
        async def retrieve(self, *a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("retriever down")

    scope = KbScope(agent_id="backend_specialist")
    await agent._inject_forced_grounding({"description": "x"}, _Boom(), scope, {})
    assert agent._kb_grounding == ""  # swallowed → fallback


# ── kb_scope threading to tools ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_forwards_kb_scope():
    agent = _agent()
    reg = MagicMock()
    reg.execute = AsyncMock(return_value="ok")
    agent.set_tool_registry(reg)
    scope = KbScope(namespace="kb_platform", bucket_ids=["B1"], agent_id="backend_specialist")
    agent._current_kb_scope = scope
    agent._current_project_root = None
    await agent._execute_tool("knowledge_search", {"query": "q"})
    _, kwargs = reg.execute.call_args
    assert kwargs["kb_scope"] is scope          # threaded
    assert kwargs["agent_id"] == "backend_specialist"


# ── query extraction ─────────────────────────────────────────────────────


def test_kb_query_from_inputs_prefers_description():
    assert BaseAgent._kb_query_from_inputs({"description": "build login"}) == "build login"
    assert BaseAgent._kb_query_from_inputs(
        {"x": "a really long sentence used as the fallback query here"}
    ).startswith("a really long sentence")
    assert BaseAgent._kb_query_from_inputs({"x": "short"}) == ""
