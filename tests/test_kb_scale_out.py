"""KB-33 — async ingestion dispatcher + datastore swap-seam validation.

Pure unit tests (no live Postgres, no Redis):

1. ``IngestionDispatcher`` routes work inline / background / queued (with
   fallback), and a background failure never escapes.
2. NFR-003 — a DROP-IN alternative ``VectorStore`` (not ``PgVectorStore``) plugs
   into the real ``Retriever`` and hybrid retrieval still works, proving the swap
   path (pgvector→Qdrant, FTS→ParadeDB) needs no call-site change.
"""

from __future__ import annotations

import asyncio

import pytest

from src.knowledge.ingest_dispatch import IngestionDispatcher
from src.knowledge.retrieval import Retriever

# ── 1. dispatcher ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inline_awaits_and_returns_result():
    d = IngestionDispatcher(mode="inline")

    async def work():
        return "doc-1"

    assert await d.submit(work()) == "doc-1"


@pytest.mark.asyncio
async def test_background_is_fire_and_forget():
    d = IngestionDispatcher(mode="background")
    ran = asyncio.Event()

    async def work():
        ran.set()

    res = await d.submit(work())
    assert res is None          # fire-and-forget
    await d.drain()
    assert ran.is_set()         # but it did run


@pytest.mark.asyncio
async def test_background_failure_does_not_escape():
    d = IngestionDispatcher(mode="background")

    async def boom():
        raise RuntimeError("ingest blew up")

    await d.submit(boom())
    await d.drain()             # must not raise


@pytest.mark.asyncio
async def test_queue_uses_enqueue_backend():
    enqueued: list = []

    async def enqueue(awaitable):
        enqueued.append(awaitable)
        awaitable.close()       # we won't run it; just prove it was handed off

    d = IngestionDispatcher(mode="queue", enqueue=enqueue)

    async def work():
        return "x"

    assert await d.submit(work()) is None
    assert len(enqueued) == 1


@pytest.mark.asyncio
async def test_queue_without_backend_falls_back_to_background():
    d = IngestionDispatcher(mode="queue", enqueue=None)  # misconfigured
    ran = asyncio.Event()

    async def work():
        ran.set()

    await d.submit(work())
    await d.drain()
    assert ran.is_set()         # degraded to background, not dropped


def test_unknown_mode_defaults_inline():
    assert IngestionDispatcher(mode="nonsense").mode == "inline"


# ── 2. datastore swap seam (NFR-003) ─────────────────────────────────────────


class _Hit:
    def __init__(self, cid: str) -> None:
        self.chunk_id = cid


class _AltVectorStore:
    """A NON-pgvector VectorStore — stands in for Qdrant. Implements only the
    interface the Retriever depends on."""

    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    async def search(self, namespace, query_vec, k, bucket_ids=None):  # noqa: ANN001
        return [_Hit(c) for c in self._ids[:k]]


class _AltKeywordStore:
    """A NON-Postgres-FTS KeywordStore — stands in for ParadeDB/OpenSearch."""

    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    async def search(self, namespace, query, k, bucket_ids=None):  # noqa: ANN001
        return [_Hit(c) for c in self._ids[:k]]


class _Embedder:
    @property
    def dimensions(self) -> int:
        return 384

    @property
    def model(self) -> str:
        return "fake"

    async def embed_query(self, text):  # noqa: ANN001
        return [0.1] * 384

    async def rerank(self, query, documents, top_k=None):  # noqa: ANN001
        return []


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


@pytest.mark.asyncio
async def test_retriever_works_with_alternative_stores():
    # Swap BOTH stores for non-default impls; the Retriever is unchanged.
    retr = Retriever(
        _Embedder(), _AltVectorStore(["c1", "c2"]), _AltKeywordStore(["c2", "c3"]),
        _KnowledgeStore(), top_k=5, candidates=10, rerank=False,
    )
    hits = await retr.retrieve("anything", "kb_platform", agent_id="a")
    ids = {h.chunk_id for h in hits}
    # 'c2' agreed across both arms (RRF) and all hydrate → results returned.
    assert "c2" in ids
    assert all(h.text and h.title for h in hits)
