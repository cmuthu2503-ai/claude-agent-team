"""KB-25 — time-aware episodic recall (store.search_memory + recall_memory tool).

Live against the running Postgres+pgvector (gated on reachability). Covers:

- semantic ranking (pgvector cosine) over ``agent_memory``,
- the ``days`` (last N days) and ``as_of`` (point-in-time) time filters,
- supersession exclusion (KB-27 forward-compat) + use_count reinforcement,
- the ``recall_memory`` tool: namespace-scoped, [MEMORY · unvetted] tagging
  (never citeable as fact, §5.1), and the no-app-scope / empty-query guards.
"""

from __future__ import annotations

import os
import uuid

import pytest

from src.knowledge.models import AgentMemory
from src.knowledge.pg import open_pool
from src.knowledge.store import KnowledgeStore
from src.knowledge.tools import KbScope, RecallMemoryTool


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
        pytest.skip("Postgres not reachable for live KB recall test")
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


def _mem(ns: str, text: str, emb: list[float]) -> AgentMemory:
    return AgentMemory(
        memory_id=f"mem-{uuid.uuid4().hex[:10]}", namespace=ns,
        agent_id="orchestrator", kind="episode", text=text, outcome="success",
        project_id="P-1", embedding=emb,
    )


async def _backdate(pool, memory_id: str, when: str):  # noqa: ANN001
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE agent_memory SET created_at=%s::timestamptz WHERE memory_id=%s",
            [when, memory_id],
        )


# ── store.search_memory ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_semantic_recall_ranks_by_similarity(store):
    s, _ = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    await s.insert_memory(_mem(ns, "alpha episode", _onehot(0)))
    await s.insert_memory(_mem(ns, "beta episode", _onehot(1)))
    await s.insert_memory(_mem(ns, "gamma episode", _onehot(2)))
    # Query closest to the 'beta' vector → beta ranks first.
    hits = await s.search_memory(ns, query_embedding=_onehot(1), limit=3)
    assert hits[0]["text"] == "beta episode"
    assert hits[0]["score"] is not None and hits[0]["score"] > 0.9


@pytest.mark.asyncio
async def test_days_filter_excludes_old_episodes(store):
    s, pool = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    recent = await s.insert_memory(_mem(ns, "recent", _onehot(0)))
    old = await s.insert_memory(_mem(ns, "ancient", _onehot(1)))
    await _backdate(pool, old, "2026-01-01T00:00:00Z")
    # Recency mode (no embedding) within the last 2 days → only the recent row.
    hits = await s.search_memory(ns, days=2, limit=10)
    texts = [h["text"] for h in hits]
    assert "recent" in texts and "ancient" not in texts
    assert recent  # sanity


@pytest.mark.asyncio
async def test_as_of_point_in_time(store):
    s, pool = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    old = await s.insert_memory(_mem(ns, "old-state", _onehot(0)))
    await s.insert_memory(_mem(ns, "new-state", _onehot(1)))
    await _backdate(pool, old, "2026-01-01T00:00:00Z")
    # As of just after the old row but before the new (now) row.
    hits = await s.search_memory(ns, as_of="2026-02-01T00:00:00Z", limit=10)
    texts = [h["text"] for h in hits]
    assert "old-state" in texts and "new-state" not in texts


@pytest.mark.asyncio
async def test_recall_excludes_superseded_and_bumps_use_count(store):
    s, pool = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    keep = await s.insert_memory(_mem(ns, "kept", _onehot(0)))
    gone = await s.insert_memory(_mem(ns, "superseded", _onehot(1)))
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE agent_memory SET superseded_by=%s WHERE memory_id=%s",
            [keep, gone],
        )
    hits = await s.search_memory(ns, query_embedding=_onehot(1), limit=10)
    assert all(h["text"] != "superseded" for h in hits)
    # 'kept' was returned → its use_count bumped to 1.
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT use_count FROM agent_memory WHERE memory_id=%s", [keep]
        )
        assert (await cur.fetchone())[0] == 1


# ── recall_memory tool ───────────────────────────────────────────────────────


class _FakeEmbedder:
    """Maps a query to a one-hot vector by a digit in the text, else zeros."""

    async def embed_query(self, text: str) -> list[float]:
        for ch in text:
            if ch.isdigit():
                return _onehot(int(ch))
        return _onehot(0)


@pytest.mark.asyncio
async def test_recall_tool_returns_unvetted_tagged_memory(store):
    s, _ = store
    ns = f"mem_test_{uuid.uuid4().hex[:6]}"
    await s.insert_memory(_mem(ns, "we chose option 1 for auth", _onehot(1)))
    tool = RecallMemoryTool(s, _FakeEmbedder())
    out = await tool.execute(
        {"query": "what did we pick for 1"},
        kb_scope=KbScope(memory_namespace=ns, agent_id="research_specialist"),
    )
    assert "[MEMORY" in out
    assert "option 1 for auth" in out
    assert "NOT citeable" in out


@pytest.mark.asyncio
async def test_recall_tool_namespace_isolation(store):
    s, _ = store
    ns_a = f"mem_a_{uuid.uuid4().hex[:6]}"
    ns_b = f"mem_b_{uuid.uuid4().hex[:6]}"
    await s.insert_memory(_mem(ns_b, "App B secret episode", _onehot(1)))
    tool = RecallMemoryTool(s, _FakeEmbedder())
    out = await tool.execute(
        {"query": "recall 1"},
        kb_scope=KbScope(memory_namespace=ns_a, agent_id="research_specialist"),
    )
    # App A's recall can't see App B's episode.
    assert "App B secret episode" not in out


@pytest.mark.asyncio
async def test_recall_tool_no_app_scope_guards(store):
    s, _ = store
    tool = RecallMemoryTool(s, _FakeEmbedder())
    out = await tool.execute({"query": "anything"}, kb_scope=KbScope())  # no memory_ns
    assert "isn't scoped to an application" in out


@pytest.mark.asyncio
async def test_recall_tool_empty_query(store):
    s, _ = store
    tool = RecallMemoryTool(s, _FakeEmbedder())
    out = await tool.execute(
        {"query": "   "}, kb_scope=KbScope(memory_namespace="mem_x")
    )
    assert "non-empty 'query'" in out
