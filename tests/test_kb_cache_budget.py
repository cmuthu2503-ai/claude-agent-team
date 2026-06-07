"""KB-32 — query cache + per-Request retrieval budget.

Pure unit tests (no live Postgres): a fake embedder/stores prove the Retriever
caches identical queries (skipping the embed + store round-trips), and the
KnowledgeSearchTool enforces ``max_searches`` per (request, agent).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.knowledge.retrieval import Retriever
from src.knowledge.tools import KbScope, KnowledgeSearchTool


class _Hit:
    def __init__(self, cid: str) -> None:
        self.chunk_id = cid


class _Embedder:
    def __init__(self) -> None:
        self.embed_calls = 0

    @property
    def dimensions(self) -> int:
        return 384

    @property
    def model(self) -> str:
        return "fake"

    async def embed_query(self, text: str) -> list[float]:
        self.embed_calls += 1
        return [0.1] * 384

    async def embed_documents(self, texts):  # type: ignore[no-untyped-def]
        return None

    async def rerank(self, query, documents, top_k=None):  # type: ignore[no-untyped-def]
        return []


class _VStore:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, ns, qvec, n, bucket_ids=None):  # noqa: ANN001
        self.calls += 1
        return [_Hit("c1"), _Hit("c2")]


class _KStore:
    async def search(self, ns, query, n, bucket_ids=None):  # noqa: ANN001
        return [_Hit("c1")]


class _KnowledgeStore:
    async def get_chunks_by_ids(self, ids):  # noqa: ANN001
        return {
            c: {"chunk_id": c, "text": f"text {c}", "doc_id": "d", "title": "T",
                "uri": None, "namespace": "kb_platform", "metadata": {}}
            for c in ids
        }

    async def get_feedback_boosts(self, ids, **_):  # noqa: ANN001
        return {}

    async def record_retrieval(self, **_):  # noqa: ANN001
        return "aud"


# ── query cache ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_identical_query_is_cached():
    emb, vec = _Embedder(), _VStore()
    retr = Retriever(emb, vec, _KStore(), _KnowledgeStore(), top_k=5, candidates=10, rerank=False)
    r1 = await retr.retrieve("how to deploy", "kb_platform", agent_id="a")
    # whitespace/case-insensitive key → same cache entry
    r2 = await retr.retrieve("how to   DEPLOY", "kb_platform", agent_id="a")
    assert [c.chunk_id for c in r1] == [c.chunk_id for c in r2]
    # second call served from cache → embedder + vector store hit only once
    assert emb.embed_calls == 1
    assert vec.calls == 1


@pytest.mark.asyncio
async def test_different_scope_is_not_cached():
    emb, vec = _Embedder(), _VStore()
    retr = Retriever(emb, vec, _KStore(), _KnowledgeStore(), top_k=5, candidates=10, rerank=False)
    await retr.retrieve("q", "kb_platform", agent_id="a")
    await retr.retrieve("q", "kb_project_X", agent_id="a")   # different namespace
    assert emb.embed_calls == 2  # distinct cache keys → both computed


# ── per-Request budget ───────────────────────────────────────────────────────


class _FakeRetriever:
    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(self, *a: Any, **k: Any):
        self.calls += 1
        return []


@pytest.mark.asyncio
async def test_max_searches_budget_enforced():
    fr = _FakeRetriever()
    tool = KnowledgeSearchTool(fr)
    scope = KbScope(
        namespace="kb_platform", agent_id="research", request_id="REQ-1", max_searches=2)
    # first two searches go through
    await tool.execute({"query": "a"}, kb_scope=scope)
    await tool.execute({"query": "b"}, kb_scope=scope)
    assert fr.calls == 2
    # third is refused with a budget message; the retriever is NOT hit
    out = await tool.execute({"query": "c"}, kb_scope=scope)
    assert "budget reached" in out.lower()
    assert fr.calls == 2


@pytest.mark.asyncio
async def test_budget_is_per_request_and_agent():
    fr = _FakeRetriever()
    tool = KnowledgeSearchTool(fr)
    s1 = KbScope(namespace="kb_platform", agent_id="research", request_id="REQ-1", max_searches=1)
    s2 = KbScope(namespace="kb_platform", agent_id="research", request_id="REQ-2", max_searches=1)
    await tool.execute({"query": "a"}, kb_scope=s1)
    out1 = await tool.execute({"query": "b"}, kb_scope=s1)  # REQ-1 exhausted
    assert "budget reached" in out1.lower()
    # a different Request has its own fresh budget
    await tool.execute({"query": "a"}, kb_scope=s2)
    assert fr.calls == 2


@pytest.mark.asyncio
async def test_no_budget_when_unset():
    fr = _FakeRetriever()
    tool = KnowledgeSearchTool(fr)
    # max_searches left None → unlimited
    scope = KbScope(namespace="kb_platform", agent_id="research", request_id="REQ-1")
    for _ in range(5):
        await tool.execute({"query": "x"}, kb_scope=scope)
    assert fr.calls == 5
