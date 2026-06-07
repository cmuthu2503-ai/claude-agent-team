"""Bucket-scoped retrieval pipeline (KB-07).

The read side. Hybrid retrieval — fans a query out to the vector + keyword
stores (both **hard-scoped to the request's buckets** and approved-only),
fuses the two result sets with Reciprocal Rank Fusion, reranks the candidates
via the embedder, and returns the top-K with **citation pointers** (chunk text
+ source doc title/uri). Every retrieval is written to ``kb_retrieval_audit``.

The grounding guarantee (FR-023) lives here: ``bucket_ids`` is passed straight
into the store filters; an agent that asks for bucket A's scope can only get
bucket A's chunks. Empty ``bucket_ids`` = the whole namespace (platform-wide).

Why hybrid + RRF + rerank, not pure vector: BM25/FTS catches exact tokens
(REQ-IDs, function names, error codes) embeddings miss; RRF fuses the two
rank lists without needing comparable score scales; the reranker gives the
final, query-aware ordering. (L27 — deterministic signal before LLM judgment.)
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import structlog

from src.knowledge.interfaces import Embedder, KeywordStore, VectorStore

logger = structlog.get_logger()

# RRF damping constant — the standard 60 from the original paper. Larger =
# flatter weighting across ranks.
_RRF_K = 60

# KB-31 — how strongly recency-weighted feedback nudges the final order.
# Applied multiplicatively (scale-robust across fused/rerank score ranges):
# final = base * max(0, 1 + WEIGHT * boost). One fresh upvote (boost≈1) at the
# default 0.5 lifts a chunk ~50%; a downvote sinks it. Env-tunable.
_FEEDBACK_WEIGHT = float(os.getenv("KB_FEEDBACK_WEIGHT", "0.5"))

# KB-32 — identical-query cache. Bounds embedding + vector/keyword cost when the
# same (namespace, buckets, query) repeats inside the TTL window — common when
# several agents on one Request ask the same thing. In-process TTL+LRU now; the
# Redis swap is the scale-out path (behind the same call site). 0 ttl/max = off.
_CACHE_TTL_S = float(os.getenv("KB_QUERY_CACHE_TTL_S", "300"))
_CACHE_MAX = int(os.getenv("KB_QUERY_CACHE_MAX", "512"))


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float
    doc_id: str
    title: str
    uri: str | None
    namespace: str
    metadata: dict[str, Any]


def rrf_fuse(
    vector_ids: list[str], keyword_ids: list[str], k: int = _RRF_K
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion of two ranked id lists. Returns
    ``(chunk_id, fused_score)`` sorted by descending score. A chunk that ranks
    well in BOTH arms rises to the top."""
    scores: dict[str, float] = {}
    for rank, cid in enumerate(vector_ids):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    for rank, cid in enumerate(keyword_ids):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


