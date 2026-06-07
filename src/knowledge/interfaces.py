"""Knowledge-subsystem interfaces + result types (KB-02).

Dependency-free on purpose — importing this module pulls in no psycopg or
fastembed, so the contracts can be unit-tested and type-checked anywhere,
and the concrete implementations import their heavy deps lazily.

The three roles:
  - ``Embedder``     — text → vectors; also reranking (local fastembed in Phase 1).
  - ``VectorStore``  — semantic search over embeddings (pgvector).
  - ``KeywordStore`` — lexical/BM25-style search (Postgres FTS).

Hybrid retrieval (KB-07) fans a query out to the vector + keyword stores,
fuses the two result sets, then reranks via the embedder. Keeping the
stores separate behind one interface each is what lets a single arm be
swapped (pgvector → Qdrant, Postgres FTS → OpenSearch) without touching
the fusion logic above.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class EmbedderUnavailableError(RuntimeError):
    """Raised when an embedder cannot be constructed (missing SDK / model /
    key). Provider-neutral so the subsystem factory can catch it regardless of
    which concrete ``Embedder`` is configured, and soft-fail to a
    no-retrieval state (NFR-007)."""


# ── Result types ────────────────────────────────────────────────────────


@dataclass
class EmbeddingResult:
    """Output of a batch embedding call."""

    vectors: list[list[float]]
    model: str
    input_tokens: int = 0


@dataclass
class VectorHit:
    """A single semantic-search result.

    ``score`` is a SIMILARITY in [0, 1] (higher = closer), normalized from
    the underlying distance metric so the fusion step in KB-07 can compare
    vector and keyword scores on a common-ish scale.
    """

    chunk_id: str
    score: float
    namespace: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KeywordHit:
    """A single lexical-search result. ``score`` is the raw ``ts_rank``
    (relative within one query; not comparable across queries)."""

    chunk_id: str
    score: float
    namespace: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RerankHit:
    """A reranker result. ``index`` points back into the input document
    list the caller passed to ``rerank``; ``score`` is relevance in [0, 1]."""

    index: int
    score: float


# ── Embedder ──────────────────────────────────────────────────────────────


class Embedder(ABC):
    """Turns text into vectors and reranks candidate documents.

    Documents and queries are embedded with different ``input_type`` hints
    by models that support it (bge/e5 bake the instruction into passage vs.
    query embedding) — so ``embed_documents`` and ``embed_query`` are distinct
    methods rather than one ``embed``.
    """

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector dimensionality. MUST match the pgvector column width."""

    @property
    @abstractmethod
    def model(self) -> str:
        """The embedding model id (for audit/logging)."""

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        """Embed a batch of documents for storage."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query for retrieval."""

    @abstractmethod
    async def rerank(
        self, query: str, documents: list[str], top_k: int | None = None
    ) -> list[RerankHit]:
        """Rerank ``documents`` against ``query``; return hits sorted by
        descending relevance, optionally truncated to ``top_k``."""


# ── VectorStore ─────────────────────────────────────────────────────────────


class VectorStore(ABC):
    """Semantic search over stored embeddings, partitioned by namespace.

    The namespace is the isolation boundary (``kb_platform`` in Phase 1;
    ``kb_project_<id>`` in Phase 2). Every method takes it explicitly — the
    store never infers scope, the caller passes it (the executor derives it
    from the Request, the agent can't widen it).
    """

    @abstractmethod
    async def health(self) -> bool:
        """True if the backing engine is reachable. Used by the subsystem's
        soft-fail check (NFR-007)."""

    @abstractmethod
    async def upsert(
        self,
        namespace: str,
        items: list[tuple[str, list[float], dict[str, Any]]],
    ) -> int:
        """Insert/replace ``(chunk_id, vector, metadata)`` rows. Returns the
        count written."""

    @abstractmethod
    async def search(
        self,
        namespace: str,
        query_vector: list[float],
        top_k: int,
        bucket_ids: list[str] | None = None,
        approved_only: bool = True,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorHit]:
        """Top-``top_k`` nearest neighbors in ``namespace``.

        ``bucket_ids`` is the GROUNDING SCOPE (KB-07): when set, only chunks
        whose ``bucket_ids`` overlap are returned — the hard isolation the
        executor enforces (the agent passes the Request's buckets; it cannot
        widen them). ``None`` = whole namespace. ``approved_only`` restricts
        to chunks of approved documents (retrieval never sees pending/
        superseded/purged docs)."""

    @abstractmethod
    async def delete(self, namespace: str, chunk_ids: list[str]) -> int:
        """Remove rows by chunk_id within a namespace. Returns count removed."""


# ── KeywordStore ────────────────────────────────────────────────────────────


class KeywordStore(ABC):
    """Lexical search — the deterministic arm of hybrid retrieval. Catches
    exact-token matches (REQ-IDs, function names, error codes, model ids)
    that embeddings miss. (The L27 lesson applied to retrieval.)"""

    @abstractmethod
    async def health(self) -> bool: ...

    @abstractmethod
    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int,
        bucket_ids: list[str] | None = None,
        approved_only: bool = True,
        filters: dict[str, Any] | None = None,
    ) -> list[KeywordHit]:
        """Top-``top_k`` lexical matches in ``namespace``, bucket-scoped +
        approved-only (see ``VectorStore.search``)."""
