"""KB-31 — retrieval feedback store primitives (record + recency-weighted boost).

Live against the running Postgres+pgvector (gated on reachability).
"""

from __future__ import annotations

import os
import uuid

import pytest

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
        pytest.skip("Postgres not reachable for live KB feedback test")
    s = KnowledgeStore(pool, dimensions=384)
    await s.initialize()
    try:
        yield s, pool
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_record_and_boost_net_votes(store):
    s, _ = store
    cid = f"c-{uuid.uuid4().hex[:10]}"
    ns = "kb_test"
    await s.record_feedback(chunk_id=cid, namespace=ns, vote=1, created_by="u1")
    await s.record_feedback(chunk_id=cid, namespace=ns, vote=1, created_by="u2")
    await s.record_feedback(chunk_id=cid, namespace=ns, vote=-1, created_by="u3")
    boosts = await s.get_feedback_boosts([cid])
    # 2 up, 1 down, all fresh → net ≈ +1
    assert cid in boosts
    assert 0.5 < boosts[cid] < 1.5


@pytest.mark.asyncio
async def test_revote_overwrites_not_duplicates(store):
    s, _ = store
    cid = f"c-{uuid.uuid4().hex[:10]}"
    await s.record_feedback(chunk_id=cid, namespace="kb_test", vote=1, created_by="u1")
    await s.record_feedback(chunk_id=cid, namespace="kb_test", vote=-1, created_by="u1")  # flip
    boosts = await s.get_feedback_boosts([cid])
    # single user, latest vote is down → net negative (not 1up + 1down = 0)
    assert boosts[cid] < 0


@pytest.mark.asyncio
async def test_recency_weighting_favours_fresh(store):
    s, pool = store
    fresh = f"c-{uuid.uuid4().hex[:10]}"
    stale = f"c-{uuid.uuid4().hex[:10]}"
    await s.record_feedback(chunk_id=fresh, namespace="kb_test", vote=1, created_by="u1")
    await s.record_feedback(chunk_id=stale, namespace="kb_test", vote=1, created_by="u1")
    # Age the stale vote well past the 30-day half-life.
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE kb_feedback SET created_at = now() - interval '120 days' "
            "WHERE chunk_id=%s", [stale],
        )
    boosts = await s.get_feedback_boosts([fresh, stale])
    assert boosts[fresh] > boosts[stale]   # decayed
    assert boosts[stale] < 0.2             # heavily faded


@pytest.mark.asyncio
async def test_no_feedback_absent_from_map(store):
    s, _ = store
    assert await s.get_feedback_boosts([f"c-{uuid.uuid4().hex[:8]}"]) == {}
    assert await s.get_feedback_boosts([]) == {}
