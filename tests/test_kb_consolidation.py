"""KB-26 — episodic-memory consolidation job.

Live against the running Postgres+pgvector (gated on reachability). Covers:

- below-threshold and recent-only namespaces are left untouched,
- old episodes are summarized into one kind='summary' row and the raw rows
  expired,
- a recurring (outcome, goal) signature ≥ threshold becomes a PENDING promotion
  candidate (never auto-promoted),
- proposal creation is idempotent on content_hash,
- purge cascades promotion candidates.
"""

from __future__ import annotations

import os
import uuid

import pytest

from src.knowledge.consolidation import consolidate_namespace
from src.knowledge.models import AgentMemory
from src.knowledge.pg import open_pool
from src.knowledge.store import KnowledgeStore

_OLD = "2026-01-01T00:00:00Z"


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
        pytest.skip("Postgres not reachable for live KB consolidation test")
    s = KnowledgeStore(pool, dimensions=384)
    await s.initialize()
    try:
        yield s, pool
    finally:
        await pool.close()


def _episode(ns: str, outcome: str, goal: str) -> AgentMemory:
    text = f"[{outcome.upper()}] feature request REQ-{uuid.uuid4().hex[:6]}\nGoal: {goal}\n"
    return AgentMemory(
        memory_id=f"mem-{uuid.uuid4().hex[:10]}", namespace=ns,
        agent_id="orchestrator", kind="episode", text=text, outcome=outcome,
        project_id="P-1", embedding=[0.1, 0.2, 0.3] + [0.0] * 381,
    )


async def _insert_old(s, pool, ns, outcome, goal):  # noqa: ANN001
    mid = await s.insert_memory(_episode(ns, outcome, goal))
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE agent_memory SET created_at=%s::timestamptz WHERE memory_id=%s",
            [_OLD, mid],
        )
    return mid


# ── consolidate_namespace ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_below_threshold_skips(store):
    s, pool = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    await _insert_old(s, pool, ns, "success", "build a button")
    res = await consolidate_namespace(s, ns, after_days=1, min_episodes=5)
    assert res.skipped is True
    # nothing removed
    assert await s.count_memory(ns) == 1


@pytest.mark.asyncio
async def test_recent_episodes_not_consolidated(store):
    s, _ = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    for _ in range(5):
        await s.insert_memory(_episode(ns, "success", "ship feature"))  # created_at = now
    res = await consolidate_namespace(s, ns, after_days=30, min_episodes=3)
    assert res.skipped is True
    assert await s.count_memory(ns) == 5


@pytest.mark.asyncio
async def test_summarizes_and_expires_raw(store):
    s, pool = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    for i in range(4):
        await _insert_old(s, pool, ns, "success", f"distinct goal number {i}")
    res = await consolidate_namespace(s, ns, after_days=1, min_episodes=3)
    assert res.skipped is False
    assert res.consolidated == 4
    assert res.summary_id is not None
    # raw episodes gone, exactly one summary remains
    rows = await s.list_memory(ns, include_superseded=True)
    assert len(rows) == 1
    assert rows[0]["kind"] == "summary"
    assert "4 episode(s) consolidated" in rows[0]["text"]


@pytest.mark.asyncio
async def test_detects_recurring_pattern_as_pending_candidate(store):
    s, pool = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    # 3 failures with the SAME goal signature → one recurring pattern.
    for _ in range(3):
        await _insert_old(s, pool, ns, "failed", "migrate the legacy database")
    res = await consolidate_namespace(s, ns, after_days=1, min_episodes=3)
    assert res.proposals == 1
    cands = await s.list_promotion_candidates(namespace=ns, status="pending")
    assert len(cands) == 1
    c = cands[0]
    assert c["status"] == "pending"            # never auto-promoted
    assert c["occurrences"] == 3
    assert len(c["evidence_ids"]) == 3
    assert "Recurring pattern" in c["summary"]


@pytest.mark.asyncio
async def test_distinct_goals_do_not_form_a_pattern(store):
    s, pool = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    # Each goal has wholly distinct salient tokens → distinct signatures,
    # so no single (outcome, goal) pattern recurs ≥ threshold.
    for goal in ("optimize checkout latency", "redesign avatar uploader",
                 "translate onboarding emails", "archive stale invoices"):
        await _insert_old(s, pool, ns, "success", goal)
    res = await consolidate_namespace(s, ns, after_days=1, min_episodes=3)
    # summarized, but no single signature recurs ≥ 3 → no proposals
    assert res.consolidated == 4
    assert res.proposals == 0


@pytest.mark.asyncio
async def test_promotion_candidate_idempotent(store):
    s, _ = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    first = await s.create_promotion_candidate(
        namespace=ns, summary="x", content_hash="dup-hash", occurrences=3,
    )
    second = await s.create_promotion_candidate(
        namespace=ns, summary="y", content_hash="dup-hash", occurrences=3,
    )
    assert first is not None and second is None
    assert len(await s.list_promotion_candidates(namespace=ns)) == 1


@pytest.mark.asyncio
async def test_purge_cascades_promotion_candidates(store):
    s, _ = store
    pid = f"PURGE-{uuid.uuid4().hex[:6]}"
    mem_ns = f"mem_project_{pid}"
    await s.create_promotion_candidate(
        namespace=mem_ns, summary="pat", content_hash="h1", occurrences=3,
    )
    counts = await s.purge_project(pid, f"kb_project_{pid}", memory_namespace=mem_ns)
    assert counts["promotions"] == 1
    assert await s.list_promotion_candidates(namespace=mem_ns) == []


# ── KB-28: get + status transitions ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_and_set_promotion_status(store):
    s, _ = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    cid = await s.create_promotion_candidate(
        namespace=ns, project_id="P-1", summary="pattern x",
        content_hash=f"h-{uuid.uuid4().hex[:8]}", occurrences=4,
    )
    got = await s.get_promotion_candidate(cid)
    assert got is not None and got["status"] == "pending" and got["project_id"] == "P-1"

    # First review wins; a second transition off non-pending is a no-op.
    assert await s.set_promotion_status(cid, "promoted", reviewed_by="admin") is True
    assert await s.set_promotion_status(cid, "rejected", reviewed_by="admin") is False
    assert (await s.get_promotion_candidate(cid))["status"] == "promoted"


@pytest.mark.asyncio
async def test_set_promotion_status_missing_is_false(store):
    s, _ = store
    assert await s.set_promotion_status("promo-nope", "rejected") is False
