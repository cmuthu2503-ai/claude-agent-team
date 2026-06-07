"""KB-03 — KnowledgeStore schema + CRUD + bucket-membership sync.

Live integration tests against the running Postgres+pgvector (gated on
reachability, skip cleanly otherwise). The schema is idempotent and shared;
tests use unique ids/slugs so they don't collide, and clean up after.

The load-bearing assertion: writing kb_document_buckets keeps the
denormalized kb_chunks.bucket_ids in sync — that array is the retrieval
grounding filter, so a stale value would silently break isolation.
"""

from __future__ import annotations

import os
import uuid

import pytest

from src.knowledge.models import KbChunk, KbDocument
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
        pytest.skip("Postgres not reachable for live KB store test")
    s = KnowledgeStore(pool, dimensions=384)  # matches the platform model dim
    await s.initialize()  # idempotent; safe on shared schema
    try:
        yield s, pool
    finally:
        await pool.close()


def _doc(ns: str, h: str) -> KbDocument:
    return KbDocument(
        doc_id=f"doc-{uuid.uuid4().hex[:10]}", namespace=ns, source_type="upload",
        title="t", content_hash=h,
    )


async def _bucket_ids_of_chunk(pool, chunk_id):  # noqa: ANN001
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT bucket_ids FROM kb_chunks WHERE chunk_id=%s", [chunk_id]
        )
        row = await cur.fetchone()
    return {str(b) for b in (row[0] or [])} if row else set()


# ── Schema ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_is_idempotent(store):
    s, _ = store
    await s.initialize()  # second call must not raise
    await s.initialize()


# ── Documents ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_document_crud_and_hash_lookup(store):
    s, _ = store
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    d = _doc(ns, "hash-abc")
    await s.create_document(d)
    try:
        got = await s.get_document(d.doc_id)
        assert got is not None and got.title == "t" and got.status == "pending"

        by_hash = await s.find_document_by_hash(ns, "hash-abc")
        assert by_hash is not None and by_hash.doc_id == d.doc_id
        assert await s.find_document_by_hash(ns, "nope") is None

        # status transitions
        assert await s.set_document_status(d.doc_id, "approved", curated_by="alice")
        got2 = await s.get_document(d.doc_id)
        assert got2.status == "approved" and got2.approved_at is not None
        assert got2.curated_by == "alice"
    finally:
        await s.purge_document(d.doc_id)


# ── Buckets + the membership → bucket_ids sync invariant ──────────────────


@pytest.mark.asyncio
async def test_bucket_membership_syncs_chunk_bucket_ids(store):
    s, pool = store
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    d = _doc(ns, f"h-{uuid.uuid4().hex[:6]}")
    await s.create_document(d)
    b1 = await s.create_bucket(f"Brand {uuid.uuid4().hex[:6]}")
    b2 = await s.create_bucket(f"Research {uuid.uuid4().hex[:6]}")
    chunk = KbChunk(chunk_id=f"c-{uuid.uuid4().hex[:8]}", doc_id=d.doc_id,
                    namespace=ns, ordinal=0, text="hello",
                    embedding=[0.1, 0.2, 0.3] + [0.0] * 381)
    try:
        await s.insert_chunks([chunk])
        # No membership yet → empty bucket_ids
        assert await _bucket_ids_of_chunk(pool, chunk.chunk_id) == set()

        # set replaces membership → chunk bucket_ids reflect it
        await s.set_document_buckets(d.doc_id, [b1.bucket_id, b2.bucket_id])
        assert await _bucket_ids_of_chunk(pool, chunk.chunk_id) == {b1.bucket_id, b2.bucket_id}
        assert set(await s.get_document_buckets(d.doc_id)) == {b1.bucket_id, b2.bucket_id}

        # set to just b1 → b2 leaves the chunk's bucket_ids
        await s.set_document_buckets(d.doc_id, [b1.bucket_id])
        assert await _bucket_ids_of_chunk(pool, chunk.chunk_id) == {b1.bucket_id}

        # assign (additive) brings b2 back
        await s.assign_document_to_buckets(d.doc_id, [b2.bucket_id])
        assert await _bucket_ids_of_chunk(pool, chunk.chunk_id) == {b1.bucket_id, b2.bucket_id}

        # delete a bucket → it leaves the chunk's bucket_ids (re-synced)
        await s.delete_bucket(b2.bucket_id)
        assert await _bucket_ids_of_chunk(pool, chunk.chunk_id) == {b1.bucket_id}
    finally:
        await s.purge_document(d.doc_id)        # cascades chunk + membership
        await s.delete_bucket(b1.bucket_id)


