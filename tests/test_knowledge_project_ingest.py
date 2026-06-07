"""KB-14 — auto-ingest of approved project artifacts into the project KB.

Live against Postgres (fake embedder) with a fake state store. Proves each
event path lands an approved document in the project's kb_project_<id>
namespace, that unassigned-project artifacts are skipped, and that the handler
no-ops when the subsystem is down.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

import src.knowledge.project_ingest as pi
from src.knowledge.interfaces import Embedder, EmbeddingResult, RerankHit
from src.knowledge.project_ingest import make_kb_artifact_ingest_handler
from src.models.base import UNASSIGNED_PROJECT_ID


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


class _FakeState:
    """Only the methods the ingest handler calls."""

    def __init__(self, *, artifact=None, tasks=None, request=None):  # noqa: ANN001
        self._artifact = artifact
        self._tasks = tasks or []
        self._request = request

    async def get_artifact(self, project_id, kind):  # noqa: ANN001
        return self._artifact

    async def list_tasks_for_project(self, project_id, list_status=None, list_version=None):  # noqa: ANN001
        return self._tasks

    async def get_request(self, request_id):  # noqa: ANN001
        return self._request


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
        pytest.skip("Postgres not reachable for live KB ingest test")
    store = KnowledgeStore(pool, dimensions=384)
    await store.initialize()
    pipe = IngestionPipeline(store, _FakeEmbedder(), max_tokens=80, overlap_tokens=8)
    settings = SimpleNamespace(project_namespace=lambda p: f"kb_project_{p}")
    sub = SimpleNamespace(available=True, settings=settings, knowledge_store=store, pipeline=pipe)
    try:
        yield sub, store
    finally:
        await pool.close()


async def _approved_docs(store, project_id):  # noqa: ANN001
    ns = f"kb_project_{project_id}"
    docs = await store.list_documents(ns, status="approved")
    return ns, docs


# ── PRD / spec / tasks ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prd_finalized_ingests_approved(kb):
    sub, store = kb
    pid = f"proj-{uuid.uuid4().hex[:8]}"
    state = _FakeState(artifact=SimpleNamespace(
        content="# PRD\nThe app lets users track expenses with categories and budgets."))
    handler = make_kb_artifact_ingest_handler(sub, state)
    try:
        await handler("project.prd_finalized", {"project_id": pid})
        ns, docs = await _approved_docs(store, pid)
        assert any(d.title == "PRD" and d.status == "approved" for d in docs)
        # And it's grounded in the project's namespace, not platform.
        assert all(d.namespace == ns for d in docs)
    finally:
        await store.purge_project(pid, f"kb_project_{pid}")


@pytest.mark.asyncio
async def test_tasks_finalized_renders_and_ingests(kb):
    sub, store = kb
    pid = f"proj-{uuid.uuid4().hex[:8]}"
    tasks = [
        SimpleNamespace(ordinal=1, title="Build login", task_type="feature_request",
                        priority="high", description="JWT auth with roles"),
        SimpleNamespace(ordinal=2, title="Add dashboard", task_type="feature_request",
                        priority="medium", description="charts + filters"),
    ]
    state = _FakeState(tasks=tasks)
    handler = make_kb_artifact_ingest_handler(sub, state)
    try:
        await handler("project.tasks_finalized", {"project_id": pid})
        _, docs = await _approved_docs(store, pid)
        assert any(d.title == "Task List" for d in docs)
    finally:
        await store.purge_project(pid, f"kb_project_{pid}")


# ── research (disk) + commit (manifest) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_research_publish_ingests_from_disk(kb, tmp_path, monkeypatch):
    sub, store = kb
    pid = f"proj-{uuid.uuid4().hex[:8]}"
    req_id = "REQ-ABC123"
    folder = tmp_path / f"{req_id}-vector-db-research"
    folder.mkdir()
    (folder / "summary.md").write_text(
        "# Research\nPostgres + pgvector beats sqlite-vec for this workload.",
        encoding="utf-8",
    )
    monkeypatch.setattr(pi, "_RESEARCH_ROOT", tmp_path)
    state = _FakeState(request=SimpleNamespace(project_id=pid))
    handler = make_kb_artifact_ingest_handler(sub, state)
    try:
        await handler("research_publish.completed",
                      {"request_id": req_id, "files": ["summary.md"]})
        _, docs = await _approved_docs(store, pid)
        assert any("Research" in d.title for d in docs)
    finally:
        await store.purge_project(pid, f"kb_project_{pid}")


@pytest.mark.asyncio
async def test_commit_ingests_manifest(kb):
    sub, store = kb
    pid = f"proj-{uuid.uuid4().hex[:8]}"
    state = _FakeState(request=SimpleNamespace(project_id=pid))
    handler = make_kb_artifact_ingest_handler(sub, state)
    try:
        await handler("code_commit.completed", {
            "request_id": "REQ-XYZ", "commit_sha": "abc123",
            "files": ["src/foo.py", "frontend/src/Bar.tsx"],
        })
        _, docs = await _approved_docs(store, pid)
        assert any(d.title.startswith("Commit manifest") for d in docs)
    finally:
        await store.purge_project(pid, f"kb_project_{pid}")


# ── Guards ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unassigned_project_skipped(kb):
    sub, store = kb
    state = _FakeState(request=SimpleNamespace(project_id=UNASSIGNED_PROJECT_ID))
    handler = make_kb_artifact_ingest_handler(sub, state)
    await handler("code_commit.completed",
                  {"request_id": "REQ-1", "files": ["a.py"]})
    ns, docs = await _approved_docs(store, UNASSIGNED_PROJECT_ID)
    assert docs == []  # nothing ingested for the catch-all project


@pytest.mark.asyncio
async def test_noop_when_unavailable():
    sub = SimpleNamespace(available=False)
    handler = make_kb_artifact_ingest_handler(sub, _FakeState())
    # Must not raise.
    await handler("project.prd_finalized", {"project_id": "x"})
