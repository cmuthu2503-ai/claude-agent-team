"""KB-30 — retention + forgetting (TTL expiry, relevance pruning, RTBF purge).

Live against the running Postgres+pgvector (gated on reachability). Covers the
store primitives + the ``run_retention_sweep`` orchestration:

- TTL expiry deletes episodes past their per-row ``ttl_days`` (and spares the
  ones with no TTL),
- relevance pruning sheds unused, stale ``episode`` rows but keeps summaries and
  anything recalled (``use_count``),
- ``forget_subject`` erases a subject across BOTH stores and writes an audit row,
- the sweep aggregates + audits.
"""

from __future__ import annotations

import os
import uuid

import pytest

from src.knowledge.models import AgentMemory, KbChunk, KbDocument
from src.knowledge.pg import open_pool
from src.knowledge.retention import run_retention_sweep
from src.knowledge.store import KnowledgeStore


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
        pytest.skip("Postgres not reachable for live KB retention test")
    s = KnowledgeStore(pool, dimensions=384)
    await s.initialize()
    try:
        yield s, pool
    finally:
        await pool.close()


def _mem(ns: str, text: str, *, kind: str = "episode", ttl_days: int | None = None,
         use_count: int = 0) -> AgentMemory:
    m = AgentMemory(
        memory_id=f"mem-{uuid.uuid4().hex[:10]}", namespace=ns, agent_id="orchestrator",
        kind=kind, text=text, outcome="success", project_id="P-1",
        embedding=[0.1, 0.2, 0.3] + [0.0] * 381, ttl_days=ttl_days,
    )
    m.use_count = use_count
    return m


async def _set_times(pool, memory_id, *, created=None, last_used=None, use_count=None):  # noqa: ANN001
    sets, params = [], []
    if created is not None:
        sets.append("created_at=%s::timestamptz")
        params.append(created)
    if last_used is not None:
        sets.append("last_used_at=%s::timestamptz")
        params.append(last_used)
    if use_count is not None:
        sets.append("use_count=%s")
        params.append(use_count)
    params.append(memory_id)
    async with pool.connection() as conn:
        await conn.execute(f"UPDATE agent_memory SET {', '.join(sets)} WHERE memory_id=%s", params)


# ── TTL expiry ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ttl_expiry_deletes_only_expired(store):
    s, pool = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    expired = await s.insert_memory(_mem(ns, "old", ttl_days=1))
    await _set_times(pool, expired, created="2026-01-01T00:00:00Z")
    fresh = await s.insert_memory(_mem(ns, "recent", ttl_days=1))        # created now
    forever = await s.insert_memory(_mem(ns, "no ttl", ttl_days=None))
    await _set_times(pool, forever, created="2026-01-01T00:00:00Z")

    n = await s.expire_memory_by_ttl(ns)
    assert n == 1
    remaining = {r["memory_id"] for r in await s.list_memory(ns, limit=50)}
    assert expired not in remaining
    assert fresh in remaining and forever in remaining  # no-ttl is never expired


# ── relevance pruning ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prune_unused_keeps_used_and_summaries(store):
    s, pool = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    stale_unused = await s.insert_memory(_mem(ns, "landfill"))
    await _set_times(pool, stale_unused, created="2026-01-01T00:00:00Z", use_count=0)
    stale_used = await s.insert_memory(_mem(ns, "valuable"))
    await _set_times(pool, stale_used, created="2026-01-01T00:00:00Z", use_count=3)
    summary = await s.insert_memory(_mem(ns, "distilled", kind="summary"))
    await _set_times(pool, summary, created="2026-01-01T00:00:00Z", use_count=0)
    recent = await s.insert_memory(_mem(ns, "today"))  # created now

    n = await s.prune_unused_memory(ns, stale_days=30, max_use_count=0)
    assert n == 1  # only the stale, unused episode
    ids = {r["memory_id"] for r in await s.list_memory(ns, kinds=["episode", "summary"], limit=50)}
    assert stale_unused not in ids
    assert stale_used in ids and summary in ids and recent in ids


# ── right-to-be-forgotten ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forget_subject_erases_both_stores_and_audits(store):
    s, _ = store
    mem_ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    kb_ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    subj = f"subject-{uuid.uuid4().hex[:8]}@example.com"

    await s.insert_memory(_mem(mem_ns, f"contacted {subj} about pricing"))
    await s.insert_memory(_mem(mem_ns, "unrelated episode"))
    doc = KbDocument(
        doc_id=f"doc-{uuid.uuid4().hex[:8]}", namespace=kb_ns, source_type="upload",
        title="Interview notes", content_hash=uuid.uuid4().hex,
    )
    await s.create_document(doc)
    await s.insert_chunks([KbChunk(
        chunk_id=f"c-{uuid.uuid4().hex[:8]}", doc_id=doc.doc_id, namespace=kb_ns,
        ordinal=0, text=f"the interviewee {subj} said ...",
    )])

    counts = await s.forget_subject(subj)
    assert counts["memory"] == 1
    assert counts["documents"] == 1
    # gone from both stores; the unrelated memory survives
    assert await s.get_document(doc.doc_id) is None
    assert await s.count_memory(mem_ns) == 1

    audit = await s.list_retention_audit(limit=20)
    assert any(a["action"] == "forget_subject" and a["scope"] == subj for a in audit)


@pytest.mark.asyncio
async def test_forget_subject_scoped_to_namespace(store):
    s, _ = store
    ns_a = f"mem_a_{uuid.uuid4().hex[:6]}"
    ns_b = f"mem_b_{uuid.uuid4().hex[:6]}"
    subj = f"tok-{uuid.uuid4().hex[:8]}"
    await s.insert_memory(_mem(ns_a, f"note about {subj}"))
    await s.insert_memory(_mem(ns_b, f"other note about {subj}"))
    counts = await s.forget_subject(subj, namespace=ns_a)
    assert counts["memory"] == 1
    assert await s.count_memory(ns_a) == 0
    assert await s.count_memory(ns_b) == 1  # other app untouched


# ── sweep orchestration ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_retention_sweep_aggregates_and_audits(store):
    s, pool = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    ttl = await s.insert_memory(_mem(ns, "ttl-gone", ttl_days=1))
    await _set_times(pool, ttl, created="2026-01-01T00:00:00Z")
    stale = await s.insert_memory(_mem(ns, "prune-gone"))
    await _set_times(pool, stale, created="2026-01-01T00:00:00Z", use_count=0)

    class _Sub:
        available = True
        knowledge_store = s

    res = await run_retention_sweep(_Sub(), stale_days=30, max_use_count=0)
    assert res.ttl_expired >= 1
    assert res.pruned >= 1
    audit = await s.list_retention_audit(limit=20)
    assert any(a["action"] == "sweep" for a in audit)
