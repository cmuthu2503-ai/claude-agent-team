"""KB-12 — retrieval eval harness tests.

Pure metric tests (recall@k, MRR, gold-set loading) + a live eval that ingests
the gold corpus through the real retriever (fake embedder + Postgres) and gates
on a recorded baseline. The gold queries anchor on distinctive lexical terms so
the Postgres FTS arm carries the match deterministically — no model download
needed for CI.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from src.knowledge.eval import (
    GoldBucket,
    load_gold_set,
    recall_at_k,
    reciprocal_rank,
    run_eval,
)
from src.knowledge.interfaces import Embedder, EmbeddingResult, RerankHit

GOLD_PATH = "tests/data/kb_eval_gold.yaml"

# CI gate. With the fake (length-only) embedder the *semantic* arm is noise, so
# these floors prove the FTS arm + fusion reliably surface the right doc. The
# real-embedder baseline (docs/kb-eval-baseline.json) is higher; this is the
# regression floor that must never drop.
BASELINE_RECALL_AT_5 = 0.95
BASELINE_MRR = 0.80


# ── Pure metrics ───────────────────────────────────────────────────────────


def test_recall_at_k_basic():
    assert recall_at_k(["a", "b", "c"], {"b"}, 5) == 1.0
    assert recall_at_k(["a", "b", "c"], {"z"}, 5) == 0.0
    # one of two relevant in top-k → 0.5
    assert recall_at_k(["a", "b", "c"], {"b", "z"}, 5) == 0.5
    # cutoff excludes a relevant beyond k
    assert recall_at_k(["a", "b", "c"], {"c"}, 2) == 0.0


def test_recall_empty_relevant_is_zero():
    assert recall_at_k(["a"], set(), 5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["a", "b", "c"], {"a"}) == 1.0
    assert reciprocal_rank(["a", "b", "c"], {"b"}) == 0.5
    assert reciprocal_rank(["a", "b", "c"], {"c"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["a", "b", "c"], {"z"}) == 0.0


def test_load_gold_set():
    gold = load_gold_set(GOLD_PATH)
    assert len(gold) >= 3
    names = {g.name for g in gold}
    assert {"deployment", "auth", "knowledge_base"} <= names
    for g in gold:
        assert g.docs and g.queries
        titles = {d.title for d in g.docs}
        # every query's relevant titles exist among the bucket's docs
        for q in g.queries:
            assert set(q.relevant_titles) <= titles


# ── Live eval against the baseline ─────────────────────────────────────────


class _FakeEmbedder(Embedder):
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
        pool = await open_pool(dsn, 1, 4)
    except Exception:
        pytest.skip("Postgres not reachable for live eval")
    store = KnowledgeStore(pool, dimensions=384)
    await store.initialize()
    emb = _FakeEmbedder()
    pipe = IngestionPipeline(store, emb, max_tokens=120, overlap_tokens=12)
    retr = Retriever(
        emb, PgVectorStore(pool, dimensions=384), PostgresFtsStore(pool), store,
        top_k=10, candidates=40,
    )
    try:
        yield store, pipe, retr
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_eval_meets_baseline(kb):
    store, pipe, retr = kb
    gold = load_gold_set(GOLD_PATH)
    result = await run_eval(
        retriever=retr, store=store, pipeline=pipe, gold=gold,
        namespace=f"kb_eval_{uuid.uuid4().hex[:8]}", k=5,
    )
    assert result.queries >= 7
    assert result.recall_at_k >= BASELINE_RECALL_AT_5, (
        f"recall@5 {result.recall_at_k:.3f} < baseline {BASELINE_RECALL_AT_5} — "
        f"per-query: {result.per_query}"
    )
    assert result.mrr >= BASELINE_MRR, (
        f"MRR {result.mrr:.3f} < baseline {BASELINE_MRR}"
    )


@pytest.mark.asyncio
async def test_eval_cleans_up_after_itself(kb):
    """The harness purges its corpus + buckets so repeated CI runs don't leak
    rows. After a run, none of the eval buckets remain."""
    store, pipe, retr = kb
    gold: list[GoldBucket] = load_gold_set(GOLD_PATH)
    before = {b.bucket_id for b in await store.list_buckets()}
    await run_eval(
        retriever=retr, store=store, pipeline=pipe, gold=gold,
        namespace=f"kb_eval_{uuid.uuid4().hex[:8]}", k=5, cleanup=True,
    )
    after = {b.bucket_id for b in await store.list_buckets()}
    assert before == after, "eval leaked buckets"


def test_recorded_baseline_file_present_and_sane():
    """The committed baseline is the documented reference point. It must exist,
    parse, and not advertise metrics below the CI floor."""
    import json

    p = Path("docs/kb-eval-baseline.json")
    assert p.exists(), "baseline file missing — run `python -m src.knowledge.eval`"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["k"] == 5
    assert data["recall_at_k"] >= BASELINE_RECALL_AT_5
    assert data["mrr"] >= BASELINE_MRR
