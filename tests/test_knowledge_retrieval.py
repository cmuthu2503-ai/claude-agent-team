"""KB-07 — bucket-scoped retrieval.

rrf_fuse is pure. The retrieval tests run live against Postgres with a fake
embedder and ingest real docs into two buckets, then prove the **grounding
guarantee**: a query scoped to bucket A never returns bucket B chunks, and
unapproved (pending) docs are never retrieved.
"""

from __future__ import annotations

import os
import uuid

import pytest

from src.knowledge.interfaces import Embedder, EmbeddingResult, RerankHit
from src.knowledge.retrieval import rrf_fuse

# ── RRF (pure) ───────────────────────────────────────────────────────────


def test_rrf_rewards_agreement():
    # 'b' ranks well in BOTH lists → should top the fused order
    v = ["a", "b", "c"]
    k = ["b", "x", "a"]
    fused = rrf_fuse(v, k)
    assert fused[0][0] == "b"
    ids = [cid for cid, _ in fused]
    assert set(ids) == {"a", "b", "c", "x"}


def test_rrf_empty():
    assert rrf_fuse([], []) == []


# ── Live retrieval ───────────────────────────────────────────────────────


class _FakeEmbedder(Embedder):
    """Deterministic vectors; rerank preserves input order (identity)."""

    @property
    def dimensions(self) -> int:
        return 384

    @property
    def model(self) -> str:
        return "fake-3"

    async def embed_documents(self, texts):  # type: ignore[no-untyped-def]
        return EmbeddingResult(vectors=[self._vec(t) for t in texts], model="fake-3")

    async def embed_query(self, text):  # type: ignore[no-untyped-def]
        return self._vec(text)

    @staticmethod
    def _vec(t: str):
        return [float(len(t) % 5), 0.5, 1.0] + [0.0] * 381

    async def rerank(self, query, documents, top_k=None):  # type: ignore[no-untyped-def]
        hits = [RerankHit(index=i, score=1.0 - i * 0.01) for i in range(len(documents))]
        return hits[: top_k] if top_k else hits


@pytest.fixture
async def kb():
    from src.knowledge.ingest import IngestionPipeline
    from src.knowledge.pg import open_pool
    from src.knowledge.retrieval import Retriever
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
        pytest.skip("Postgres not reachable for live retrieval test")
    store = KnowledgeStore(pool, dimensions=384)
    await store.initialize()
    emb = _FakeEmbedder()
    pipe = IngestionPipeline(store, emb, max_tokens=80, overlap_tokens=8)
    retr = Retriever(emb, _PgV(pool), _PgF(pool), store, top_k=10, candidates=40)
    try:
        yield store, pipe, retr
    finally:
        await pool.close()


def _PgV(pool):  # noqa: N802
    from src.knowledge.store_pgvector import PgVectorStore
    return PgVectorStore(pool, dimensions=384)


def _PgF(pool):  # noqa: N802
    from src.knowledge.store_pgfts import PostgresFtsStore
    return PostgresFtsStore(pool)


async def _ingest_approved(store, pipe, *, text, ns, bucket_id, title):  # noqa: ANN001
    res = await pipe.ingest_text(
        text=text, title=title, source_type="lesson", namespace=ns, bucket_ids=[bucket_id]
    )
    await store.set_document_status(res.doc_id, "approved", curated_by="system")
    return res.doc_id


@pytest.mark.asyncio
async def test_retrieval_is_bucket_scoped(kb):
    store, pipe, retr = kb
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    ba = await store.create_bucket(f"Alpha {uuid.uuid4().hex[:6]}")
    bb = await store.create_bucket(f"Beta {uuid.uuid4().hex[:6]}")
    da = await _ingest_approved(
        store, pipe, ns=ns, bucket_id=ba.bucket_id, title="A",
        text="The supervisor deploys to staging using the pipeline.")
    db = await _ingest_approved(
        store, pipe, ns=ns, bucket_id=bb.bucket_id, title="B",
        text="The quality gate blocks a request when coverage is low.")
    try:
        # scope to Alpha → only Alpha's doc chunks come back
        hits_a = await retr.retrieve("deploy", ns, bucket_ids=[ba.bucket_id], agent_id="t")
        assert hits_a, "expected Alpha results"
        assert all(h.doc_id == da for h in hits_a)
        assert all(h.doc_id != db for h in hits_a)

        # scope to Beta → only Beta
        hits_b = await retr.retrieve("coverage", ns, bucket_ids=[bb.bucket_id], agent_id="t")
        assert hits_b and all(h.doc_id == db for h in hits_b)

        # no bucket scope → namespace-wide (both docs reachable)
        hits_all = await retr.retrieve("the", ns, agent_id="t")
        doc_ids = {h.doc_id for h in hits_all}
        assert da in doc_ids and db in doc_ids

        # citation pointers present
        assert all(h.title and h.text for h in hits_a)
    finally:
        await store.purge_document(da)
        await store.purge_document(db)
        await store.delete_bucket(ba.bucket_id)
        await store.delete_bucket(bb.bucket_id)


