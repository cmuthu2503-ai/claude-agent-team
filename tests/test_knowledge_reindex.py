"""KB-06 — platform corpus discovery + reindex.

corpus discovery is pure (temp dir). reindex runs live against Postgres with
a fake embedder + a temp corpus, so the walk → ingest → auto-approve → summary
flow is exercised without a model download or touching the real docs.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from src.knowledge.corpus import platform_corpus_files, relative_uri
from src.knowledge.interfaces import Embedder, EmbeddingResult, RerankHit

# ── Corpus discovery (pure) ──────────────────────────────────────────────


def test_corpus_lists_claude_and_docs(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("# conventions")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "architecture.md").write_text("# arch")
    (tmp_path / "docs" / "cross-cutting.md").write_text("# xcut")
    (tmp_path / "docs" / "notes.txt").write_text("not markdown")  # excluded by glob
    files = platform_corpus_files(tmp_path)
    names = {p.name for p in files}
    assert "CLAUDE.md" in names
    assert "architecture.md" in names and "cross-cutting.md" in names
    assert "notes.txt" not in names


def test_corpus_excludes_mockups_and_pending(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "mockups").mkdir()
    (tmp_path / "docs" / "good.md").write_text("# good")
    (tmp_path / "docs" / "mockups" / "x.md").write_text("# mock")  # not in docs/*.md anyway
    (tmp_path / "docs" / "lessons.pending.md").write_text("# pending")
    files = {p.name for p in platform_corpus_files(tmp_path)}
    assert "good.md" in files
    assert "lessons.pending.md" not in files  # excluded by substring


def test_corpus_missing_files_are_skipped(tmp_path: Path):
    # no CLAUDE.md, no docs/ → empty, no error
    assert platform_corpus_files(tmp_path) == []


def test_relative_uri(tmp_path: Path):
    p = tmp_path / "docs" / "a.md"
    p.parent.mkdir()
    p.write_text("x")
    assert relative_uri(tmp_path, p) == "docs/a.md"


# ── Live reindex ─────────────────────────────────────────────────────────


class _FakeEmbedder(Embedder):
    @property
    def dimensions(self) -> int:
        return 384

    @property
    def model(self) -> str:
        return "fake-3"

    async def embed_documents(self, texts):  # type: ignore[no-untyped-def]
        return EmbeddingResult(
            vectors=[[float(len(t) % 5), 0.5, 1.0] + [0.0] * 381 for t in texts],
            model="fake-3",
        )

    async def embed_query(self, text):  # type: ignore[no-untyped-def]
        return [1.0, 0.5, 1.0] + [0.0] * 381

    async def rerank(self, query, documents, top_k=None):  # type: ignore[no-untyped-def]
        return [RerankHit(index=i, score=1.0) for i in range(len(documents))]


@pytest.fixture
async def live(tmp_path: Path):
    from src.knowledge.ingest import IngestionPipeline
    from src.knowledge.pg import open_pool
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
        pytest.skip("Postgres not reachable for live reindex test")
    store = KnowledgeStore(pool, dimensions=384)
    await store.initialize()
    pipe = IngestionPipeline(store, _FakeEmbedder(), max_tokens=80, overlap_tokens=10)
    try:
        yield pipe, store, pool
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_reindex_ingests_and_is_idempotent(live, tmp_path: Path):
    from src.knowledge.reindex import reindex_platform

    pipe, store, _ = live
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    # build a temp corpus
    (tmp_path / "CLAUDE.md").write_text("# Conventions\n\nRestart containers after edits.")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "architecture.md").write_text(
        "# Architecture\n\n## Lifecycle\n\nThe orchestrator dispatches the workflow."
    )

    s1 = await reindex_platform(pipeline=pipe, store=store, root=tmp_path, namespace=ns)
    bucket_id = s1.bucket_id
    try:
        assert s1.files_seen == 2 and s1.ingested == 2 and s1.skipped == 0
        assert s1.chunks >= 2 and s1.failures == []

        # docs are auto-approved + tagged into the Platform bucket
        docs = await store.list_documents(ns)
        assert len(docs) == 2
        assert all(d.status == "approved" for d in docs)
        for d in docs:
            assert bucket_id in await store.get_document_buckets(d.doc_id)

        # second run: unchanged content → all skipped (idempotent)
        s2 = await reindex_platform(pipeline=pipe, store=store, root=tmp_path, namespace=ns)
        assert s2.ingested == 0 and s2.skipped == 2

        # change a file → only it re-ingests
        (tmp_path / "docs" / "architecture.md").write_text("# Architecture\n\nReworked content.")
        s3 = await reindex_platform(pipeline=pipe, store=store, root=tmp_path, namespace=ns)
        assert s3.ingested == 1 and s3.skipped == 1
    finally:
        for d in await store.list_documents(ns):
            await store.purge_document(d.doc_id)
        # leave the shared system "Platform" bucket (idempotent across tests)
