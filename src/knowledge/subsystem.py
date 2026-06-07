"""Knowledge subsystem factory + lifecycle (KB-02).

``build_knowledge_subsystem(config)`` is the single entry point the backend
lifespan calls. It wires the local fastembed embedder + the pgvector/FTS stores over a
shared async pool — and **soft-fails** (NFR-007): if the KB is disabled, the
embedding key is missing, or Postgres is unreachable, it returns an
``available=False`` subsystem with a human-readable ``reason`` and the backend
boots normally (agents simply run without retrieval). This mirrors the PAM
``model_resolver_init_failed_falling_back`` posture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from src.knowledge.interfaces import Embedder, KeywordStore, VectorStore
from src.knowledge.settings import KnowledgeSettings

logger = structlog.get_logger()


@dataclass
class KnowledgeSubsystem:
    """What the rest of the platform holds. When ``available`` is False the
    stores/embedder/pipeline are None and callers must degrade to
    no-retrieval / no-ingest."""

    available: bool
    reason: str
    settings: KnowledgeSettings
    embedder: Embedder | None = None
    vector_store: VectorStore | None = None
    keyword_store: KeywordStore | None = None
    # KB-06: the relational store + the ingestion engine, ready to use.
    # ``knowledge_store`` has had its schema applied at build time.
    knowledge_store: Any = None        # KnowledgeStore
    pipeline: Any = None               # IngestionPipeline
    retriever: Any = None              # Retriever (KB-07)
    ingest_dispatcher: Any = None      # IngestionDispatcher (KB-33)
    _pool: Any = None

    async def aclose(self) -> None:
        """Close the connection pool on shutdown. Safe to call when the
        subsystem never came up (pool is None)."""
        if self._pool is not None:
            try:
                await self._pool.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("kb_pool_close_failed", error=str(e))


def _disabled(settings: KnowledgeSettings, reason: str) -> KnowledgeSubsystem:
    logger.warning("knowledge_subsystem_unavailable", reason=reason)
    return KnowledgeSubsystem(available=False, reason=reason, settings=settings)


async def build_knowledge_subsystem(config: Any) -> KnowledgeSubsystem:
    """Construct the subsystem from config. Never raises — returns a
    degraded subsystem on any failure so the platform keeps booting."""
    settings = KnowledgeSettings.from_config(config)

    if not settings.enabled:
        return _disabled(settings, "knowledge_base.enabled is false")

    # 1. Embedder. Default is the LOCAL fastembed embedder (no key, no cost —
    #    runs ONNX in-process). ``provider`` selects the impl; any failure to
    #    construct → degrade (ingest/retrieval off, platform still boots).
    embedder: Embedder | None = None
    try:
        from src.knowledge.interfaces import EmbedderUnavailableError

        try:
            provider = (settings.embed_provider or "fastembed").lower()
            if provider in ("fastembed", "local", "bge", "onnx"):
                from src.knowledge.embedder_fastembed import FastEmbedEmbedder

                embedder = FastEmbedEmbedder(
                    model=settings.embed_model,
                    dimensions=settings.dimensions,
                    cache_dir=settings.embed_cache_dir or None,
                )
            else:
                return _disabled(
                    settings,
                    f"unknown embeddings provider {provider!r} "
                    "(supported: fastembed)",
                )
        except EmbedderUnavailableError as e:
            return _disabled(settings, f"embedder unavailable: {e}")
    except Exception as e:  # noqa: BLE001
        return _disabled(settings, f"embedder import failed: {e}")

    # 2. Postgres pool — unreachable → degrade.
    try:
        from src.knowledge.pg import open_pool
        from src.knowledge.store_pgfts import PostgresFtsStore
        from src.knowledge.store_pgvector import PgVectorStore

        pool = await open_pool(settings.dsn, settings.pool_min, settings.pool_max)
    except Exception as e:  # noqa: BLE001
        return _disabled(settings, f"postgres unreachable: {e}")

    vector_store = PgVectorStore(pool, dimensions=settings.dimensions)
    keyword_store = PostgresFtsStore(pool)

    # 3. Confirm the stores can actually serve (health probe).
    if not await vector_store.health():
        await pool.close()
        return _disabled(settings, "vector store health check failed")

    # 4. KB-06 — relational store (schema applied here) + ingestion engine.
    #    KB-07 — the retriever (hybrid + rerank, bucket-scoped).
    try:
        from src.knowledge.ingest import IngestionPipeline
        from src.knowledge.ingest_dispatch import IngestionDispatcher
        from src.knowledge.retrieval import Retriever
        from src.knowledge.store import KnowledgeStore

        knowledge_store = KnowledgeStore(pool, dimensions=settings.dimensions)
        await knowledge_store.initialize()  # idempotent KNOWLEDGE_SCHEMA_SQL
        pipeline = IngestionPipeline(knowledge_store, embedder)
        ingest_dispatcher = IngestionDispatcher(mode=settings.ingest_mode)
        retriever = Retriever(
            embedder, vector_store, keyword_store, knowledge_store,
            top_k=settings.top_k, candidates=settings.hybrid_candidates,
            rerank=settings.rerank_enabled,
        )
    except Exception as e:  # noqa: BLE001
        await pool.close()
        return _disabled(settings, f"knowledge store init failed: {e}")

    logger.info(
        "knowledge_subsystem_ready",
        embed_model=settings.embed_model,
        dimensions=settings.dimensions,
        namespace=settings.platform_namespace,
        rerank=settings.rerank_enabled,
        ingest_mode=settings.ingest_mode,
    )
    return KnowledgeSubsystem(
        available=True,
        reason="ok",
        settings=settings,
        embedder=embedder,
        vector_store=vector_store,
        keyword_store=keyword_store,
        knowledge_store=knowledge_store,
        pipeline=pipeline,
        retriever=retriever,
        ingest_dispatcher=ingest_dispatcher,
        _pool=pool,
    )