class Retriever:
    """One per subsystem. Holds the embedder + stores + knowledge store."""

    def __init__(
        self,
        embedder: Embedder,
        vector_store: VectorStore,
        keyword_store: KeywordStore,
        knowledge_store: Any,            # KnowledgeStore
        *,
        top_k: int = 8,
        candidates: int = 40,
        rerank: bool = True,
    ) -> None:
        self._embedder = embedder
        self._vector = vector_store
        self._keyword = keyword_store
        self._store = knowledge_store
        self._top_k = top_k
        self._candidates = candidates
        self._rerank = rerank
        # KB-32 — identical-query cache: key → (expires_at, results).
        self._cache: OrderedDict[tuple, tuple[float, list[RetrievedChunk]]] = OrderedDict()

    async def retrieve(
        self,
        query: str,
        namespace: str,
        *,
        bucket_ids: list[str] | None = None,
        agent_id: str = "",
        request_id: str | None = None,
        top_k: int | None = None,
        rerank: bool | None = None,
    ) -> list[RetrievedChunk]:
        top_k = top_k or self._top_k
        do_rerank = self._rerank if rerank is None else rerank

        # KB-32 — identical-query cache. A hit skips embedding + both store
        # queries (the expensive part), but still writes the audit so the
        # Grounding Report reflects the retrieval the agent actually did.
        cache_key = (
            namespace, tuple(sorted(bucket_ids or [])),
            " ".join(query.split()).lower(), top_k, do_rerank,
        )
        cached = self._cache_get(cache_key)
        if cached is not None:
            await self._audit(
                agent_id, namespace, query, request_id, bucket_ids,
                [c.chunk_id for c in cached],
            )
            logger.info("kb_retrieve_cache_hit", namespace=namespace, agent_id=agent_id)
            return cached

        # 1. Hybrid candidate retrieval (both arms bucket-scoped + approved-only).
        qvec = await self._embedder.embed_query(query)
        vhits = await self._vector.search(
            namespace, qvec, self._candidates, bucket_ids=bucket_ids
        )
        khits = await self._keyword.search(
            namespace, query, self._candidates, bucket_ids=bucket_ids
        )

        # 2. Fuse.
        fused = rrf_fuse([h.chunk_id for h in vhits], [h.chunk_id for h in khits])
        fused_scores = dict(fused)
        cand_ids = [cid for cid, _ in fused[: self._candidates]]

        if not cand_ids:
            await self._audit(agent_id, namespace, query, request_id, bucket_ids, [])
            return []

        # 3. Hydrate (text + citation pointers), preserve fused order.
        hydrated = await self._store.get_chunks_by_ids(cand_ids)
        ordered = [hydrated[cid] for cid, _ in fused if cid in hydrated]

        # 4. Base score per candidate — reranked (query-aware) or fused order.
        if do_rerank and len(ordered) > 1:
            rr = await self._embedder.rerank(
                query, [h["text"] for h in ordered], top_k=len(ordered)
            )
            base = {
                ordered[r.index]["chunk_id"]: r.score
                for r in rr if r.index < len(ordered)
            }
        else:
            base = {h["chunk_id"]: fused_scores.get(h["chunk_id"], 0.0) for h in ordered}

        # 5. KB-31 — fold recency-weighted feedback into the final order. Applied
        #    multiplicatively so it's scale-robust; soft-fails (feedback must
        #    never break retrieval). Then take the top-K.
        boosts = await self._feedback_boosts(cand_ids)
        scored = [
            (h, base.get(h["chunk_id"], 0.0)
                * max(0.0, 1.0 + _FEEDBACK_WEIGHT * boosts.get(h["chunk_id"], 0.0)))
            for h in ordered
        ]
        scored.sort(key=lambda hs: hs[1], reverse=True)
        results = [self._to_chunk(h, s) for h, s in scored[:top_k]]

        self._cache_put(cache_key, results)
        await self._audit(
            agent_id, namespace, query, request_id, bucket_ids,
            [c.chunk_id for c in results],
        )
        logger.info(
            "kb_retrieve",
            namespace=namespace, agent_id=agent_id,
            buckets=len(bucket_ids or []), candidates=len(cand_ids),
            returned=len(results), reranked=do_rerank,
        )
        return results

    # ── helpers ─────────────────────────────────────────────────────────

    def _cache_get(self, key: tuple) -> list[RetrievedChunk] | None:
        if _CACHE_MAX <= 0 or _CACHE_TTL_S <= 0:
            return None
        hit = self._cache.get(key)
        if hit is None:
            return None
        expires_at, results = hit
        if expires_at < time.monotonic():
            self._cache.pop(key, None)  # expired
            return None
        self._cache.move_to_end(key)  # LRU touch
        return list(results)

    def _cache_put(self, key: tuple, results: list[RetrievedChunk]) -> None:
        if _CACHE_MAX <= 0 or _CACHE_TTL_S <= 0:
            return
        self._cache[key] = (time.monotonic() + _CACHE_TTL_S, list(results))
        self._cache.move_to_end(key)
        while len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)  # evict oldest

    async def _feedback_boosts(self, chunk_ids: list[str]) -> dict[str, float]:
        """KB-31 — recency-weighted feedback per chunk; soft-fails to no boost
        so a feedback hiccup never breaks retrieval."""
        try:
            return await self._store.get_feedback_boosts(chunk_ids)
        except Exception as e:  # noqa: BLE001
            logger.warning("kb_feedback_boost_failed", error=str(e))
            return {}

    @staticmethod
    def _to_chunk(row: dict[str, Any], score: float) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=row["chunk_id"], text=row["text"], score=float(score),
            doc_id=row["doc_id"], title=row.get("title", ""), uri=row.get("uri"),
            namespace=row["namespace"], metadata=row.get("metadata", {}),
        )

    async def _audit(
        self, agent_id: str, namespace: str, query: str,
        request_id: str | None, bucket_ids: list[str] | None,
        returned_chunk_ids: list[str],
    ) -> None:
        try:
            await self._store.record_retrieval(
                agent_id=agent_id or "unknown", namespace=namespace, query=query,
                request_id=request_id, bucket_ids=bucket_ids or [],
                returned_chunk_ids=returned_chunk_ids,
            )
        except Exception as e:  # noqa: BLE001 — audit must never block retrieval
            logger.warning("kb_retrieval_audit_failed", error=str(e))


__all__ = ["Retriever", "RetrievedChunk", "rrf_fuse"]
