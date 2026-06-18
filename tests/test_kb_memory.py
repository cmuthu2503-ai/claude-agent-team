"""KB-24 — episodic memory store + auto-capture.

Two layers, both live against the running Postgres+pgvector (gated on
reachability, skip cleanly otherwise):

1. ``KnowledgeStore`` memory CRUD — insert / list / count / idempotent
   re-capture / namespace isolation / purge.
2. ``make_memory_capture_handler`` — the EventEmitter handler writes one
   ``episode`` row per completed/failed Request into ``mem_project_<id>``,
   skips the unassigned project, and soft-fails when the subsystem is down.

The load-bearing guarantees: memory is namespace-isolated exactly like the KB
(App A can't read App B's episodes), capture is idempotent across the
orchestrator's repeated ``request.failed`` emits, and a KB hiccup never
escapes the handler.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from src.knowledge.memory_capture import make_memory_capture_handler
from src.knowledge.models import AgentMemory
from src.knowledge.pg import open_pool
from src.knowledge.settings import KnowledgeSettings
from src.knowledge.store import KnowledgeStore
from src.models.base import UNASSIGNED_PROJECT_ID


def _dsn() -> str:
    return (
        f"host={os.getenv('KB_PG_HOST', 'postgres')} "
        f"port={os.getenv('KB_PG_PORT', '5432')} "
        f"user={os.getenv('KB_PG_USER', 'agentteam')} "
        f"password={os.getenv('KB_PG_PASSWORD', 'change-me-in-dev')} "
        f"dbname={os.getenv('KB_PG_DB', 'agentteam_kb')}"
    )


@pytest.fixture
async def store():
    try:
        pool = await open_pool(_dsn(), 1, 4)
    except Exception:
        pytest.skip("Postgres not reachable for live KB memory test")
    s = KnowledgeStore(pool, dimensions=384)
    await s.initialize()
    try:
        yield s, pool
    finally:
        await pool.close()


def _mem(ns: str, *, text: str = "did a thing", outcome: str = "success",
         request_id: str | None = None, content_hash: str | None = None) -> AgentMemory:
    return AgentMemory(
        memory_id=f"mem-{uuid.uuid4().hex[:10]}",
        namespace=ns, agent_id="orchestrator", kind="episode",
        text=text, outcome=outcome, request_id=request_id,
        project_id="P-1", content_hash=content_hash,
        embedding=[0.1, 0.2, 0.3] + [0.0] * 381,
    )


# ── Store CRUD ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_and_list_memory(store):
    s, _ = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    mid = await s.insert_memory(_mem(ns, text="shipped the login page"))
    assert mid.startswith("mem-")
    rows = await s.list_memory(ns)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "episode"
    assert r["text"] == "shipped the login page"
    assert r["outcome"] == "success"
    assert r["unvetted"] is True
    assert await s.count_memory(ns) == 1


@pytest.mark.asyncio
async def test_capture_is_idempotent_on_content_hash(store):
    s, _ = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    h = f"hash-{uuid.uuid4().hex[:8]}"
    first = await s.insert_memory(_mem(ns, content_hash=h))
    second = await s.insert_memory(_mem(ns, content_hash=h, text="DIFFERENT"))
    # Same hash → no duplicate row; the first id is returned again.
    assert first == second
    assert await s.count_memory(ns) == 1


@pytest.mark.asyncio
async def test_memory_namespace_isolation(store):
    s, _ = store
    ns_a = f"mem_a_{uuid.uuid4().hex[:6]}"
    ns_b = f"mem_b_{uuid.uuid4().hex[:6]}"
    await s.insert_memory(_mem(ns_a, text="App A episode"))
    await s.insert_memory(_mem(ns_b, text="App B episode"))
    rows_a = await s.list_memory(ns_a)
    assert len(rows_a) == 1 and rows_a[0]["text"] == "App A episode"
    # App A's read structurally cannot see App B's episode.
    assert all(r["text"] != "App B episode" for r in rows_a)


@pytest.mark.asyncio
async def test_purge_project_deletes_memory(store):
    s, _ = store
    pid = f"PURGE-{uuid.uuid4().hex[:6]}"
    mem_ns = f"mem_project_{pid}"
    await s.insert_memory(_mem(mem_ns, text="to be forgotten"))
    assert await s.count_memory(mem_ns) == 1
    counts = await s.purge_project(pid, f"kb_project_{pid}", memory_namespace=mem_ns)
    assert counts["memory"] == 1
    assert await s.count_memory(mem_ns) == 0


# ── Capture handler ──────────────────────────────────────────────────────────


def _subsystem(store_obj, *, available: bool = True):
    settings = SimpleNamespace(memory_namespace=lambda pid: f"mem_project_{pid}")
    return SimpleNamespace(
        available=available, settings=settings,
        knowledge_store=store_obj,
        embedder=None,  # capture degrades to a None embedding — still stored
    )


class _FakeState:
    def __init__(self, request):  # noqa: ANN001
        self._request = request

    async def get_request(self, request_id):  # noqa: ANN001
        if self._request and self._request.request_id == request_id:
            return self._request
        return None


@pytest.mark.asyncio
async def test_handler_captures_completed_request(store):
    s, _ = store
    pid = f"CAP-{uuid.uuid4().hex[:6]}"
    req = SimpleNamespace(
        request_id="REQ-CAP-1", description="Build the dashboard",
        task_type=SimpleNamespace(value="feature"), project_id=pid,
    )
    handler = make_memory_capture_handler(_subsystem(s), _FakeState(req))
    await handler("request.completed", {"request_id": "REQ-CAP-1", "result": "done"})

    rows = await s.list_memory(f"mem_project_{pid}")
    assert len(rows) == 1
    assert rows[0]["outcome"] == "success"
    assert "Build the dashboard" in rows[0]["text"]
    assert rows[0]["request_id"] == "REQ-CAP-1"

    # Re-fire (orchestrator emits from several paths) → still one row.
    await handler("request.completed", {"request_id": "REQ-CAP-1", "result": "done"})
    assert await s.count_memory(f"mem_project_{pid}") == 1


@pytest.mark.asyncio
async def test_handler_captures_failed_request(store):
    s, _ = store
    pid = f"CAP-{uuid.uuid4().hex[:6]}"
    req = SimpleNamespace(
        request_id="REQ-CAP-2", description="Migrate the DB",
        task_type=SimpleNamespace(value="feature"), project_id=pid,
    )
    handler = make_memory_capture_handler(_subsystem(s), _FakeState(req))
    await handler("request.failed", {"request_id": "REQ-CAP-2", "error": "boom"})
    rows = await s.list_memory(f"mem_project_{pid}")
    assert len(rows) == 1 and rows[0]["outcome"] == "failed"
    assert "boom" in rows[0]["text"]


@pytest.mark.asyncio
async def test_handler_skips_unassigned_project(store):
    s, _ = store
    req = SimpleNamespace(
        request_id="REQ-CAP-3", description="orphan",
        task_type=SimpleNamespace(value="feature"), project_id=UNASSIGNED_PROJECT_ID,
    )
    handler = make_memory_capture_handler(_subsystem(s), _FakeState(req))
    await handler("request.completed", {"request_id": "REQ-CAP-3", "result": "x"})
    # Nothing captured for the catch-all project.
    assert await s.count_memory(f"mem_project_{UNASSIGNED_PROJECT_ID}") == 0


@pytest.mark.asyncio
async def test_handler_soft_fails_when_subsystem_down(store):
    s, _ = store
    req = SimpleNamespace(
        request_id="REQ-CAP-4", description="x",
        task_type=SimpleNamespace(value="feature"), project_id="P-DOWN",
    )
    handler = make_memory_capture_handler(_subsystem(s, available=False), _FakeState(req))
    # Must not raise and must not write.
    await handler("request.completed", {"request_id": "REQ-CAP-4"})
    assert await s.count_memory("mem_project_P-DOWN") == 0


# ── Settings ─────────────────────────────────────────────────────────────────


def test_settings_memory_namespace_helpers():
    s = KnowledgeSettings(
        enabled=True, pg_host="h", pg_port=5432, pg_user="u", pg_password="",
        pg_db="d", pool_min=1, pool_max=4, embed_provider="fastembed",
        embed_model="m", embed_cache_dir="", dimensions=384, top_k=8,
        hybrid_candidates=40, rerank_enabled=False, ingest_mode="inline",
        platform_namespace="kb_platform",
        personal_namespace="kb_personal",
        project_namespace_prefix="kb_project_", memory_namespace_prefix="mem_project_",
        agent_memory_namespace_prefix="mem_agent_",
        personal_auto_approve=False,
    )
    assert s.memory_namespace("P-9") == "mem_project_P-9"
    assert s.agent_memory_namespace("research_specialist") == "mem_agent_research_specialist"
