"""KB-02 — knowledge subsystem: settings, soft-fail, and live pgvector/FTS.

Split into:
  - Pure unit tests (settings resolution, soft-fail paths) — no DB/network.
  - Live integration tests against the running Postgres+pgvector, gated on
    reachability so they skip cleanly in a DB-less CI. They use a TEMP table
    (not KB-03's kb_chunks) so KB-02 is testable independently of the schema.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from src.knowledge.interfaces import Embedder, KeywordStore, VectorStore
from src.knowledge.settings import KnowledgeSettings
from src.knowledge.subsystem import build_knowledge_subsystem

# ── Helpers ──────────────────────────────────────────────────────────────


def _config(kb_block: dict | None) -> SimpleNamespace:
    """Fake ConfigLoader with a .project dict."""
    project = {}
    if kb_block is not None:
        project["knowledge_base"] = kb_block
    return SimpleNamespace(project=project)


def _full_kb_block() -> dict:
    return {
        "enabled": True,
        "postgres": {
            "host_env": "KB_PG_HOST", "port_env": "KB_PG_PORT",
            "user_env": "KB_PG_USER", "password_ref": "kb_pg_password",
            "db_env": "KB_PG_DB", "pool_min": 1, "pool_max": 5,
        },
        "embeddings": {
            "provider": "fastembed", "model": "BAAI/bge-small-en-v1.5",
            "dimensions": 384,
        },
        "retrieval": {"top_k": 8, "hybrid_candidates": 40, "rerank": False},
        "namespaces": {"platform": "kb_platform"},
    }


class _StubEmbedder:
    """Instant-construct stand-in for FastEmbedEmbedder so soft-fail tests
    don't download a real ONNX model. Shape-compatible with Embedder."""

    def __init__(self, *a, **k):  # noqa: ANN002, ANN003
        pass

    @property
    def dimensions(self) -> int:
        return 384

    @property
    def model(self) -> str:
        return "stub"

    async def embed_documents(self, texts):  # noqa: ANN001
        from src.knowledge.interfaces import EmbeddingResult
        return EmbeddingResult(vectors=[[0.0] * 384 for _ in texts], model="stub")

    async def embed_query(self, text):  # noqa: ANN001
        return [0.0] * 384

    async def rerank(self, query, documents, top_k=None):  # noqa: ANN001
        return []


# ── Settings ───────────────────────────────────────────────────────────────


def test_settings_defaults_when_block_missing():
    s = KnowledgeSettings.from_config(_config(None))
    assert s.enabled is False
    # Defaults still resolve so dsn/props don't explode.
    assert s.pg_db == "agentteam_kb"
    assert s.dimensions == 384            # KB-13a — local bge-small default
    assert s.embed_provider == "fastembed"
    assert s.embed_model == "BAAI/bge-small-en-v1.5"
    assert s.platform_namespace == "kb_platform"


def test_settings_reads_block(monkeypatch):
    monkeypatch.setenv("KB_PG_HOST", "pg-test")
    monkeypatch.setenv("KB_PG_PORT", "5599")
    monkeypatch.setenv("KB_PG_USER", "u")
    monkeypatch.setenv("KB_PG_DB", "d")
    monkeypatch.setenv("KB_PG_PASSWORD", "secret")
    s = KnowledgeSettings.from_config(_config(_full_kb_block()))
    assert s.enabled is True
    assert s.pg_host == "pg-test"
    assert s.pg_port == 5599
    assert "host=pg-test" in s.dsn
    assert "dbname=d" in s.dsn
    assert s.pool_max == 5


def test_settings_reads_embeddings_block():
    s = KnowledgeSettings.from_config(_config(_full_kb_block()))
    # Local embedder — no API key involved at all.
    assert s.embed_provider == "fastembed"
    assert s.dimensions == 384
    assert s.rerank_enabled is False


# ── Soft-fail (NFR-007) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_when_not_enabled():
    sub = await build_knowledge_subsystem(_config({"enabled": False}))
    assert sub.available is False
    assert "enabled" in sub.reason
    assert sub.embedder is None and sub.vector_store is None


@pytest.mark.asyncio
async def test_disabled_when_unknown_provider():
    block = _full_kb_block()
    block["embeddings"]["provider"] = "bogus_provider"
    sub = await build_knowledge_subsystem(_config(block))
    assert sub.available is False
    assert "unknown embeddings provider" in sub.reason