@pytest.mark.asyncio
async def test_retrieval_excludes_pending_docs(kb):
    store, pipe, retr = kb
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    b = await store.create_bucket(f"B {uuid.uuid4().hex[:6]}")
    # ingest but DO NOT approve → stays pending
    res = await pipe.ingest_text(
        text="A pending document about deployment that must not be retrieved.",
        title="Pending", source_type="lesson", namespace=ns, bucket_ids=[b.bucket_id])
    try:
        hits = await retr.retrieve("deployment", ns, bucket_ids=[b.bucket_id], agent_id="t")
        assert all(h.doc_id != res.doc_id for h in hits)  # pending excluded
        # approve it → now retrievable
        await store.set_document_status(res.doc_id, "approved")
        hits2 = await retr.retrieve("deployment", ns, bucket_ids=[b.bucket_id], agent_id="t")
        assert any(h.doc_id == res.doc_id for h in hits2)
    finally:
        await store.purge_document(res.doc_id)
        await store.delete_bucket(b.bucket_id)


@pytest.mark.asyncio
async def test_feedback_reorders_retrieval(kb):
    """KB-31 — upvotes lift a chunk in the final order. Two docs both match the
    query; stacking upvotes on the lower-ranked one floats it to the top."""
    store, pipe, retr = kb
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    b = await store.create_bucket(f"B {uuid.uuid4().hex[:6]}")
    d1 = await _ingest_approved(
        store, pipe, ns=ns, bucket_id=b.bucket_id, title="One",
        text="deployment pipeline supervisor staging rollout one.")
    d2 = await _ingest_approved(
        store, pipe, ns=ns, bucket_id=b.bucket_id, title="Two",
        text="deployment pipeline supervisor staging rollout two.")
    try:
        base = await retr.retrieve("deployment", ns, bucket_ids=[b.bucket_id], agent_id="t")
        assert len(base) >= 2
        top0, second = base[0].chunk_id, base[1].chunk_id

        # Stack fresh upvotes (distinct users) on the SECOND chunk.
        for i in range(6):
            await store.record_feedback(
                chunk_id=second, namespace=ns, vote=1, created_by=f"voter-{i}")

        # KB-32 caches identical queries; clear it to simulate the TTL window
        # elapsing so the re-rank with new feedback is observed.
        retr._cache.clear()  # noqa: SLF001
        boosted = await retr.retrieve("deployment", ns, bucket_ids=[b.bucket_id], agent_id="t")
        assert boosted[0].chunk_id == second  # the upvoted chunk now leads
        assert boosted[0].chunk_id != top0
    finally:
        async with store._pool.connection() as conn:  # noqa: SLF001
            await conn.execute(
                "DELETE FROM kb_feedback WHERE namespace=%s", [ns])
        await store.purge_document(d1)
        await store.purge_document(d2)
        await store.delete_bucket(b.bucket_id)


@pytest.mark.asyncio
async def test_retrieval_writes_audit(kb):
    store, pipe, retr = kb
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    b = await store.create_bucket(f"B {uuid.uuid4().hex[:6]}")
    doc = await _ingest_approved(
        store, pipe, ns=ns, bucket_id=b.bucket_id, title="D",
        text="Auditable retrieval over the supervisor deployment flow.")
    try:
        await retr.retrieve("supervisor", ns, bucket_ids=[b.bucket_id],
                            agent_id="research_specialist", request_id="req-aud")
        async with store._pool.connection() as conn:  # noqa: SLF001
            cur = await conn.execute(
                "SELECT agent_id, query, returned_chunk_ids FROM kb_retrieval_audit "
                "WHERE request_id=%s ORDER BY created_at DESC LIMIT 1", ["req-aud"])
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "research_specialist" and row[1] == "supervisor"
        assert isinstance(row[2], list)
        # cleanup audit
        async with store._pool.connection() as conn:  # noqa: SLF001
            await conn.execute("DELETE FROM kb_retrieval_audit WHERE request_id=%s", ["req-aud"])
    finally:
        await store.purge_document(doc)
        await store.delete_bucket(b.bucket_id)
