"""KB-13 — per-project KB provisioning + purge (Phase 2).

Live against Postgres with a deterministic fake embedder. Proves:
  - provision_project creates a project-owned default bucket, idempotently.
  - purge_project removes a project's documents, chunks, buckets, audit, and
    decision-ledger rows — and ONLY that project's (cross-project isolation).
  - the EventEmitter handler dispatches provision on project.created and purge
    on project.deleted, and no-ops when the subsystem is unavailable.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from src.knowledge.interfaces import Embedder, EmbeddingResult, RerankHit
from src.knowledge.project_lifecycle import make_kb_project_handler


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
        return [RerankHit(index=i, score=1.0) for i in range(len(documents))]


@pytest.fixture
async def kb():
    from src.knowledge.ingest import IngestionPipeline
    from src.knowledge.pg import open_pool
    from src.knowledge.store import KnowledgeStore

    dsn = (
        f"host={os.getenv('KB_PG_HOST', 'postgres')} port={os.getenv('KB_PG_PORT', '5432')} "
        f"user={os.getenv('KB_PG_USER', 'agentteam')} "
        f"password={os.getenv('KB_PG_PASSWORD', 'change-me-in-dev')} "
        f"dbname={os.getenv('KB_PG_DB', 'agentteam_kb')}"
    )
    try:
        pool = await open_pool(dsn, 1, 4)
    except Exception:
        pytest.skip("Postgres not reachable for live project-lifecycle test")
    store = KnowledgeStore(pool, dimensions=384)
    await store.initialize()
    pipe = IngestionPipeline(store, _FakeEmbedder(), max_tokens=80, overlap_tokens=8)
    try:
        yield store, pipe
    finally:
        await pool.close()


async def _seed_project(store, pipe, project_id):  # noqa: ANN001
    """Provision + ingest one approved doc into a project's namespace, and write
    an audit + ledger row scoped to it. Returns (namespace, doc_id, bucket)."""
    ns = f"kb_project_{project_id}"
    bucket = await store.provision_project(project_id, ns)
    res = await pipe.ingest_text(
        text=f"Project {project_id} app knowledge about its brand and domain.",
        title="app-doc", source_type="upload", namespace=ns,
        bucket_ids=[bucket.bucket_id], project_id=project_id,
    )
    await store.set_document_status(res.doc_id, "approved")
    await store.record_retrieval(
        agent_id="research_specialist", namespace=ns, query="brand",
        request_id=f"req-{project_id}", bucket_ids=[bucket.bucket_id],
        returned_chunk_ids=["c1"], cited_chunk_ids=["c1"],
    )
    await store.record_decision(
        request_id=f"req-{project_id}", agent_id="research_specialist",
        summary="grounded", project_id=project_id,
    )
    return ns, res.doc_id, bucket


# ── Provisioning ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provision_is_idempotent(kb):
    store, _ = kb
    pid = f"proj-{uuid.uuid4().hex[:8]}"
    ns = f"kb_project_{pid}"
    try:
        b1 = await store.provision_project(pid, ns)
        b2 = await store.provision_project(pid, ns)
        assert b1.bucket_id == b2.bucket_id, "re-provision must return the same bucket"
        assert b1.is_system is True
        assert b1.project_id == pid
    finally:
        await store.purge_project(pid, ns)


# ── Purge + cross-project isolation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_removes_everything_and_is_isolated(kb):
    store, pipe = kb
    pid_a = f"projA-{uuid.uuid4().hex[:8]}"
    pid_b = f"projB-{uuid.uuid4().hex[:8]}"
    ns_a, doc_a, bkt_a = await _seed_project(store, pipe, pid_a)
    ns_b, doc_b, bkt_b = await _seed_project(store, pipe, pid_b)
    try:
        # Sanity: both projects have data before the purge.
        assert await store.get_document(doc_a) is not None
        assert await store.get_document(doc_b) is not None

        counts = await store.purge_project(pid_a, ns_a)
        assert counts["documents"] >= 1
        assert counts["buckets"] >= 1
        assert counts["audit"] >= 1
        assert counts["ledger"] >= 1

        # Project A is gone…
        assert await store.get_document(doc_a) is None
        assert await store.get_bucket(bkt_a.bucket_id) is None
        async with store._pool.connection() as conn:  # noqa: SLF001
            cur = await conn.execute(
                "SELECT count(*) FROM kb_chunks WHERE namespace=%s", [ns_a])
            assert (await cur.fetchone())[0] == 0
            cur = await conn.execute(
                "SELECT count(*) FROM kb_retrieval_audit WHERE namespace=%s", [ns_a])
            assert (await cur.fetchone())[0] == 0
            cur = await conn.execute(
                "SELECT count(*) FROM decision_ledger WHERE project_id=%s", [pid_a])
            assert (await cur.fetchone())[0] == 0

        # …while Project B is completely untouched (ISOLATION).
        assert await store.get_document(doc_b) is not None
        assert await store.get_bucket(bkt_b.bucket_id) is not None
    finally:
        await store.purge_project(pid_b, ns_b)


# ── Handler dispatch ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handler_provisions_and_purges(kb):
    store, _ = kb
    pid = f"proj-{uuid.uuid4().hex[:8]}"
    settings = SimpleNamespace(
        project_namespace=lambda p: f"kb_project_{p}",
        memory_namespace=lambda p: f"mem_project_{p}",
    )
    sub = SimpleNamespace(available=True, knowledge_store=store, settings=settings)
    handler = make_kb_project_handler(sub)
    try:
        await handler("project.created", {"project_id": pid})
        b = await store.get_bucket(
            (await store.provision_project(pid, f"kb_project_{pid}")).bucket_id
        )
        assert b is not None and b.project_id == pid

        await handler("project.deleted", {"project_id": pid})
        # bucket gone after purge
        async with store._pool.connection() as conn:  # noqa: SLF001
            cur = await conn.execute(
                "SELECT count(*) FROM kb_buckets WHERE project_id=%s", [pid])
            assert (await cur.fetchone())[0] == 0
    finally:
        await store.purge_project(pid, f"kb_project_{pid}")


@pytest.mark.asyncio
async def test_handler_noops_when_unavailable():
    sub = SimpleNamespace(available=False)
    handler = make_kb_project_handler(sub)
    # Must not raise / not touch anything.
    await handler("project.created", {"project_id": "x"})
    await handler("project.deleted", {"project_id": "x"})


@pytest.mark.asyncio
async def test_handler_ignores_other_events(kb):
    store, _ = kb
    settings = SimpleNamespace(
        project_namespace=lambda p: f"kb_project_{p}",
        memory_namespace=lambda p: f"mem_project_{p}",
    )
    sub = SimpleNamespace(available=True, knowledge_store=store, settings=settings)
    handler = make_kb_project_handler(sub)
    # Unrelated event → no-op (no project bucket created).
    await handler("request.completed", {"request_id": "r1"})
