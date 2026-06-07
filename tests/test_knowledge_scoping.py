"""KB-15 — namespace scope resolution + per-application isolation.

Pure tests for ``resolve_namespace`` (scope grants) + a LIVE test proving the
in-query isolation guarantee: a retrieval scoped to project A's namespace never
returns project B's chunks (NFR-003).
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from src.knowledge.interfaces import Embedder, EmbeddingResult, RerankHit
from src.knowledge.scoping import resolve_craft_namespace, resolve_namespace
from src.models.base import UNASSIGNED_PROJECT_ID

_SETTINGS = SimpleNamespace(
    platform_namespace="kb_platform",
    project_namespace=lambda p: f"kb_project_{p}",
)


# ── Pure: scope grants ─────────────────────────────────────────────────────


def test_auto_uses_project_namespace_for_real_project():
    assert resolve_namespace(_SETTINGS, "proj-abc", "auto") == "kb_project_proj-abc"


def test_auto_falls_back_to_platform_for_no_project():
    assert resolve_namespace(_SETTINGS, None, "auto") == "kb_platform"
    assert resolve_namespace(_SETTINGS, UNASSIGNED_PROJECT_ID, "auto") == "kb_platform"


def test_default_scope_is_auto():
    assert resolve_namespace(_SETTINGS, "proj-x") == "kb_project_proj-x"


def test_platform_scope_never_reads_project_facts():
    # A craft-only agent stays on the platform namespace even on a project task.
    assert resolve_namespace(_SETTINGS, "proj-x", "platform") == "kb_platform"


def test_project_scope_uses_project_else_platform():
    assert resolve_namespace(_SETTINGS, "proj-x", "project") == "kb_project_proj-x"
    # project-scoped agent on a project-less request degrades to platform.
    assert resolve_namespace(_SETTINGS, None, "project") == "kb_platform"


def test_scope_is_case_insensitive_and_tolerates_blank():
    assert resolve_namespace(_SETTINGS, "p", "PLATFORM") == "kb_platform"
    assert resolve_namespace(_SETTINGS, "p", "") == "kb_project_p"


# ── KB-17: craft namespace (platform craft alongside project facts) ────────


def test_craft_namespace_only_for_auto_project():
    # auto + real project → facts from project, craft from platform.
    assert resolve_craft_namespace(_SETTINGS, "proj-x", "auto") == "kb_platform"


def test_no_craft_namespace_for_strict_or_platform_or_no_project():
    # facts-strict: no separate craft source.
    assert resolve_craft_namespace(_SETTINGS, "proj-x", "project") is None
    # platform scope: primary already IS platform.
    assert resolve_craft_namespace(_SETTINGS, "proj-x", "platform") is None
    # project-less auto: primary already platform → no separate craft.
    assert resolve_craft_namespace(_SETTINGS, None, "auto") is None
    assert resolve_craft_namespace(_SETTINGS, UNASSIGNED_PROJECT_ID, "auto") is None


# ── Live: in-query namespace isolation ─────────────────────────────────────


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
        return [RerankHit(index=i, score=1.0 - i * 0.01) for i in range(len(documents))]


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
        pytest.skip("Postgres not reachable for live scoping test")
    store = KnowledgeStore(pool, dimensions=384)
    await store.initialize()
    emb = _FakeEmbedder()
    pipe = IngestionPipeline(store, emb, max_tokens=80, overlap_tokens=8)
    retr = Retriever(
        emb, PgVectorStore(pool, dimensions=384), PostgresFtsStore(pool), store,
        top_k=10, candidates=40,
    )
    try:
        yield store, pipe, retr
    finally:
        await pool.close()


async def _seed_app(store, pipe, project_id, text):  # noqa: ANN001
    ns = f"kb_project_{project_id}"
    bucket = await store.provision_project(project_id, ns)
    res = await pipe.ingest_text(
        text=text, title=f"{project_id}-doc", source_type="prd", namespace=ns,
        bucket_ids=[bucket.bucket_id], project_id=project_id,
    )
    await store.set_document_status(res.doc_id, "approved")
    return ns, res.doc_id


@pytest.mark.asyncio
async def test_retrieval_is_namespace_isolated_across_apps(kb):
    store, pipe, retr = kb
    pid_a = f"appA-{uuid.uuid4().hex[:8]}"
    pid_b = f"appB-{uuid.uuid4().hex[:8]}"
    ns_a, doc_a = await _seed_app(
        store, pipe, pid_a, "App A is a fintech expense tracker with budgets.")
    ns_b, doc_b = await _seed_app(
        store, pipe, pid_b, "App B is a fintech expense tracker with budgets.")
    try:
        # Same query text; each app's namespace returns ONLY its own doc.
        hits_a = await retr.retrieve("expense tracker budgets", ns_a, agent_id="t")
        assert hits_a and all(h.doc_id == doc_a for h in hits_a)
        assert all(h.doc_id != doc_b for h in hits_a)

        hits_b = await retr.retrieve("expense tracker budgets", ns_b, agent_id="t")
        assert hits_b and all(h.doc_id == doc_b for h in hits_b)
        assert all(h.doc_id != doc_a for h in hits_b)
    finally:
        await store.purge_project(pid_a, ns_a)
        await store.purge_project(pid_b, ns_b)