@pytest.mark.asyncio
async def test_list_buckets_doc_count_and_rename(store):
    s, _ = store
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    b = await s.create_bucket(f"Temp {uuid.uuid4().hex[:6]}")
    d = _doc(ns, f"h-{uuid.uuid4().hex[:6]}")
    await s.create_document(d)
    try:
        await s.set_document_buckets(d.doc_id, [b.bucket_id])
        listed = {x.bucket_id: x for x in await s.list_buckets()}
        assert listed[b.bucket_id].doc_count == 1

        assert await s.rename_bucket(b.bucket_id, "Renamed Bucket XYZ", "new desc")
        listed2 = {x.bucket_id: x for x in await s.list_buckets()}
        assert listed2[b.bucket_id].name == "Renamed Bucket XYZ"
        assert listed2[b.bucket_id].slug == "renamed-bucket-xyz"
    finally:
        await s.purge_document(d.doc_id)
        await s.delete_bucket(b.bucket_id)


@pytest.mark.asyncio
async def test_bucket_counts_no_cartesian_fanout(store):
    """Regression: doc_count + chunk_count must be correlated subqueries, not
    joins. 2 docs × 2 chunks in one bucket → doc_count 2, chunk_count 4 — NOT
    16 (the fan-out the join version produced). Caught live during KB-13a."""
    s, _ = store
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    b = await s.create_bucket(f"FanOut {uuid.uuid4().hex[:6]}")
    docs = []
    try:
        for i in range(2):
            d = _doc(ns, f"h-{uuid.uuid4().hex[:8]}")
            await s.create_document(d)
            await s.set_document_status(d.doc_id, "approved")
            docs.append(d)
            await s.insert_chunks([
                KbChunk(chunk_id=f"c-{uuid.uuid4().hex[:8]}", doc_id=d.doc_id,
                        namespace=ns, ordinal=j, text=f"chunk {i}-{j}",
                        embedding=[float(j), 0.0, 0.0] + [0.0] * 381)
                for j in range(2)
            ])
            await s.set_document_buckets(d.doc_id, [b.bucket_id])

        got = await s.get_bucket(b.bucket_id)
        assert got.doc_count == 2, got.doc_count
        assert got.chunk_count == 4, f"fan-out regression: {got.chunk_count}"
        listed = {x.bucket_id: x for x in await s.list_buckets()}
        assert listed[b.bucket_id].chunk_count == 4
    finally:
        for d in docs:
            await s.purge_document(d.doc_id)
        await s.delete_bucket(b.bucket_id)


@pytest.mark.asyncio
async def test_system_bucket_is_idempotent(store):
    s, _ = store
    name = f"Platform {uuid.uuid4().hex[:6]}"
    a = await s.get_or_create_system_bucket(name)
    b = await s.get_or_create_system_bucket(name)
    try:
        assert a.bucket_id == b.bucket_id
        assert a.is_system is True
    finally:
        await s.delete_bucket(a.bucket_id)


# ── Audit + ledger ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retrieval_audit_and_decision_ledger(store):
    s, pool = store
    aid = await s.record_retrieval(
        agent_id="research_specialist", namespace="kb_platform",
        query="supervisor deploy", request_id="req-x",
        returned_chunk_ids=["c1", "c2"], cited_chunk_ids=["c1"],
    )
    did = await s.record_decision(
        request_id="req-x", agent_id="research_specialist",
        summary="concluded X", retrieved_chunk_ids=["c1"], inputs_digest="abc123",
    )
    assert aid.startswith("kbaud-") and did.startswith("kbdec-")
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT cited_chunk_ids FROM kb_retrieval_audit WHERE audit_id=%s", [aid]
        )
        assert (await cur.fetchone())[0] == ["c1"]
        cur2 = await conn.execute(
            "SELECT summary FROM decision_ledger WHERE decision_id=%s", [did]
        )
        assert (await cur2.fetchone())[0] == "concluded X"
    # cleanup
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM kb_retrieval_audit WHERE audit_id=%s", [aid])
        await conn.execute("DELETE FROM decision_ledger WHERE decision_id=%s", [did])


# ── Purge cascade ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_cascades_chunks_and_membership(store):
    s, pool = store
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    d = _doc(ns, f"h-{uuid.uuid4().hex[:6]}")
    await s.create_document(d)
    b = await s.create_bucket(f"B {uuid.uuid4().hex[:6]}")
    chunk = KbChunk(chunk_id=f"c-{uuid.uuid4().hex[:8]}", doc_id=d.doc_id,
                    namespace=ns, ordinal=0, text="x",
                    embedding=[1.0, 0.0, 0.0] + [0.0] * 381)
    await s.insert_chunks([chunk])
    await s.set_document_buckets(d.doc_id, [b.bucket_id])
    try:
        assert await s.purge_document(d.doc_id) is True
        async with pool.connection() as conn:
            cur = await conn.execute("SELECT count(*) FROM kb_chunks WHERE doc_id=%s", [d.doc_id])
            assert (await cur.fetchone())[0] == 0
            cur2 = await conn.execute(
                "SELECT count(*) FROM kb_document_buckets WHERE doc_id=%s", [d.doc_id])
            assert (await cur2.fetchone())[0] == 0
    finally:
        await s.delete_bucket(b.bucket_id)