@pytest.mark.asyncio
async def test_disabled_when_postgres_unreachable(monkeypatch):
    # Stub the local embedder so it constructs instantly (no model download),
    # letting the test reach + fail the Postgres check deterministically.
    import src.knowledge.embedder_fastembed as fe

    monkeypatch.setattr(fe, "FastEmbedEmbedder", _StubEmbedder)
    monkeypatch.setenv("KB_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("KB_PG_PORT", "1")  # nothing listens on port 1
    block = _full_kb_block()
    sub = await build_knowledge_subsystem(_config(block))
    assert sub.available is False
    assert "postgres unreachable" in sub.reason
    await sub.aclose()  # safe even when pool never opened


# ── Live integration (gated on Postgres reachability) ───────────────────────


@pytest.mark.asyncio
async def test_live_vector_and_keyword_roundtrip():
    """End-to-end against the real pgvector engine using a TEMP table.
    Skips when Postgres isn't reachable (e.g. unit-only CI)."""
    from src.knowledge.pg import open_pool
    from src.knowledge.store_pgfts import PostgresFtsStore
    from src.knowledge.store_pgvector import PgVectorStore

    dsn = (
        f"host={os.getenv('KB_PG_HOST', 'postgres')} "
        f"port={os.getenv('KB_PG_PORT', '5432')} "
        f"user={os.getenv('KB_PG_USER', 'agentteam')} "
        f"password={os.getenv('KB_PG_PASSWORD', 'change-me-in-dev')} "
        f"dbname={os.getenv('KB_PG_DB', 'agentteam_kb')}"
    )
    try:
        pool = await open_pool(dsn, 1, 3)
    except Exception:
        pytest.skip("Postgres not reachable for live KB test")

    table = f"_kbtest_{uuid.uuid4().hex[:8]}"
    try:
        async with pool.connection() as conn:
            await conn.execute(
                f"CREATE TABLE {table} ("
                f"  chunk_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, "
                f"  embedding vector(3), text TEXT, metadata JSONB DEFAULT '{{}}')"
            )

        vstore = PgVectorStore(pool, table=table, dimensions=3)
        kstore = PostgresFtsStore(pool, table=table)

        # Seed: vectors + text. We need text for the FTS arm, so write via a
        # parameterized insert (the vector store's upsert doesn't carry text
        # in KB-02). Vectors bound as pgvector text literals.
        seed = [
            ("a", "kb_platform", "[1,0,0]", "the supervisor deploys to staging", '{"src":"doc1"}'),
            ("b", "kb_platform", "[0,1,0]", "quality gate blocks the request", "{}"),
            ("c", "other_ns", "[1,0,0]", "isolated namespace row", "{}"),
        ]
        async with pool.connection() as conn:
            await conn.cursor().executemany(
                f"INSERT INTO {table} "
                f"(chunk_id, namespace, embedding, text, metadata) "
                f"VALUES (%s, %s, %s::vector, %s, %s::jsonb)",
                seed,
            )

        # Vector search: query close to 'a'. Must return a first, and must
        # NOT leak the 'other_ns' row (namespace isolation). approved_only=False
        # because the temp table has no kb_documents rows to join.
        hits = await vstore.search("kb_platform", [0.9, 0.1, 0.0], top_k=5,
                                   approved_only=False)
        ids = [h.chunk_id for h in hits]
        assert ids[0] == "a"
        assert "c" not in ids
        assert hits[0].score > hits[-1].score  # similarity ordering
        assert hits[0].metadata.get("src") == "doc1"

        # Keyword search: exact term 'supervisor' → row a only.
        khits = await kstore.search("kb_platform", "supervisor", top_k=5,
                                    approved_only=False)
        assert [h.chunk_id for h in khits] == ["a"]
        # Namespace isolation on the keyword arm too.
        khits_other = await kstore.search("other_ns", "isolated", top_k=5,
                                          approved_only=False)
        assert [h.chunk_id for h in khits_other] == ["c"]

        # upsert() path + delete().
        n = await vstore.upsert("kb_platform", [("d", [0.0, 0.0, 1.0], {"k": "v"})])
        assert n == 1
        deleted = await vstore.delete("kb_platform", ["d"])
        assert deleted == 1

        # health()
        assert await vstore.health() is True
        assert await kstore.health() is True
    finally:
        async with pool.connection() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS {table}")
        await pool.close()


# ── Type sanity ──────────────────────────────────────────────────────────


def test_impls_satisfy_interfaces():
    """Concrete classes are registered as their ABCs (import-time check that
    nothing drifted out of the contract)."""
    from src.knowledge.store_pgfts import PostgresFtsStore
    from src.knowledge.store_pgvector import PgVectorStore

    assert issubclass(PgVectorStore, VectorStore)
    assert issubclass(PostgresFtsStore, KeywordStore)
    # FastEmbedEmbedder imports fastembed lazily in __init__, so the class
    # itself is importable and is an Embedder subclass without the dep.
    from src.knowledge.embedder_fastembed import FastEmbedEmbedder

    assert issubclass(FastEmbedEmbedder, Embedder)
