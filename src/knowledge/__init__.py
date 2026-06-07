"""Agentic Knowledge Base subsystem (KB-02).

The retrieval substrate for the platform's knowledge base. Three pluggable
interfaces — ``Embedder``, ``VectorStore``, ``KeywordStore`` — with concrete
implementations on a LOCAL fastembed embedder (ONNX, in-process) and
PostgreSQL + pgvector (vector + keyword search).

Engine-agnostic by design: swapping pgvector → Qdrant or the local embedder →
a hosted one is a new implementation behind the same interface, not a rewrite
(NFR-003 / NFR-007). The subsystem soft-fails — if Postgres is unreachable or
the embedding model can't load, ``build_knowledge_subsystem`` returns an
``available=False`` subsystem and the platform keeps booting.
"""

from src.knowledge.interfaces import (
    Embedder,
    EmbeddingResult,
    KeywordHit,
    KeywordStore,
    RerankHit,
    VectorHit,
    VectorStore,
)
from src.knowledge.subsystem import KnowledgeSubsystem, build_knowledge_subsystem

__all__ = [
    "Embedder",
    "EmbeddingResult",
    "KeywordHit",
    "KeywordStore",
    "RerankHit",
    "VectorHit",
    "VectorStore",
    "KnowledgeSubsystem",
    "build_knowledge_subsystem",
]
