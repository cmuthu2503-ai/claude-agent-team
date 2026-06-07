"""KB-11 — bucket isolation hardening + hybrid-arm + concurrency.

KB-07 already proves single-query bucket scoping, pending exclusion, and audit
writes. This file closes the remaining DoD gaps:

  - **Concurrency**: many retrievals scoped to bucket A and bucket B running
    *simultaneously* (asyncio.gather) never cross-pollinate. This is the
    correctness-critical guarantee — the bucket filter is a per-call argument,
    never shared mutable state, so interleaved tasks can't leak each other's
    scope.
  - **Tool boundary**: two ``KnowledgeSearchTool.execute`` calls with different
    ``kb_scope`` run concurrently and each only ever surfaces its own bucket's
    document (the FR-023 grounding guarantee under load).
  - **Hybrid keyword arm**: a lexical-only match (a rare token the fake
    embedder can't rank semantically) is still found — proving the Postgres
    FTS arm contributes to the fused result, not just pgvector.

Live against Postgres with a deterministic fake embedder (no model download).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from src.knowledge.interfaces import Embedder, EmbeddingResult, RerankHit


class _FakeEmbedder(Embedder):
    """Deterministic vectors; rerank = identity order. Vectors are a weak
    signal on purpose so the keyword arm has to carry lexical matches."""

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
        return hits[:top_k] if top_k else hits


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
        pytest.skip("Postgres not reachable for live isolation test")
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


async def _ingest_approved(store, pipe, *, text, ns, bucket_id, title):  # noqa: ANN001
    res = await pipe.ingest_text(
        text=text, title=title, source_type="lesson", namespace=ns, bucket_ids=[bucket_id],
    )
    await store.set_document_status(res.doc_id, "approved", curated_by="system")
    return res.doc_id


# ── Concurrency: A/B retrievals never cross-pollinate ──────────────────────


@pytest.mark.asyncio
async def test_concurrent_bucket_retrievals_isolated(kb):
    store, pipe, retr = kb
    ns = f"kb_iso_{uuid.uuid4().hex[:6]}"
    ba = await store.create_bucket(f"Alpha {uuid.uuid4().hex[:6]}")
    bb = await store.create_bucket(f"Beta {uuid.uuid4().hex[:6]}")
    da = await _ingest_approved(
        store, pipe, ns=ns, bucket_id=ba.bucket_id, title="A",
        text="Alpha bucket: the supervisor deploys staging through the pipeline.")
    db = await _ingest_approved(
        store, pipe, ns=ns, bucket_id=bb.bucket_id, title="B",
        text="Beta bucket: the quality gate blocks a request on low coverage.")
    try:
        # 24 retrievals, alternating scope, all in flight at once. If the
        # bucket filter were shared mutable state, interleaving would leak.
        async def q_a():
            hits = await retr.retrieve("the", ns, bucket_ids=[ba.bucket_id], agent_id="t")
            return ("A", {h.doc_id for h in hits})

        async def q_b():
            hits = await retr.retrieve("the", ns, bucket_ids=[bb.bucket_id], agent_id="t")
            return ("B", {h.doc_id for h in hits})

        tasks = [q_a() if i % 2 == 0 else q_b() for i in range(24)]
        results = await asyncio.gather(*tasks)

        for which, docs in results:
            if which == "A":
                assert da in docs, "Alpha query lost its own doc"
                assert db not in docs, "CROSS-POLLINATION: Beta doc leaked into Alpha scope"
            else:
                assert db in docs, "Beta query lost its own doc"
                assert da not in docs, "CROSS-POLLINATION: Alpha doc leaked into Beta scope"
    finally:
        await store.purge_document(da)
        await store.purge_document(db)
        await store.delete_bucket(ba.bucket_id)
        await store.delete_bucket(bb.bucket_id)


# ── Tool boundary: concurrent execute() with different kb_scope ────────────


@pytest.mark.asyncio
async def test_concurrent_tool_execute_isolated(kb):
    from src.knowledge.tools import KbScope, KnowledgeSearchTool

    store, pipe, retr = kb
    ns = f"kb_iso_{uuid.uuid4().hex[:6]}"
    ba = await store.create_bucket(f"Alpha {uuid.uuid4().hex[:6]}")
    bb = await store.create_bucket(f"Beta {uuid.uuid4().hex[:6]}")
    da = await _ingest_approved(
        store, pipe, ns=ns, bucket_id=ba.bucket_id, title="AlphaDoc",
        text="Alpha bucket content about the supervisor deploy pipeline.")
    db = await _ingest_approved(
        store, pipe, ns=ns, bucket_id=bb.bucket_id, title="BetaDoc",
        text="Beta bucket content about the coverage quality gate.")
    tool = KnowledgeSearchTool(retr)
    try:
        out_a, out_b = await asyncio.gather(
            tool.execute({"query": "content"}, kb_scope=KbScope(
                namespace=ns, bucket_ids=[ba.bucket_id], agent_id="ra")),
            tool.execute({"query": "content"}, kb_scope=KbScope(
                namespace=ns, bucket_ids=[bb.bucket_id], agent_id="rb")),
        )
        # Each tool output cites only its own bucket's document id.
        assert da in out_a and db not in out_a
        assert db in out_b and da not in out_b
    finally:
        await store.purge_document(da)
        await store.purge_document(db)
        await store.delete_bucket(ba.bucket_id)
        await store.delete_bucket(bb.bucket_id)


# ── Hybrid: the keyword (FTS) arm contributes lexical matches ──────────────


@pytest.mark.asyncio
async def test_hybrid_keyword_arm_finds_lexical_match(kb):
    store, pipe, retr = kb
    ns = f"kb_iso_{uuid.uuid4().hex[:6]}"
    b = await store.create_bucket(f"Lex {uuid.uuid4().hex[:6]}")
    # A distinctive token the fake embedder can't rank semantically (vectors
    # only key off length); only the FTS arm can match it lexically.
    rare = "zylophonic"
    doc = await _ingest_approved(
        store, pipe, ns=ns, bucket_id=b.bucket_id, title="Lexical",
        text=f"The {rare} subsystem reconciles deployment drift nightly.")
    # A decoy doc with no overlapping rare term.
    decoy = await _ingest_approved(
        store, pipe, ns=ns, bucket_id=b.bucket_id, title="Decoy",
        text="Unrelated notes about pancakes and weather.")
    try:
        hits = await retr.retrieve(rare, ns, bucket_ids=[b.bucket_id], agent_id="t")
        ids = {h.doc_id for h in hits}
        assert doc in ids, "keyword arm failed to surface the lexical match"
    finally:
        await store.purge_document(doc)
        await store.purge_document(decoy)
        await store.delete_bucket(b.bucket_id)
