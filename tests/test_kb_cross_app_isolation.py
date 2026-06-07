"""KB-18 — cross-application isolation, end-to-end (Phase 2 capstone).

Validates the whole per-app grounding chain wired across KB-13 (provisioning),
KB-14 (ingest), KB-15 (namespace scope injection) and KB-17 (dual-source
facts/craft):

  1. The executor derives the namespace from ``Request.project_id`` — a task on
     App A resolves to ``kb_project_A`` (facts) + ``kb_platform`` (craft).
  2. Retrieval scoped to App A returns chunks **100% from App A** — never B's,
     even with identical query text.
  3. The ``knowledge_search`` tool is hard-sealed: a smuggled ``namespace`` /
     ``bucket_ids`` in the tool params is ignored — the agent cannot widen
     beyond the executor-injected scope (FR-023).
  4. Concurrent A/B retrievals never cross-pollinate.

Live against Postgres with a deterministic fake embedder.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest

from src.knowledge.interfaces import Embedder, EmbeddingResult, RerankHit
from src.knowledge.tools import KbScope, KnowledgeSearchTool
from src.models.base import Request


class _FakeEmbedder(Embedder):
    @property
    def dimensions(self) -> int:
        return 384

    @property
    def model(self) -> str:
        return "fake-3"

    async def embed_documents(self, texts):  # type: ignore[no-untyped-def]
        return EmbeddingResult(
            vectors=[[float(len(t) % 5), 0.5, 1.0] + [0.0] * 381 for t in texts],
            model="fake-3",
        )

    async def embed_query(self, text):  # type: ignore[no-untyped-def]
        return [float(len(text) % 5), 0.5, 1.0] + [0.0] * 381

    async def rerank(self, query, documents, top_k=None):  # type: ignore[no-untyped-def]
        return [RerankHit(index=i, score=1.0 - i * 0.01) for i in range(len(documents))]


_SETTINGS = SimpleNamespace(
    platform_namespace="kb_platform",
    project_namespace=lambda p: f"kb_project_{p}",
    memory_namespace=lambda p: f"mem_project_{p}",
)


@pytest.fixture
async def kb():
    from src.knowledge.ingest import IngestionPipeline
    from src.knowledge.pg import open_pool
    from src.knowledge.retrieval import Retriever
    from src.knowledge.store import KnowledgeStore
    from src.knowledge.store_pgfts import PostgresFtsStore
    from src.knowledge.store_pgvector import PgVectorStore

    dsn = (
        f"host={os.getenv('KB_PG_HOST', 'postgres')} port={os.getenv('KB_PG_PORT', '5432')} "
        f"user={os.getenv('KB_PG_USER', 'agentteam')} "
        f"password={os.getenv('KB_PG_PASSWORD', 'change-me-in-dev')} "
        f"dbname={os.getenv('KB_PG_DB', 'agentteam_kb')}"
    )
    try:
        pool = await open_pool(dsn, 2, 8)
    except Exception:
        pytest.skip("Postgres not reachable for cross-app isolation E2E")
    store = KnowledgeStore(pool, dimensions=384)
    await store.initialize()
    emb = _FakeEmbedder()
    pipe = IngestionPipeline(store, emb, max_tokens=80, overlap_tokens=8)
    retr = Retriever(
        emb, PgVectorStore(pool, dimensions=384), PostgresFtsStore(pool), store,
        top_k=10, candidates=40,
    )
    try:
        yield store, pipe, retr
    finally:
        await pool.close()


async def _seed(store, pipe, project_id, text):  # noqa: ANN001
    ns = f"kb_project_{project_id}"
    bucket = await store.provision_project(project_id, ns)
    res = await pipe.ingest_text(
        text=text, title=f"{project_id}-prd", source_type="prd", namespace=ns,
        bucket_ids=[bucket.bucket_id], project_id=project_id,
    )
    await store.set_document_status(res.doc_id, "approved")
    return ns, res.doc_id, bucket.bucket_id


# ── 1. Executor resolves the namespace from the Request's project_id ────────


class _StateWithRequest:
    def __init__(self, request):  # noqa: ANN001
        self._request = request

    async def get_request(self, request_id):  # noqa: ANN001
        return self._request


@pytest.mark.asyncio
async def test_executor_resolves_project_namespace_end_to_end():
    from src.agents.executor import AgentSystemExecutor
    from src.config.loader import ConfigLoader

    config = ConfigLoader()
    config.load_all()
    executor = AgentSystemExecutor(config, state=None)
    executor.kb_subsystem = SimpleNamespace(
        available=True, settings=_SETTINGS, retriever=object(), knowledge_store=object(),
    )

    # A research task filed against project "appA" → facts in the app namespace,
    # craft from platform (research_specialist is scope=auto).
    executor.state = _StateWithRequest(
        Request(request_id="REQ-A", description="research App A pricing", project_id="appA"))
    scope, _, _ = await executor._resolve_kb_for_request(
        "research_specialist", "REQ-A", {"description": "research pricing"})
    assert scope.namespace == "kb_project_appA"
    assert scope.craft_namespace == "kb_platform"

    # A project-less (unassigned) task degrades to the platform namespace,
    # with no separate craft source.
    executor.state = _StateWithRequest(
        Request(request_id="REQ-U", description="generic task"))  # no project → unassigned
    scope_u, _, _ = await executor._resolve_kb_for_request(
        "research_specialist", "REQ-U", {"description": "generic"})
    assert scope_u.namespace == "kb_platform"
    assert scope_u.craft_namespace is None


# ── 2. Retrieval is 100% from the scoped app ────────────────────────────────


@pytest.mark.asyncio
async def test_retrieval_and_citations_are_100pct_from_scoped_app(kb):
    store, pipe, retr = kb
    a = f"appA-{uuid.uuid4().hex[:8]}"
    b = f"appB-{uuid.uuid4().hex[:8]}"
    fact = "The product charges a 2% fee on every transaction."
    ns_a, doc_a, bkt_a = await _seed(store, pipe, a, fact)
    ns_b, doc_b, bkt_b = await _seed(store, pipe, b, fact)
    try:
        tool = KnowledgeSearchTool(retr)
        out_a = await tool.execute(
            {"query": "transaction fee"},
            kb_scope=KbScope(namespace=ns_a, bucket_ids=[bkt_a], agent_id="research_specialist",
                             request_id="REQ-A"),
        )
        assert doc_a in out_a and doc_b not in out_a
        # The audit recorded the retrieval against App A's namespace + buckets only.
        audit = await store.list_retrieval_audit("REQ-A")
        assert audit and all(set(r["bucket_ids"]) <= {bkt_a} for r in audit)
    finally:
        await store.purge_project(a, ns_a)
        await store.purge_project(b, ns_b)


# ── 3. The tool is hard-sealed — smuggled scope is ignored (FR-023) ─────────


@pytest.mark.asyncio
async def test_smuggled_scope_params_are_ignored(kb):
    store, pipe, retr = kb
    a = f"appA-{uuid.uuid4().hex[:8]}"
    b = f"appB-{uuid.uuid4().hex[:8]}"
    ns_a, doc_a, bkt_a = await _seed(store, pipe, a, "App A: budgets and expense categories.")
    ns_b, doc_b, bkt_b = await _seed(store, pipe, b, "App B: budgets and expense categories.")
    try:
        tool = KnowledgeSearchTool(retr)
        # The "agent" tries to smuggle App B's namespace + bucket into the params.
        out = await tool.execute(
            {"query": "budgets", "namespace": ns_b, "bucket_ids": [bkt_b]},
            kb_scope=KbScope(namespace=ns_a, bucket_ids=[bkt_a],
                             agent_id="research_specialist", request_id="REQ-A"),
        )
        # Scope came from the executor-injected kb_scope (App A) — the smuggled
        # params were never read. App B is unreachable.
        assert doc_a in out and doc_b not in out
    finally:
        await store.purge_project(a, ns_a)
        await store.purge_project(b, ns_b)


# ── 4. Concurrent A/B never cross-pollinate ─────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_app_tasks_never_cross_pollinate(kb):
    store, pipe, retr = kb
    a = f"appA-{uuid.uuid4().hex[:8]}"
    b = f"appB-{uuid.uuid4().hex[:8]}"
    ns_a, doc_a, bkt_a = await _seed(store, pipe, a, "App A fintech ledger reconciliation rules.")
    ns_b, doc_b, bkt_b = await _seed(store, pipe, b, "App B fintech ledger reconciliation rules.")
    tool = KnowledgeSearchTool(retr)
    try:
        async def q(ns, bkt, mine, other):  # noqa: ANN001
            out = await tool.execute(
                {"query": "ledger reconciliation"},
                kb_scope=KbScope(namespace=ns, bucket_ids=[bkt], agent_id="t"),
            )
            return mine in out and other not in out

        tasks = [
            q(ns_a, bkt_a, doc_a, doc_b) if i % 2 == 0 else q(ns_b, bkt_b, doc_b, doc_a)
            for i in range(20)
        ]
        results = await asyncio.gather(*tasks)
        assert all(results), "cross-pollination under concurrency"
    finally:
        await store.purge_project(a, ns_a)
        await store.purge_project(b, ns_b)
