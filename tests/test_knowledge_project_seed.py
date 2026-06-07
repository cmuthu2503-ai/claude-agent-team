"""KB-19 — enhance-existing seeding + the lifecycle hook.

Live against Postgres (fake embedder). Proves ``seed_project_corpus`` ingests
an existing app's text docs into its project namespace (auto-approved,
idempotent, capped), no-ops on a missing/empty tree, and that the
``project.created`` handler seeds from the conventional local source path.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

import src.knowledge.project_lifecycle as plc
from src.knowledge.interfaces import Embedder, EmbeddingResult, RerankHit
from src.knowledge.project_lifecycle import make_kb_project_handler
from src.knowledge.project_seed import seed_project_corpus


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
        return [float(len(text) % 5), 0.5, 1.0] + [0.0] * 381

    async def rerank(self, query, documents, top_k=None):  # type: ignore[no-untyped-def]
        return [RerankHit(index=i, score=1.0) for i in range(len(documents))]


@pytest.fixture
async def kb():
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
        pytest.skip("Postgres not reachable for live seed test")
    store = KnowledgeStore(pool, dimensions=384)
    await store.initialize()
    pipe = IngestionPipeline(store, _FakeEmbedder(), max_tokens=80, overlap_tokens=8)
    try:
        yield store, pipe
    finally:
        await pool.close()


def _write_app_tree(root):  # noqa: ANN001
    (root / "README.md").write_text("# MyApp\nA budgeting tool for small teams.", encoding="utf-8")
    docs = root / "docs"
    docs.mkdir()
    (docs / "domain.md").write_text("Expenses are tagged and reconciled weekly.", encoding="utf-8")
    (root / "notes.txt").write_text("Brand voice: friendly, concise.", encoding="utf-8")
    # Noise that must NOT be ingested.
    (root / "app.py").write_text("print('hello')", encoding="utf-8")
    nm = root / "node_modules"
    nm.mkdir()
    (nm / "junk.md").write_text("vendor noise", encoding="utf-8")


@pytest.mark.asyncio
async def test_seed_ingests_app_docs_approved(kb, tmp_path):
    store, pipe = kb
    pid = f"proj-{uuid.uuid4().hex[:8]}"
    ns = f"kb_project_{pid}"
    _write_app_tree(tmp_path)
    try:
        summary = await seed_project_corpus(
            pipeline=pipe, store=store, project_id=pid, namespace=ns, root=tmp_path)
        assert summary.ingested >= 3  # README + domain.md + notes.txt
        docs = await store.list_documents(ns, status="approved")
        titles = {d.title for d in docs}
        assert {"README.md", "domain.md", "notes.txt"} <= titles
        # Code + vendored noise excluded.
        assert "app.py" not in titles
        assert "junk.md" not in titles

        # Idempotent: a re-run ingests nothing new.
        again = await seed_project_corpus(
            pipeline=pipe, store=store, project_id=pid, namespace=ns, root=tmp_path)
        assert again.ingested == 0 and again.skipped >= 3
    finally:
        await store.purge_project(pid, ns)


@pytest.mark.asyncio
async def test_seed_noops_on_missing_root(kb, tmp_path):
    store, pipe = kb
    pid = f"proj-{uuid.uuid4().hex[:8]}"
    ns = f"kb_project_{pid}"
    summary = await seed_project_corpus(
        pipeline=pipe, store=store, project_id=pid, namespace=ns,
        root=tmp_path / "does-not-exist")
    assert summary.files_seen == 0 and summary.ingested == 0


@pytest.mark.asyncio
async def test_handler_seeds_from_local_source_on_create(kb, tmp_path, monkeypatch):
    store, pipe = kb
    pid = f"proj-{uuid.uuid4().hex[:8]}"
    name = "MyEnhanceApp"
    (tmp_path / name).mkdir()
    (tmp_path / name / "README.md").write_text("# MyEnhanceApp\nExisting docs.", encoding="utf-8")
    monkeypatch.setattr(plc, "_PROJECT_SOURCE_ROOT", tmp_path)

    settings = SimpleNamespace(project_namespace=lambda p: f"kb_project_{p}")
    sub = SimpleNamespace(available=True, settings=settings, knowledge_store=store, pipeline=pipe)
    handler = make_kb_project_handler(sub)
    try:
        await handler("project.created", {"project_id": pid, "name": name})
        docs = await store.list_documents(f"kb_project_{pid}", status="approved")
        assert any(d.title == "README.md" for d in docs)
    finally:
        await store.purge_project(pid, f"kb_project_{pid}")


@pytest.mark.asyncio
async def test_handler_rejects_unsafe_project_name(tmp_path, monkeypatch):
    # Path-traversal / unsafe names must be refused (no seeding attempt). A bare
    # subsystem with no store/pipe would AttributeError if seeding were tried.
    monkeypatch.setattr(plc, "_PROJECT_SOURCE_ROOT", tmp_path)
    sub = SimpleNamespace(available=True, settings=SimpleNamespace(
        project_namespace=lambda p: f"kb_project_{p}"))
    await plc._maybe_seed(sub, "p1", "kb_project_p1", "../../etc")  # must not raise
