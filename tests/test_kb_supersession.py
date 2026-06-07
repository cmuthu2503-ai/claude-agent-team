"""KB-27 — supersession chains + point-in-time (as-of) recall.

Live against the running Postgres+pgvector (gated on reachability). Covers:

- ``supersede_memory`` links old→new, stamps ``superseded_at``, and the old row
  leaves DEFAULT retrieval immediately,
- ``as_of`` recall returns the row that was LIVE at that instant — a superseded
  fact still surfaces when you ask as-of a date before it was replaced
  (point-in-time truth),
- supersession guards (self / missing / cross-namespace) are refused.
"""

from __future__ import annotations

import os
import uuid

import pytest

from src.knowledge.models import AgentMemory
from src.knowledge.pg import open_pool
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
        pytest.skip("Postgres not reachable for live KB supersession test")
    s = KnowledgeStore(pool, dimensions=384)
    await s.initialize()
    try:
        yield s, pool
    finally:
        await pool.close()


def _onehot(i: int) -> list[float]:
    v = [0.0] * 384
    v[i] = 1.0
    return v


def _mem(ns: str, text: str) -> AgentMemory:
    return AgentMemory(
        memory_id=f"mem-{uuid.uuid4().hex[:10]}", namespace=ns,
        agent_id="orchestrator", kind="episode", text=text, outcome="success",
        project_id="P-1", embedding=_onehot(1),
    )


async def _backdate(pool, memory_id, when):  # noqa: ANN001
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE agent_memory SET created_at=%s::timestamptz WHERE memory_id=%s",
            [when, memory_id],
        )


# ── supersession write + default retrieval ──────────────────────────────────


@pytest.mark.asyncio
async def test_supersede_sets_chain_and_drops_from_default(store):
    s, _ = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    old = await s.insert_memory(_mem(ns, "tier is $9"))
    new = await s.insert_memory(_mem(ns, "tier is $12"))
    assert await s.supersede_memory(old, new, when="2026-03-01T00:00:00Z") is True

    old_row = await s.get_memory(old)
    assert old_row["superseded_by"] == new

    # Default recall (current truth) returns only the live row.
    hits = await s.search_memory(ns, query_embedding=_onehot(1), limit=10)
    ids = [h["memory_id"] for h in hits]
    assert new in ids and old not in ids


@pytest.mark.asyncio
async def test_as_of_returns_point_in_time_truth(store):
    s, pool = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    old = await s.insert_memory(_mem(ns, "tier is $9"))
    new = await s.insert_memory(_mem(ns, "tier is $12"))
    await _backdate(pool, old, "2026-01-01T00:00:00Z")
    await _backdate(pool, new, "2026-03-01T00:00:00Z")
    await s.supersede_memory(old, new, when="2026-03-01T00:00:00Z")

    # As of Feb: the OLD fact was live, the new one didn't exist yet.
    feb = await s.search_memory(
        ns, query_embedding=_onehot(1), as_of="2026-02-01T00:00:00Z",
        limit=10, bump_use=False,
    )
    feb_ids = [h["memory_id"] for h in feb]
    assert old in feb_ids and new not in feb_ids

    # As of April: the NEW fact is live, the old one already superseded.
    apr = await s.search_memory(
        ns, query_embedding=_onehot(1), as_of="2026-04-01T00:00:00Z",
        limit=10, bump_use=False,
    )
    apr_ids = [h["memory_id"] for h in apr]
    assert new in apr_ids and old not in apr_ids


# ── guards ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supersede_self_is_refused(store):
    s, _ = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    m = await s.insert_memory(_mem(ns, "x"))
    assert await s.supersede_memory(m, m) is False


@pytest.mark.asyncio
async def test_supersede_missing_is_refused(store):
    s, _ = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    m = await s.insert_memory(_mem(ns, "x"))
    assert await s.supersede_memory(m, "mem-does-not-exist") is False
    assert await s.supersede_memory("mem-nope", m) is False


@pytest.mark.asyncio
async def test_supersede_cross_namespace_is_refused(store):
    s, _ = store
    ns_a = f"mem_a_{uuid.uuid4().hex[:6]}"
    ns_b = f"mem_b_{uuid.uuid4().hex[:6]}"
    a = await s.insert_memory(_mem(ns_a, "a"))
    b = await s.insert_memory(_mem(ns_b, "b"))
    # Can't supersede App A's memory with App B's row.
    assert await s.supersede_memory(a, b) is False
    assert (await s.get_memory(a))["superseded_by"] is None
