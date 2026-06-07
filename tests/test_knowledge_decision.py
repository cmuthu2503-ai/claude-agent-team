"""KB-20 — decision ledger: record_decision tool + auto-derive from trace.

Live against Postgres. Proves the explicit tool writes a provenance row scoped
to the request/agent/project, `list_decisions` reads it back, and the executor
auto-derives a decision from the retrieval trace when the agent didn't record
one itself (and skips when it did).
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from src.knowledge.tools import KbScope, RecordDecisionTool


@pytest.fixture
async def store():
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
        pytest.skip("Postgres not reachable for decision-ledger test")
    s = KnowledgeStore(pool, dimensions=384)
    await s.initialize()
    try:
        yield s, pool
    finally:
        await pool.close()


async def _cleanup(pool, request_id):  # noqa: ANN001
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM decision_ledger WHERE request_id=%s", [request_id])
        await conn.execute("DELETE FROM kb_retrieval_audit WHERE request_id=%s", [request_id])


# ── Explicit tool ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_decision_tool_writes_scoped_row(store):
    s, pool = store
    req = f"req-{uuid.uuid4().hex[:8]}"
    tool = RecordDecisionTool(s)
    scope = KbScope(namespace="kb_project_appA", agent_id="research_specialist",
                    request_id=req, project_id="appA")
    try:
        out = await tool.execute(
            {"summary": "Recommend Postgres+pgvector over sqlite-vec for scale.",
             "cited_chunk_ids": ["c1", "c2"]},
            kb_scope=scope,
        )
        assert "Decision recorded" in out and "2 source" in out
        ledger = await s.list_decisions(req)
        assert len(ledger) == 1
        d = ledger[0]
        assert d["agent_id"] == "research_specialist"
        assert d["project_id"] == "appA"  # injected from scope, not spoofable
        assert d["retrieved_chunk_ids"] == ["c1", "c2"]
        assert "pgvector" in d["summary"]
    finally:
        await _cleanup(pool, req)


@pytest.mark.asyncio
async def test_record_decision_requires_summary_and_request(store):
    s, _ = store
    tool = RecordDecisionTool(s)
    blank = await tool.execute({"summary": "  "}, kb_scope=KbScope(request_id="r"))
    assert "non-empty 'summary'" in blank
    assert "within a request" in await tool.execute({"summary": "x"}, kb_scope=KbScope())


# ── Auto-derive from the trace ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_record_derives_from_trace(store):
    from src.agents.executor import AgentSystemExecutor
    from src.config.loader import ConfigLoader

    s, pool = store
    req = f"req-{uuid.uuid4().hex[:8]}"
    # The agent's retrieval surfaced two chunks during the task.
    await s.record_retrieval(
        agent_id="research_specialist", namespace="kb_project_appA", query="pricing",
        request_id=req, returned_chunk_ids=["ck1", "ck2"], cited_chunk_ids=["ck1"])

    config = ConfigLoader()
    config.load_all()
    executor = AgentSystemExecutor(config, state=None)
    executor.kb_subsystem = SimpleNamespace(available=True, knowledge_store=s)
    scope = KbScope(namespace="kb_project_appA", agent_id="research_specialist",
                    request_id=req, project_id="appA")
    try:
        await executor._auto_record_decision(
            "research_specialist", req, scope, {"mode": "hybrid"},
            {"description": "research pricing"}, {"text": "The app charges a 2% fee."})
        ledger = await s.list_decisions(req)
        assert len(ledger) == 1
        d = ledger[0]
        assert d["summary"] == "The app charges a 2% fee."
        assert set(d["retrieved_chunk_ids"]) == {"ck1", "ck2"}  # from the trace
        assert d["inputs_digest"]  # a digest was recorded
    finally:
        await _cleanup(pool, req)


@pytest.mark.asyncio
async def test_auto_record_skips_when_agent_recorded_explicitly(store):
    from src.agents.executor import AgentSystemExecutor
    from src.config.loader import ConfigLoader

    s, pool = store
    req = f"req-{uuid.uuid4().hex[:8]}"
    # Agent already recorded its own decision.
    await s.record_decision(request_id=req, agent_id="research_specialist",
                            summary="my own conclusion", project_id="appA")

    config = ConfigLoader()
    config.load_all()
    executor = AgentSystemExecutor(config, state=None)
    executor.kb_subsystem = SimpleNamespace(available=True, knowledge_store=s)
    scope = KbScope(agent_id="research_specialist", request_id=req, project_id="appA")
    try:
        await executor._auto_record_decision(
            "research_specialist", req, scope, {"mode": "hybrid"},
            {"description": "x"}, {"text": "auto summary that should NOT be added"})
        ledger = await s.list_decisions(req)
        assert len(ledger) == 1  # no duplicate auto-row
        assert ledger[0]["summary"] == "my own conclusion"
    finally:
        await _cleanup(pool, req)


@pytest.mark.asyncio
async def test_auto_record_noop_without_retrieval_config(store):
    from src.agents.executor import AgentSystemExecutor
    from src.config.loader import ConfigLoader

    s, pool = store
    req = f"req-{uuid.uuid4().hex[:8]}"
    config = ConfigLoader()
    config.load_all()
    executor = AgentSystemExecutor(config, state=None)
    executor.kb_subsystem = SimpleNamespace(available=True, knowledge_store=s)
    scope = KbScope(agent_id="backend_specialist", request_id=req)
    try:
        # No retrieval_config → not a KB-wired agent → no decision recorded.
        await executor._auto_record_decision(
            "backend_specialist", req, scope, None, {"x": "y"}, {"text": "code done"})
        assert await s.list_decisions(req) == []
    finally:
        await _cleanup(pool, req)
