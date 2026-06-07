"""FastEmbedEmbedder — local, in-process embeddings (KB-13a).

The platform's default embedder. Runs an ONNX sentence-embedding model
(``BAAI/bge-small-en-v1.5``, 384-dim) entirely inside the backend container
via `fastembed` — **no external account, no API key, no per-call cost**. The
model downloads once from HuggingFace on first use and is cached on a named
volume thereafter.

Swappable like any ``Embedder``: point ``knowledge_base.embeddings`` at a
different provider/model behind the same interface (NFR-007). ``fastembed`` is
imported lazily so this module loads without the dep, and the constructor
raises ``EmbedderUnavailableError`` when the library/model can't be loaded —
the subsystem factory catches it and soft-fails to no-retrieval.

fastembed is synchronous (CPU ONNX inference), so every call is offloaded to a
worker thread via ``asyncio.to_thread`` to keep the event loop responsive.

Reranking is **identity** in Phase 1 — the hybrid RRF fusion of the vector +
keyword arms is the ranking signal, and ``retrieval.rerank`` is left ``false``
so this is never invoked. A local cross-encoder reranker can drop in later
without touching the interface.
"""

from __future__ import annotations

import asyncio

import structlog

from src.knowledge.interfaces import (
    Embedder,
    EmbedderUnavailableError,
    EmbeddingResult,
    RerankHit,
)

logger = structlog.get_logger()

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DIM = 384


class FastEmbedEmbedder(Embedder):
    """Local ONNX embeddings via fastembed. No key, no network at query time."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIM,
        cache_dir: str | None = None,
    ) -> None:
        try:
            from fastembed import TextEmbedding
        except Exception as e:  # noqa: BLE001
            raise EmbedderUnavailableError(
                f"fastembed not installed: {e}"
            ) from e
        try:
            # Constructing the model triggers the one-time download (cached).
            self._embed = TextEmbedding(model_name=model, cache_dir=cache_dir)
        except Exception as e:  # noqa: BLE001
            raise EmbedderUnavailableError(
                f"fastembed model '{model}' failed to load: {e}"
            ) from e
        self._model = model
        self._dim = dimensions
        logger.info("fastembed_embedder_ready", model=model, dimensions=dimensions)

    @property
    def dimensions(self) -> int:
        return self._dim

    @property
    def model(self) -> str:
        return self._model

    async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self._model, input_tokens=0)
        vectors = await asyncio.to_thread(self._embed_passages, texts)
        return EmbeddingResult(vectors=vectors, model=self._model, input_tokens=0)

    async def embed_query(self, text: str) -> list[float]:
        vectors = await asyncio.to_thread(self._embed_queries, [text])
        return vectors[0]

    async def rerank(
        self, query: str, documents: list[str], top_k: int | None = None
    ) -> list[RerankHit]:
        # Identity rerank — preserve input order with monotonically
        # decreasing pseudo-scores. RRF fusion upstream already ranks.
        n = len(documents)
        hits = [RerankHit(index=i, score=1.0 - i / max(n, 1)) for i in range(n)]
        return hits[:top_k] if top_k else hits

    # ── thread-offloaded sync workers ─────────────────────────────────────

    def _embed_passages(self, texts: list[str]) -> list[list[float]]:
        # fastembed bakes the model's passage instruction into .passage_embed
        # when supported, falling back to .embed otherwise. Returns an iterable
        # of numpy arrays.
        embed_fn = getattr(self._embed, "passage_embed", self._embed.embed)
        return [[float(x) for x in vec] for vec in embed_fn(texts)]

    def _embed_queries(self, texts: list[str]) -> list[list[float]]:
        embed_fn = getattr(self._embed, "query_embed", self._embed.embed)
        return [[float(x) for x in vec] for vec in embed_fn(texts)]
