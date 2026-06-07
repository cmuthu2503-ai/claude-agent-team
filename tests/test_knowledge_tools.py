"""KB-08 — knowledge_search / knowledge_get tools + scope injection.

Pure unit tests (fake retriever/store) — the live retrieval is already
proven in KB-07. The point here is the TOOL contract and the **scope
injection** property: the agent supplies only query/doc_id, and the
registry threads the executor's kb_scope so the agent can't widen it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.knowledge.tools import (
    KbScope,
    KnowledgeGetTool,
    KnowledgeSearchTool,
    register_knowledge_tools,
)
from src.tools.registry import ToolRegistry

# ── Fakes ────────────────────────────────────────────────────────────────


@dataclass
class _Hit:
    chunk_id: str
    text: str
    score: float
    doc_id: str
    title: str
    uri: str | None = None
    namespace: str = "kb_platform"
    metadata: dict | None = None


class _FakeRetriever:
    """Records the scope it was called with."""

    def __init__(self, hits: list[_Hit] | None = None) -> None:
        self._hits = hits or []
        self.calls: list[dict] = []

    async def retrieve(self, query, namespace, *, bucket_ids=None,  # noqa: ANN001
                        agent_id="", request_id=None, top_k=None):
        self.calls.append({
            "query": query, "namespace": namespace, "bucket_ids": bucket_ids,
            "agent_id": agent_id, "request_id": request_id, "top_k": top_k,
        })
        return self._hits


class _FakeStore:
    def __init__(self, docs: dict[str, dict]) -> None:
        self._docs = docs

    async def get_document_full(self, doc_id):  # noqa: ANN001
        return self._docs.get(doc_id)


# ── knowledge_search ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_formats_hits_with_citations():
    retr = _FakeRetriever([
        _Hit("c1", "The supervisor deploys to staging.", 0.91, "doc-a", "Arch"),
        _Hit("c2", "Quality gate blocks low coverage.", 0.74, "doc-b", "Gates"),
    ])
    tool = KnowledgeSearchTool(retr)
    out = await tool.execute({"query": "deploy"}, kb_scope=KbScope(
        namespace="kb_platform", bucket_ids=["B1"], agent_id="research_specialist",
        request_id="req-1",
    ))
    assert "[KB#c1]" in out and "[KB#c2]" in out
    assert "Arch" in out and "doc-a" in out
    # scope was passed straight through to the retriever
    call = retr.calls[0]
    assert call["bucket_ids"] == ["B1"]
    assert call["agent_id"] == "research_specialist" and call["request_id"] == "req-1"


@pytest.mark.asyncio
async def test_search_no_results_prompts_flagging():
    tool = KnowledgeSearchTool(_FakeRetriever([]))
    out = await tool.execute({"query": "x"}, kb_scope=KbScope(bucket_ids=["B1"]))
    assert "No results" in out and "flag" in out.lower()


@pytest.mark.asyncio
async def test_search_requires_query():
    tool = KnowledgeSearchTool(_FakeRetriever())
    assert "requires" in (await tool.execute({}, kb_scope=KbScope())).lower()


@pytest.mark.asyncio
async def test_search_unavailable_when_no_retriever():
    tool = KnowledgeSearchTool(None)
    assert "unavailable" in (await tool.execute({"query": "x"})).lower()


def test_search_schema_has_no_scope_param():
    """The grounding guarantee at the schema level: the agent CANNOT pass a
    bucket / namespace / scope — only query + top_k."""
    props = KnowledgeSearchTool(None).schema()["input_schema"]["properties"]
    assert set(props) == {"query", "top_k"}
    assert "bucket" not in str(props).lower() and "namespace" not in str(props).lower()


# ── knowledge_get ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_in_scope_approved_doc():
    store = _FakeStore({
        "doc-a": {"doc_id": "doc-a", "namespace": "kb_platform", "title": "Arch",
                  "uri": "architecture.md", "status": "approved", "text": "full text body"},
    })
    out = await KnowledgeGetTool(store).execute(
        {"doc_id": "doc-a"}, kb_scope=KbScope(namespace="kb_platform"))
    assert "Arch" in out and "full text body" in out and "architecture.md" in out


@pytest.mark.asyncio
async def test_get_blocks_cross_namespace():
    """A doc_id from another tenant/namespace is refused — isolation."""
    store = _FakeStore({
        "doc-x": {"doc_id": "doc-x", "namespace": "kb_project_OTHER", "title": "X",
                  "uri": None, "status": "approved", "text": "secret"},
    })
    out = await KnowledgeGetTool(store).execute(
        {"doc_id": "doc-x"}, kb_scope=KbScope(namespace="kb_platform"))
    assert "not in scope" in out and "secret" not in out


@pytest.mark.asyncio
async def test_get_blocks_unapproved():
    store = _FakeStore({
        "doc-p": {"doc_id": "doc-p", "namespace": "kb_platform", "title": "P",
                  "uri": None, "status": "pending", "text": "draft"},
    })
    out = await KnowledgeGetTool(store).execute(
        {"doc_id": "doc-p"}, kb_scope=KbScope(namespace="kb_platform"))
    assert "not approved" in out and "draft" not in out


# ── Registry scope injection (the agent-can't-widen property) ────────────


class _Cfg:
    """Minimal config for ToolRegistry: grants knowledge_search to one agent."""

    def __init__(self) -> None:
        self.tools = {"tools": {
            "knowledge_search": {"description": "kb", "category": "knowledge",
                                 "available_to": ["research_specialist"]},
        }}
        self.agents = {"research_specialist": {"tools": ["knowledge_search"]}}


@pytest.mark.asyncio
async def test_registry_injects_kb_scope_and_agent_cannot_widen():
    retr = _FakeRetriever([_Hit("c1", "text", 0.9, "doc-a", "T")])
    reg = ToolRegistry(_Cfg())
    reg.register_implementation("knowledge_search", KnowledgeSearchTool(retr))

    injected = KbScope(namespace="kb_platform", bucket_ids=["ONLY-THIS"],
                       agent_id="research_specialist", request_id="r9")
    # The agent's params try to smuggle a bucket override — it must be IGNORED.
    await reg.execute(
        "knowledge_search", "research_specialist",
        {"query": "q", "bucket_ids": ["SNEAKY"], "namespace": "kb_other"},
        kb_scope=injected,
    )
    call = retr.calls[0]
    assert call["bucket_ids"] == ["ONLY-THIS"]   # injected scope, not the agent's
    assert call["namespace"] == "kb_platform"


@pytest.mark.asyncio
async def test_registry_permission_enforced():
    from src.tools.registry import ToolPermissionError
    reg = ToolRegistry(_Cfg())
    reg.register_implementation("knowledge_search", KnowledgeSearchTool(_FakeRetriever()))
    with pytest.raises(ToolPermissionError):
        await reg.execute("knowledge_search", "backend_specialist", {"query": "q"})


# ── register helper ──────────────────────────────────────────────────────


def test_register_helper_skips_unavailable_subsystem():
    reg = ToolRegistry(_Cfg())

    class _Sub:
        available = False

    assert register_knowledge_tools(reg, _Sub()) is False
    assert register_knowledge_tools(reg, None) is False


def test_register_helper_registers_when_available():
    reg = ToolRegistry(_Cfg())

    class _Sub:
        available = True
        retriever = _FakeRetriever()
        knowledge_store: Any = _FakeStore({})

    assert register_knowledge_tools(reg, _Sub()) is True
    assert "knowledge_search" in reg._implementations  # noqa: SLF001
    assert "knowledge_get" in reg._implementations  # noqa: SLF001
