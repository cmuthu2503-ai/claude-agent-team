"""KB-21 — knowledge_cite: mechanical enforcement of the §5.1 grounding rule.

Live against Postgres. Proves the citation tool records real Knowledge chunks
(into the audit, so they surface in the Grounding Report) and REJECTS anything
that isn't citeable as a substantive fact: memory/recall ids, fabricated ids,
and chunks from another task's namespace.
"""

from __future__ import annotations

import os
import uuid

import pytest

from src.knowledge.models import KbChunk, KbDocument
from src.knowledge.tools import KbScope, KnowledgeCiteTool

_VEC = [0.1, 0.2, 0.3] + [0.0] * 381


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
        pytest.skip("Postgres not reachable for cite test")
    s = KnowledgeStore(pool, dimensions=384)
    await s.initialize()
    try:
        yield s, pool
    finally:
        await pool.close()


async def _seed_chunk(s, ns, chunk_id, title):  # noqa: ANN001
    doc_id = f"doc-{uuid.uuid4().hex[:12]}"
    await s.create_document(KbDocument(
        doc_id=doc_id, namespace=ns, source_type="prd", title=title,
        content_hash=uuid.uuid4().hex, status="approved"))
    await s.insert_chunks([KbChunk(
        chunk_id=chunk_id, doc_id=doc_id, namespace=ns, ordinal=0,
        text="The app charges a 2% fee.", embedding=_VEC)])
    return doc_id


@pytest.mark.asyncio
async def test_cite_records_real_knowledge_chunk(store):
    s, pool = store
    ns = f"kb_project_{uuid.uuid4().hex[:8]}"
    cid = f"ck-{uuid.uuid4().hex[:8]}"
    req = f"req-{uuid.uuid4().hex[:8]}"
    doc_id = await _seed_chunk(s, ns, cid, "Pricing PRD")
    tool = KnowledgeCiteTool(s)
    scope = KbScope(namespace=ns, agent_id="research_specialist", request_id=req)
    try:
        out = await tool.execute({"chunk_ids": [cid]}, kb_scope=scope)
        assert "Recorded 1 citation" in out
        assert f"[KB#{cid}]" in out and "Pricing PRD" in out
        # Surfaces in the grounding report as a cited chunk.
        audit = await s.list_retrieval_audit(req)
        assert any(cid in (a["cited_chunk_ids"] or []) for a in audit)
    finally:
        await s.purge_document(doc_id)
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM kb_retrieval_audit WHERE request_id=%s", [req])


@pytest.mark.asyncio
async def test_cite_rejects_memory_ids(store):
    s, _ = store
    tool = KnowledgeCiteTool(s)
    scope = KbScope(namespace="kb_platform", agent_id="research_specialist", request_id="r")
    out = await tool.execute({"chunk_ids": ["mem-abc123", "memory-99"]}, kb_scope=scope)
    assert "REJECTED" in out and "Memory" in out
    assert "Recorded" not in out  # nothing accepted


@pytest.mark.asyncio
async def test_cite_rejects_fabricated_and_cross_namespace(store):
    s, pool = store
    ns_a = f"kb_project_{uuid.uuid4().hex[:8]}"
    ns_b = f"kb_project_{uuid.uuid4().hex[:8]}"
    cid_b = f"ck-{uuid.uuid4().hex[:8]}"
    doc_b = await _seed_chunk(s, ns_b, cid_b, "Other App PRD")
    tool = KnowledgeCiteTool(s)
    scope_a = KbScope(namespace=ns_a, agent_id="research_specialist", request_id="r2")
    try:
        # A fabricated id + another app's real chunk → both rejected for scope A.
        out = await tool.execute(
            {"chunk_ids": ["chk-does-not-exist", cid_b]}, kb_scope=scope_a)
        assert "REJECTED" in out
        assert "chk-does-not-exist" in out and cid_b in out
        assert "Recorded" not in out
    finally:
        await s.purge_document(doc_b)


@pytest.mark.asyncio
async def test_cite_requires_chunk_ids(store):
    s, _ = store
    tool = KnowledgeCiteTool(s)
    assert "non-empty 'chunk_ids'" in await tool.execute({"chunk_ids": []}, kb_scope=KbScope())


def test_cite_tool_registered():
    # The tool is exported + registered in register_knowledge_tools.
    from unittest.mock import MagicMock

    from src.knowledge.tools import register_knowledge_tools

    reg = MagicMock()
    sub = MagicMock()
    sub.available = True
    register_knowledge_tools(reg, sub)
    names = {c.args[0] for c in reg.register_implementation.call_args_list}
    assert "knowledge_cite" in names
