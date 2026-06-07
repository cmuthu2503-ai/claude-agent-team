"""KnowledgeStore — KB persistence layer (KB-03).

All KB relational state goes through this store (the platform convention:
routes never touch the DB directly). Runs over the shared async Postgres
pool from ``src/knowledge/pg.py``. Applies the idempotent schema at
``initialize()``.

Bucket membership is the one piece with a non-trivial invariant: writing
``kb_document_buckets`` must keep the denormalized ``kb_chunks.bucket_ids``
in sync (that array is the fast retrieval filter). Every membership change
re-syncs the doc's chunks via ``_sync_chunk_buckets`` inside the same
transaction, so retrieval never sees a half-updated scope.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import structlog

from src.knowledge.models import AgentMemory, KbBucket, KbChunk, KbDocument
from src.knowledge.schema import KNOWLEDGE_SCHEMA_SQL

logger = structlog.get_logger()


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "bucket"


class KnowledgeStore:
    """One per subsystem. Holds the pool; all methods are async."""

    def __init__(self, pool: Any, dimensions: int = 1024) -> None:
        self._pool = pool
        self._dim = dimensions

    async def initialize(self) -> None:
        """Create the KB schema (idempotent). Substitutes the vector
        dimension into the DDL so the embedding column width matches the
        configured embedder."""
        ddl = KNOWLEDGE_SCHEMA_SQL % {"DIM": int(self._dim)}
        async with self._pool.connection() as conn:
            await conn.execute(ddl)
        logger.info("knowledge_schema_ready", dimensions=self._dim)

    # ── Documents ───────────────────────────────────────────────────────

    async def create_document(self, doc: KbDocument) -> str:
        sql = (
            "INSERT INTO kb_documents "
            "(doc_id, namespace, project_id, source_type, title, uri, content_hash, "
            " sensitivity, status, version, curated_by, ttl_days) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        )
        async with self._pool.connection() as conn:
            await conn.execute(sql, [
                doc.doc_id, doc.namespace, doc.project_id, doc.source_type, doc.title,
                doc.uri, doc.content_hash, doc.sensitivity, doc.status, doc.version,
                doc.curated_by, doc.ttl_days,
            ])
        return doc.doc_id

    async def get_document(self, doc_id: str) -> KbDocument | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT doc_id, namespace, project_id, source_type, title, uri, "
                "content_hash, sensitivity, status, superseded_by, version, curated_by, "
                "approved_at, created_at, ttl_days FROM kb_documents WHERE doc_id = %s",
                [doc_id],
            )
            row = await cur.fetchone()
        return self._row_to_doc(row) if row else None

    async def find_document_by_hash(self, namespace: str, content_hash: str) -> KbDocument | None:
        """Idempotent-ingest support: has this exact content already landed
        in this namespace? (KB-05 uses this to skip re-embedding.)"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT doc_id, namespace, project_id, source_type, title, uri, "
                "content_hash, sensitivity, status, superseded_by, version, curated_by, "
                "approved_at, created_at, ttl_days FROM kb_documents "
                "WHERE namespace = %s AND content_hash = %s AND status <> 'purged' "
                "ORDER BY version DESC LIMIT 1",
                [namespace, content_hash],
            )
            row = await cur.fetchone()
        return self._row_to_doc(row) if row else None

    async def list_documents(
        self, namespace: str, status: str | None = None, limit: int = 200
    ) -> list[KbDocument]:
        where = "WHERE namespace = %s"
        params: list[Any] = [namespace]
        if status:
            where += " AND status = %s"
            params.append(status)
        params.append(limit)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT doc_id, namespace, project_id, source_type, title, uri, "
                "content_hash, sensitivity, status, superseded_by, version, curated_by, "
                f"approved_at, created_at, ttl_days FROM kb_documents {where} "
                "ORDER BY created_at DESC LIMIT %s",
                params,
            )
            rows = await cur.fetchall()
        return [self._row_to_doc(r) for r in rows]

    async def set_document_status(
        self, doc_id: str, status: str, curated_by: str | None = None
    ) -> bool:
        """approve (`approved`), retire/supersede (`superseded`), etc. Stamps
        approved_at + curator on approval."""
        if status == "approved":
            sql = ("UPDATE kb_documents SET status='approved', approved_at=now(), "
                   "curated_by=COALESCE(%s, curated_by) WHERE doc_id=%s")
            params = [curated_by, doc_id]
        else:
            sql = "UPDATE kb_documents SET status=%s WHERE doc_id=%s"
            params = [status, doc_id]
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return bool(cur.rowcount and cur.rowcount > 0)

    async def purge_document(self, doc_id: str) -> bool:
        """Hard delete the document + cascade (chunks, membership via FK
        ON DELETE CASCADE). Right-to-be-forgotten (FR-015)."""
        async with self._pool.connection() as conn:
            cur = await conn.execute("DELETE FROM kb_documents WHERE doc_id=%s", [doc_id])
            return bool(cur.rowcount and cur.rowcount > 0)

    # ── Chunks ──────────────────────────────────────────────────────────

    async def get_chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Hydrate chunks for citation assembly (KB-07): text + the parent
        document's title/uri/status. Returned as ``{chunk_id: {...}}`` so the
        retriever can re-order by its fused ranking."""
        if not chunk_ids:
            return {}
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT c.chunk_id, c.text, c.doc_id, c.namespace, c.metadata, "
                "d.title, d.uri, d.status "
                "FROM kb_chunks c JOIN kb_documents d ON d.doc_id = c.doc_id "
                "WHERE c.chunk_id = ANY(%s)",
                [chunk_ids],
            )
            rows = await cur.fetchall()
        return {
            r[0]: {
                "chunk_id": r[0], "text": r[1], "doc_id": r[2], "namespace": r[3],
                "metadata": r[4] if isinstance(r[4], dict) else {},
                "title": r[5], "uri": r[6], "status": r[7],
            }
            for r in rows
        }

    async def get_document_full(self, doc_id: str) -> dict[str, Any] | None:
        """Fetch a document's metadata + its full text (chunks concatenated in
        order). Used by the ``knowledge_get`` tool to pull deep context after a
        search hit. Returns None if the doc doesn't exist."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT doc_id, namespace, title, uri, status FROM kb_documents "
                "WHERE doc_id = %s", [doc_id],
            )
            d = await cur.fetchone()
            if not d:
                return None
            cur2 = await conn.execute(
                "SELECT text FROM kb_chunks WHERE doc_id = %s ORDER BY ordinal", [doc_id],
            )
            chunks = [r[0] for r in await cur2.fetchall()]
        return {
            "doc_id": d[0], "namespace": d[1], "title": d[2], "uri": d[3],
            "status": d[4], "text": "\n\n".join(chunks),
        }

    async def insert_chunks(self, chunks: list[KbChunk]) -> int:
        """Bulk-insert chunks for a document. ``bucket_ids`` is set later by
        the membership sync (chunks start with the doc's current buckets if
        the caller populated them, else empty)."""
        if not chunks:
            return 0
        sql = (
            "INSERT INTO kb_chunks (chunk_id, doc_id, namespace, ordinal, text, "
            "embedding, token_count, metadata, bucket_ids) "
            "VALUES (%s,%s,%s,%s,%s,%s::vector,%s,%s::jsonb,%s) "
            "ON CONFLICT (chunk_id) DO UPDATE SET "
            "  text=EXCLUDED.text, embedding=EXCLUDED.embedding, "
            "  token_count=EXCLUDED.token_count, metadata=EXCLUDED.metadata"
        )
        rows = [
            (
                c.chunk_id, c.doc_id, c.namespace, c.ordinal, c.text,
                _vec_literal(c.embedding) if c.embedding else None,
                c.token_count, json.dumps(c.metadata or {}),
                list(c.bucket_ids or []),
            )
            for c in chunks
        ]
        async with self._pool.connection() as conn:
            await conn.cursor().executemany(sql, rows)
        return len(rows)

    # ── Buckets ─────────────────────────────────────────────────────────

    async def create_bucket(
        self,
        name: str,
        description: str = "",
        project_id: str | None = None,
        is_system: bool = False,
        created_by: str = "system",
    ) -> KbBucket:
        bucket_id = str(uuid.uuid4())
        slug = _slugify(name)
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO kb_buckets (bucket_id, name, slug, description, project_id, "
                "is_system, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                [bucket_id, name, slug, description, project_id, is_system, created_by],
            )
        return KbBucket(
            bucket_id=bucket_id, name=name, slug=slug, description=description,
            project_id=project_id, is_system=is_system, created_by=created_by,
        )

    async def get_or_create_system_bucket(self, name: str = "Platform") -> KbBucket:
        """The auto-ingest target. Idempotent — returns the existing system
        bucket of this slug or creates it."""
        slug = _slugify(name)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT bucket_id, name, slug, description, project_id, is_system, "
                "created_by, created_at FROM kb_buckets WHERE slug=%s", [slug],
            )
            row = await cur.fetchone()
        if row:
            return self._row_to_bucket(row)
        return await self.create_bucket(name, "Auto-ingested platform knowledge.",
                                         is_system=True, created_by="system")

    async def list_buckets(self, project_id: str | None = None) -> list[KbBucket]:
        """All buckets (+ a doc_count and chunk_count per bucket).
        ``project_id=None`` lists all; pass a value to scope to a project
        (Phase 2). chunk_count counts only chunks of approved docs — the
        retrievable surface area, which is what the bucket card advertises."""
        where = "" if project_id is None else "WHERE b.project_id = %s"
        params = [] if project_id is None else [project_id]
        # doc_count + chunk_count are CORRELATED SUBQUERIES (not joins) — joining
        # kb_document_buckets and kb_chunks in one GROUP BY fans out into a
        # cartesian product and over-counts both. chunk_count is the approved,
        # retrievable surface area.
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT b.bucket_id, b.name, b.slug, b.description, b.project_id, "
                "b.is_system, b.created_by, b.created_at, "
                "(SELECT COUNT(*) FROM kb_document_buckets db "
                "   WHERE db.bucket_id=b.bucket_id) AS doc_count, "
                "(SELECT COUNT(*) FROM kb_chunks c "
                "   JOIN kb_documents d ON d.doc_id=c.doc_id "
                "   WHERE c.bucket_ids @> ARRAY[b.bucket_id] "
                "     AND d.status='approved') AS chunk_count "
                f"FROM kb_buckets b {where} ORDER BY b.is_system DESC, b.created_at",
                params,
            )
            rows = await cur.fetchall()
        out = []
        for r in rows:
            b = self._row_to_bucket(r[:8])
            b.doc_count = int(r[8])
            b.chunk_count = int(r[9])
            out.append(b)
        return out

    async def get_bucket(self, bucket_id: str) -> KbBucket | None:
        """Single bucket with doc_count (+ chunk_count of approved chunks)."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT b.bucket_id, b.name, b.slug, b.description, b.project_id, "
                "b.is_system, b.created_by, b.created_at, "
                "(SELECT COUNT(*) FROM kb_document_buckets db "
                "   WHERE db.bucket_id=b.bucket_id) AS doc_count, "
                "(SELECT COUNT(*) FROM kb_chunks c "
                "   JOIN kb_documents d ON d.doc_id=c.doc_id "
                "   WHERE c.bucket_ids @> ARRAY[b.bucket_id] "
                "     AND d.status='approved') AS chunk_count "
                "FROM kb_buckets b WHERE b.bucket_id=%s",
                [bucket_id],
            )
            row = await cur.fetchone()
        if not row:
            return None
        b = self._row_to_bucket(row[:8])
        b.doc_count = int(row[8])
        b.chunk_count = int(row[9])
        return b

    async def rename_bucket(
        self, bucket_id: str, name: str, description: str | None = None
    ) -> bool:
        sets = ["name=%s", "slug=%s"]
        params: list[Any] = [name, _slugify(name)]
        if description is not None:
            sets.append("description=%s")
            params.append(description)
        params.append(bucket_id)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE kb_buckets SET {', '.join(sets)} WHERE bucket_id=%s", params
            )
            return bool(cur.rowcount and cur.rowcount > 0)

    async def delete_bucket(self, bucket_id: str) -> bool:
        """Delete a bucket. Membership rows cascade (FK); the affected docs'
        chunks are re-synced so the removed bucket id leaves bucket_ids."""
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT doc_id FROM kb_document_buckets WHERE bucket_id=%s", [bucket_id]
            )
            doc_ids = [r[0] for r in await cur.fetchall()]
            cur2 = await conn.execute("DELETE FROM kb_buckets WHERE bucket_id=%s", [bucket_id])
            removed = bool(cur2.rowcount and cur2.rowcount > 0)
            for doc_id in doc_ids:
                await self._sync_chunk_buckets(conn, doc_id)
        return removed

    # ── Per-project provisioning + purge (KB-13, Phase 2) ────────────────

    async def provision_project(
        self, project_id: str, namespace: str, name: str = "App Knowledge"
    ) -> KbBucket:
        """Idempotently provision a Project's KB: ensure it has a project-owned
        default bucket. The namespace itself needs no DDL (it's a string scope
        on kb_documents/kb_chunks) — this just creates the grounding bucket the
        project's uploads + auto-ingested artifacts (KB-14) land in.

        Idempotent: returns the existing project system bucket if already
        provisioned. The bucket slug is project-scoped so it can't collide with
        another project's default bucket (the slug index is global)."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT bucket_id, name, slug, description, project_id, is_system, "
                "created_by, created_at FROM kb_buckets "
                "WHERE project_id=%s AND is_system=true ORDER BY created_at LIMIT 1",
                [project_id],
            )
            row = await cur.fetchone()
        if row:
            return self._row_to_bucket(row)
        bucket_id = str(uuid.uuid4())
        # Project-scoped slug → globally unique even with the same display name.
        slug = f"proj-{project_id}-{_slugify(name)}"
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO kb_buckets (bucket_id, name, slug, description, project_id, "
                "is_system, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                [bucket_id, name, slug, f"{namespace} — auto-provisioned app knowledge.",
                 project_id, True, "system"],
            )
        logger.info("kb_project_provisioned", project_id=project_id, namespace=namespace)
        return KbBucket(
            bucket_id=bucket_id, name=name, slug=slug,
            description=f"{namespace} — auto-provisioned app knowledge.",
            project_id=project_id, is_system=True, created_by="system",
        )

    async def purge_project(
        self, project_id: str, namespace: str, memory_namespace: str | None = None
    ) -> dict[str, int]:
        """Hard-delete ALL of a project's KB on project deletion: documents
        (cascading to chunks + membership via FK), buckets, retrieval audit,
        decision-ledger rows, and episodic memory. One transaction. Returns
        deleted counts.

        Documents/chunks/audit are scoped by ``namespace`` (the isolation
        partition); buckets + ledger carry ``project_id`` directly; episodic
        memory is scoped by ``memory_namespace`` (``mem_project_<id>``)."""
        async with self._pool.connection() as conn, conn.transaction():
            cur1 = await conn.execute(
                "DELETE FROM kb_documents WHERE namespace=%s", [namespace]
            )
            docs = int(cur1.rowcount or 0)
            cur2 = await conn.execute(
                "DELETE FROM kb_buckets WHERE project_id=%s", [project_id]
            )
            buckets = int(cur2.rowcount or 0)
            cur3 = await conn.execute(
                "DELETE FROM kb_retrieval_audit WHERE namespace=%s", [namespace]
            )
            audit = int(cur3.rowcount or 0)
            cur4 = await conn.execute(
                "DELETE FROM decision_ledger WHERE project_id=%s", [project_id]
            )
            ledger = int(cur4.rowcount or 0)
            memory = 0
            promotions = 0
            if memory_namespace:
                cur5 = await conn.execute(
                    "DELETE FROM agent_memory WHERE namespace=%s", [memory_namespace]
                )
                memory = int(cur5.rowcount or 0)
                cur6 = await conn.execute(
                    "DELETE FROM kb_promotion_candidates WHERE namespace=%s",
                    [memory_namespace],
                )
                promotions = int(cur6.rowcount or 0)
        logger.info(
            "kb_project_purged", project_id=project_id, namespace=namespace,
            documents=docs, buckets=buckets, audit=audit, ledger=ledger,
            memory=memory, promotions=promotions,
        )
        return {
            "documents": docs, "buckets": buckets, "audit": audit,
            "ledger": ledger, "memory": memory, "promotions": promotions,
        }

    # ── Membership (doc ↔ bucket) + the bucket_ids sync invariant ───────

    async def set_document_buckets(self, doc_id: str, bucket_ids: list[str]) -> None:
        """Replace a document's bucket membership with exactly ``bucket_ids``,
        then re-sync the doc's chunks. One transaction — retrieval never sees
        a partial scope."""
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute("DELETE FROM kb_document_buckets WHERE doc_id=%s", [doc_id])
            for b in dict.fromkeys(bucket_ids):  # de-dup, preserve order
                await conn.execute(
                    "INSERT INTO kb_document_buckets (doc_id, bucket_id) VALUES (%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    [doc_id, b],
                )
            await self._sync_chunk_buckets(conn, doc_id)

    async def assign_document_to_buckets(self, doc_id: str, bucket_ids: list[str]) -> None:
        """Add bucket tags (without removing existing), then re-sync."""
        async with self._pool.connection() as conn, conn.transaction():
            for b in dict.fromkeys(bucket_ids):
                await conn.execute(
                    "INSERT INTO kb_document_buckets (doc_id, bucket_id) VALUES (%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    [doc_id, b],
                )
            await self._sync_chunk_buckets(conn, doc_id)

    async def get_document_buckets(self, doc_id: str) -> list[str]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT bucket_id::text FROM kb_document_buckets WHERE doc_id=%s", [doc_id]
            )
            return [r[0] for r in await cur.fetchall()]

    async def get_document_buckets_bulk(
        self, doc_ids: list[str]
    ) -> dict[str, list[str]]:
        """Membership for many docs in one query — feeds the document list /
        tagging screen without N+1 round-trips. Returns ``{doc_id: [bucket_id]}``
        (docs with no membership are absent from the map)."""
        if not doc_ids:
            return {}
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT doc_id, bucket_id::text FROM kb_document_buckets "
                "WHERE doc_id = ANY(%s)",
                [doc_ids],
            )
            rows = await cur.fetchall()
        out: dict[str, list[str]] = {}
        for doc_id, bucket_id in rows:
            out.setdefault(doc_id, []).append(bucket_id)
        return out

    async def get_chunk_counts_bulk(self, doc_ids: list[str]) -> dict[str, int]:
        """Chunk count per document in one GROUP BY — the document's retrieval
        'weight'. Feeds the doc list without N+1 (docs with 0 chunks absent)."""
        if not doc_ids:
            return {}
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT doc_id, count(*) FROM kb_chunks WHERE doc_id = ANY(%s) "
                "GROUP BY doc_id",
                [doc_ids],
            )
            rows = await cur.fetchall()
        return {r[0]: int(r[1]) for r in rows}

    @staticmethod
    async def _sync_chunk_buckets(conn: Any, doc_id: str) -> None:
        """Re-derive kb_chunks.bucket_ids for a doc from its current
        membership. The single source of the denormalization invariant."""
        await conn.execute(
            "UPDATE kb_chunks SET bucket_ids = COALESCE(("
            "  SELECT array_agg(bucket_id) FROM kb_document_buckets WHERE doc_id=%s"
            "), '{}') WHERE doc_id=%s",
            [doc_id, doc_id],
        )

    # ── Audit + ledger (append-only) ────────────────────────────────────

    async def record_retrieval(
        self, *, agent_id: str, namespace: str, query: str,
        request_id: str | None = None, bucket_ids: list[str] | None = None,
        returned_chunk_ids: list[str] | None = None,
        cited_chunk_ids: list[str] | None = None,
    ) -> str:
        audit_id = f"kbaud-{uuid.uuid4().hex[:12]}"
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO kb_retrieval_audit "
                "(audit_id, request_id, agent_id, namespace, query, bucket_ids, "
                " returned_chunk_ids, cited_chunk_ids) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                [
                    audit_id, request_id, agent_id, namespace, query,
                    list(bucket_ids or []),
                    json.dumps(returned_chunk_ids or []),
                    json.dumps(cited_chunk_ids or []),
                ],
            )
        return audit_id

    async def record_decision(
        self, *, request_id: str, agent_id: str, summary: str,
        project_id: str | None = None,
        retrieved_chunk_ids: list[str] | None = None,
        recalled_memory_ids: list[str] | None = None,
        inputs_digest: str | None = None,
    ) -> str:
        decision_id = f"kbdec-{uuid.uuid4().hex[:12]}"
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO decision_ledger "
                "(decision_id, request_id, agent_id, project_id, summary, "
                " retrieved_chunk_ids, recalled_memory_ids, inputs_digest) "
                "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)",
                [
                    decision_id, request_id, agent_id, project_id, summary,
                    json.dumps(retrieved_chunk_ids or []),
                    json.dumps(recalled_memory_ids or []),
                    inputs_digest,
                ],
            )
        return decision_id

    async def list_retrieval_audit(
        self, request_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        """The Grounding Report (FR-011/016): every retrieval this request's
        agents performed — what they searched, in which buckets, what came
        back, and what they cited. Append-only, ordered oldest-first so the
        UI renders a reasoning trail in execution order."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT audit_id, request_id, agent_id, namespace, query, "
                "bucket_ids, returned_chunk_ids, cited_chunk_ids, created_at "
                "FROM kb_retrieval_audit WHERE request_id=%s "
                "ORDER BY created_at ASC LIMIT %s",
                [request_id, limit],
            )
            rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({
                "audit_id": r[0], "request_id": r[1], "agent_id": r[2],
                "namespace": r[3], "query": r[4],
                "bucket_ids": [str(b) for b in (r[5] or [])],
                "returned_chunk_ids": r[6] if isinstance(r[6], list) else [],
                "cited_chunk_ids": r[7] if isinstance(r[7], list) else [],
                "created_at": r[8].isoformat() if r[8] else None,
            })
        return out

    async def list_decisions(
        self, request_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        """The decision ledger for a request (KB-20/23): every agent decision
        point + the chunks/memory + inputs digest that justified it, oldest
        first. Feeds the auto-record dedup check and the provenance UI."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT decision_id, request_id, agent_id, project_id, summary, "
                "retrieved_chunk_ids, recalled_memory_ids, inputs_digest, created_at "
                "FROM decision_ledger WHERE request_id=%s ORDER BY created_at ASC LIMIT %s",
                [request_id, limit],
            )
            rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({
                "decision_id": r[0], "request_id": r[1], "agent_id": r[2],
                "project_id": r[3], "summary": r[4],
                "retrieved_chunk_ids": r[5] if isinstance(r[5], list) else [],
                "recalled_memory_ids": r[6] if isinstance(r[6], list) else [],
                "inputs_digest": r[7],
                "created_at": r[8].isoformat() if r[8] else None,
            })
        return out

    # ── Episodic memory (KB-24) ─────────────────────────────────────────

    async def insert_memory(self, mem: AgentMemory) -> str:
        """Write one episodic-memory row. Idempotent on (namespace,
        content_hash): a re-fired capture event (the orchestrator emits
        request.failed from several paths) updates in place instead of
        duplicating. Returns the memory_id that now holds the row."""
        if mem.content_hash:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT memory_id FROM agent_memory "
                    "WHERE namespace=%s AND content_hash=%s LIMIT 1",
                    [mem.namespace, mem.content_hash],
                )
                existing = await cur.fetchone()
            if existing:
                return str(existing[0])
        sql = (
            "INSERT INTO agent_memory "
            "(memory_id, namespace, agent_id, request_id, project_id, kind, text, "
            " outcome, embedding, content_hash, unvetted, ttl_days) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s,%s,%s)"
        )
        async with self._pool.connection() as conn:
            await conn.execute(
                sql,
                [
                    mem.memory_id, mem.namespace, mem.agent_id, mem.request_id,
                    mem.project_id, mem.kind, mem.text, mem.outcome,
                    _vec_literal(mem.embedding) if mem.embedding else None,
                    mem.content_hash, 1 if mem.unvetted else 0, mem.ttl_days,
                ],
            )
        return mem.memory_id

    async def list_memory(
        self, namespace: str, *, kinds: list[str] | None = None,
        limit: int = 20, include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        """Recent episodes for a namespace, newest first. The plain
        (non-semantic) read — KB-25's ``recall_memory`` adds vector + time
        filters on top. Superseded rows (KB-27) are excluded by default."""
        clauses = ["namespace=%s"]
        params: list[Any] = [namespace]
        if kinds:
            clauses.append("kind = ANY(%s)")
            params.append(list(kinds))
        if not include_superseded:
            clauses.append("superseded_by IS NULL")
        params.append(limit)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT memory_id, namespace, agent_id, request_id, project_id, kind, "
                "text, outcome, unvetted, superseded_by, created_at, use_count "
                "FROM agent_memory WHERE " + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT %s",
                params,
            )
            rows = await cur.fetchall()
        return [self._row_to_memory_dict(r) for r in rows]

    async def count_memory(self, namespace: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM agent_memory WHERE namespace=%s", [namespace]
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        """Fetch a single memory row by id — including superseded ones, so a
        point-in-time chain stays reachable (KB-27)."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT memory_id, namespace, agent_id, request_id, project_id, kind, "
                "text, outcome, unvetted, superseded_by, created_at, use_count "
                "FROM agent_memory WHERE memory_id=%s", [memory_id]
            )
            row = await cur.fetchone()
        return self._row_to_memory_dict(row) if row else None

    async def supersede_memory(
        self, old_memory_id: str, new_memory_id: str, *, when: str | None = None,
    ) -> bool:
        """KB-27 — link ``old`` → ``new`` in the supersession chain and stamp
        when it happened. The old row immediately leaves default retrieval but
        stays reachable for as-of queries dated before ``when``. Idempotent-ish:
        re-superseding just updates the pointer. Returns True if a row changed.

        Guards: the new row must exist and be in the same namespace (you can't
        supersede an app's memory with another app's), and a row can't supersede
        itself."""
        if old_memory_id == new_memory_id:
            return False
        new_row = await self.get_memory(new_memory_id)
        old_row = await self.get_memory(old_memory_id)
        if new_row is None or old_row is None:
            return False
        if new_row["namespace"] != old_row["namespace"]:
            logger.warning(
                "supersede_cross_namespace_blocked",
                old=old_memory_id, new=new_memory_id,
            )
            return False
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE agent_memory SET superseded_by=%s, "
                "superseded_at=COALESCE(%s::timestamptz, now()) WHERE memory_id=%s",
                [new_memory_id, when, old_memory_id],
            )
        return int(cur.rowcount or 0) > 0

    async def search_memory(
        self, namespace: str, *, query_embedding: list[float] | None = None,
        days: int | None = None, as_of: str | None = None, limit: int = 8,
        bump_use: bool = True,
    ) -> list[dict[str, Any]]:
        """KB-25 — time-aware episodic recall over ``agent_memory``.

        Semantic when ``query_embedding`` is given (pgvector cosine), else
        recency-ordered. ``days`` keeps only episodes from the last N days.

        ``as_of`` (KB-27) is **point-in-time truth**: it returns the rows that
        were *live* at that instant — created at-or-before ``as_of`` and not yet
        superseded then (``superseded_at`` after ``as_of``, or never superseded).
        So a superseded fact still surfaces when you ask "as of <a date before it
        was replaced>". Without ``as_of`` (default retrieval) superseded rows are
        excluded outright. Recalled rows have ``use_count`` bumped (reinforcement
        signal, KB-30/31) unless ``bump_use=False``."""
        clauses = ["namespace=%s"]
        params: list[Any] = [namespace]
        if as_of:
            # Point-in-time: created by then AND still live then.
            clauses.append("created_at <= %s::timestamptz")
            params.append(as_of)
            clauses.append("(superseded_at IS NULL OR superseded_at > %s::timestamptz)")
            params.append(as_of)
        else:
            # Default retrieval: current truth only — drop superseded rows.
            clauses.append("superseded_by IS NULL")
        if days is not None and days > 0:
            clauses.append("created_at >= now() - make_interval(days => %s)")
            params.append(int(days))

        semantic = bool(query_embedding)
        if semantic:
            clauses.append("embedding IS NOT NULL")
            vec = _vec_literal(query_embedding or [])
            select = (
                "SELECT memory_id, namespace, agent_id, request_id, project_id, kind, "
                "text, outcome, unvetted, superseded_by, created_at, use_count, "
                "1 - (embedding <=> %s::vector) AS score "
                "FROM agent_memory WHERE " + " AND ".join(clauses)
                + " ORDER BY embedding <=> %s::vector LIMIT %s"
            )
            sql_params = [vec, *params, vec, int(limit)]
        else:
            select = (
                "SELECT memory_id, namespace, agent_id, request_id, project_id, kind, "
                "text, outcome, unvetted, superseded_by, created_at, use_count, "
                "NULL::float AS score "
                "FROM agent_memory WHERE " + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT %s"
            )
            sql_params = [*params, int(limit)]

        async with self._pool.connection() as conn:
            cur = await conn.execute(select, sql_params)
            rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = self._row_to_memory_dict(r)
            d["score"] = float(r[12]) if r[12] is not None else None
            out.append(d)
        if bump_use and out:
            ids = [d["memory_id"] for d in out]
            async with self._pool.connection() as conn:
                await conn.execute(
                    "UPDATE agent_memory SET use_count = use_count + 1, "
                    "last_used_at = now() WHERE memory_id = ANY(%s)", [ids]
                )
        return out

    # ── Consolidation (KB-26) ───────────────────────────────────────────

    async def distinct_memory_namespaces(self, kind: str = "episode") -> list[str]:
        """Namespaces that currently hold rows of ``kind`` — the consolidation
        job iterates these to know which apps have episodes to fold."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT namespace FROM agent_memory WHERE kind=%s", [kind]
            )
            return [r[0] for r in await cur.fetchall()]

    async def list_episodes_older_than(
        self, namespace: str, *, days: int, limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Raw episodes in ``namespace`` older than ``days`` (the consolidation
        window), oldest first. Only ``kind='episode'`` rows that haven't been
        superseded — summaries and already-folded rows are left alone."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT memory_id, namespace, agent_id, request_id, project_id, kind, "
                "text, outcome, unvetted, superseded_by, created_at, use_count "
                "FROM agent_memory "
                "WHERE namespace=%s AND kind='episode' AND superseded_by IS NULL "
                "AND created_at < now() - make_interval(days => %s) "
                "ORDER BY created_at ASC LIMIT %s",
                [namespace, int(days), int(limit)],
            )
            rows = await cur.fetchall()
        return [self._row_to_memory_dict(r) for r in rows]

    async def delete_memories(self, memory_ids: list[str]) -> int:
        """Expire (hard-delete) raw episodes after they've been folded into a
        summary. Returns the number removed."""
        if not memory_ids:
            return 0
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM agent_memory WHERE memory_id = ANY(%s)", [memory_ids]
            )
        return int(cur.rowcount or 0)

    async def create_promotion_candidate(
        self, *, namespace: str, summary: str, project_id: str | None = None,
        kind: str = "pattern", evidence_ids: list[str] | None = None,
        occurrences: int = 0, content_hash: str | None = None,
    ) -> str | None:
        """Propose a recurring pattern for memory→KB promotion (KB-26). Pending
        by default — NEVER auto-promoted. Idempotent on (namespace,
        content_hash): a re-run of the job returns None instead of duplicating
        an existing proposal."""
        candidate_id = f"promo-{uuid.uuid4().hex[:12]}"
        try:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    "INSERT INTO kb_promotion_candidates "
                    "(candidate_id, namespace, project_id, kind, summary, "
                    " evidence_ids, occurrences, content_hash) "
                    "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s) "
                    "ON CONFLICT (namespace, content_hash) DO NOTHING",
                    [
                        candidate_id, namespace, project_id, kind, summary,
                        json.dumps(evidence_ids or []), int(occurrences), content_hash,
                    ],
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("kb_promotion_candidate_failed", error=str(e))
            return None
        # rowcount 0 → ON CONFLICT skipped (duplicate proposal).
        return candidate_id if (cur.rowcount or 0) > 0 else None

    async def list_promotion_candidates(
        self, namespace: str | None = None, status: str = "pending", limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["status=%s"]
        params: list[Any] = [status]
        if namespace:
            clauses.append("namespace=%s")
            params.append(namespace)
        params.append(limit)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT candidate_id, namespace, project_id, kind, summary, "
                "evidence_ids, occurrences, status, created_at "
                "FROM kb_promotion_candidates WHERE " + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT %s",
                params,
            )
            rows = await cur.fetchall()
        return [self._row_to_promo_dict(r) for r in rows]

    async def get_promotion_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        """Fetch one promotion candidate by id (KB-28 review/approve path)."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT candidate_id, namespace, project_id, kind, summary, "
                "evidence_ids, occurrences, status, created_at "
                "FROM kb_promotion_candidates WHERE candidate_id=%s", [candidate_id]
            )
            row = await cur.fetchone()
        return self._row_to_promo_dict(row) if row else None

    async def set_promotion_status(
        self, candidate_id: str, status: str, *, reviewed_by: str | None = None,
    ) -> bool:
        """KB-28 — record a review decision (promoted | rejected) on a candidate.
        Append-only intent: a candidate is only acted on from ``pending`` (the
        WHERE guard makes a double-approve a no-op). Returns True if it moved."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE kb_promotion_candidates SET status=%s, reviewed_by=%s, "
                "reviewed_at=now() WHERE candidate_id=%s AND status='pending'",
                [status, reviewed_by, candidate_id],
            )
        return int(cur.rowcount or 0) > 0

    # ── Retention + forgetting (KB-30) ──────────────────────────────────

    async def expire_memory_by_ttl(self, namespace: str | None = None) -> int:
        """Delete episodic rows that have outlived their ``ttl_days`` (a row's
        own per-record TTL). Global, or scoped to one namespace. Returns the
        number expired."""
        clause = "ttl_days IS NOT NULL AND created_at < now() - make_interval(days => ttl_days)"
        params: list[Any] = []
        if namespace:
            clause += " AND namespace=%s"
            params.append(namespace)
        async with self._pool.connection() as conn:
            cur = await conn.execute(f"DELETE FROM agent_memory WHERE {clause}", params)
        return int(cur.rowcount or 0)

    async def prune_unused_memory(
        self, namespace: str, *, stale_days: int, max_use_count: int = 0,
    ) -> int:
        """Relevance-pruning: drop raw ``episode`` rows that have been recalled
        no more than ``max_use_count`` times and not touched in ``stale_days``
        (``last_used_at``, or ``created_at`` if never recalled). Summaries and
        anything actively used survive — this only sheds the unread landfill."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM agent_memory WHERE namespace=%s AND kind='episode' "
                "AND use_count <= %s "
                "AND COALESCE(last_used_at, created_at) < now() - make_interval(days => %s)",
                [namespace, int(max_use_count), int(stale_days)],
            )
        return int(cur.rowcount or 0)

    async def forget_subject(
        self, subject: str, *, namespace: str | None = None, actor: str = "system",
    ) -> dict[str, int]:
        """Right-to-be-forgotten (FR-015): erase every trace of a data subject
        across BOTH stores — episodic memory whose text mentions it, and KB
        documents whose title/uri/chunk-text mentions it (chunks + membership
        cascade via FK). Optionally scoped to one namespace. Audited. Returns
        per-store deleted counts.

        Matching is a case-insensitive substring — callers pass a specific
        identifier (an email, an id, a name), not a broad term."""
        like = f"%{subject}%"
        async with self._pool.connection() as conn, conn.transaction():
            mem_clause = "text ILIKE %s"
            mem_params: list[Any] = [like]
            if namespace:
                mem_clause += " AND namespace=%s"
                mem_params.append(namespace)
            cur_m = await conn.execute(
                f"DELETE FROM agent_memory WHERE {mem_clause}", mem_params
            )
            mem = int(cur_m.rowcount or 0)

            doc_clause = (
                "(title ILIKE %s OR uri ILIKE %s OR doc_id IN ("
                "  SELECT DISTINCT doc_id FROM kb_chunks WHERE text ILIKE %s))"
            )
            doc_params: list[Any] = [like, like, like]
            if namespace:
                doc_clause += " AND namespace=%s"
                doc_params.append(namespace)
            cur_d = await conn.execute(
                f"DELETE FROM kb_documents WHERE {doc_clause}", doc_params
            )
            docs = int(cur_d.rowcount or 0)
        counts = {"memory": mem, "documents": docs}
        await self.record_retention_audit(
            action="forget_subject", scope=subject, actor=actor, counts=counts,
        )
        logger.info(
            "kb_forget_subject", subject=subject, namespace=namespace,
            actor=actor, memory=mem, documents=docs,
        )
        return counts

    async def record_retention_audit(
        self, *, action: str, scope: str | None, counts: dict[str, int],
        actor: str = "system",
    ) -> str:
        audit_id = f"ret-{uuid.uuid4().hex[:12]}"
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO kb_retention_audit (audit_id, action, scope, actor, counts) "
                "VALUES (%s,%s,%s,%s,%s::jsonb)",
                [audit_id, action, scope, actor, json.dumps(counts)],
            )
        return audit_id

    async def list_retention_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT audit_id, action, scope, actor, counts, created_at "
                "FROM kb_retention_audit ORDER BY created_at DESC LIMIT %s",
                [int(limit)],
            )
            rows = await cur.fetchall()
        return [
            {
                "audit_id": r[0], "action": r[1], "scope": r[2], "actor": r[3],
                "counts": r[4] if isinstance(r[4], dict) else {},
                "created_at": r[5].isoformat() if r[5] else None,
            }
            for r in rows
        ]

    # ── Retrieval feedback (KB-31) ──────────────────────────────────────

    async def record_feedback(
        self, *, chunk_id: str, namespace: str, vote: int,
        request_id: str | None = None, created_by: str = "unknown",
    ) -> str:
        """Record a thumbs up (+1) / down (-1) on a chunk. Idempotent per
        (chunk, user): re-voting overwrites the prior vote + timestamp (so a
        flip from up→down is honoured and counts once)."""
        v = 1 if int(vote) >= 0 else -1
        feedback_id = f"fb-{uuid.uuid4().hex[:12]}"
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO kb_feedback "
                "(feedback_id, chunk_id, namespace, request_id, created_by, vote) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (chunk_id, created_by) DO UPDATE SET "
                "  vote=EXCLUDED.vote, created_at=now()",
                [feedback_id, chunk_id, namespace, request_id, created_by, v],
            )
        return feedback_id

    async def get_feedback_boosts(
        self, chunk_ids: list[str], *, halflife_days: float = 30.0,
    ) -> dict[str, float]:
        """Recency-weighted usefulness per chunk (KB-31): a vote's weight decays
        with a configurable half-life so fresh feedback dominates stale. Returns
        ``{chunk_id: boost}`` (positive = net liked, negative = net disliked).
        Chunks with no feedback are absent from the map."""
        if not chunk_ids:
            return {}
        halflife_s = max(1.0, float(halflife_days) * 86400.0)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT chunk_id, "
                "SUM(vote * exp(-EXTRACT(EPOCH FROM (now() - created_at)) / %s)) AS boost "
                "FROM kb_feedback WHERE chunk_id = ANY(%s) GROUP BY chunk_id",
                [halflife_s, list(chunk_ids)],
            )
            rows = await cur.fetchall()
        return {r[0]: float(r[1]) for r in rows if r[1] is not None}

    @staticmethod
    def _row_to_promo_dict(r: Any) -> dict[str, Any]:
        return {
            "candidate_id": r[0], "namespace": r[1], "project_id": r[2],
            "kind": r[3], "summary": r[4],
            "evidence_ids": r[5] if isinstance(r[5], list) else [],
            "occurrences": int(r[6] or 0), "status": r[7],
            "created_at": r[8].isoformat() if r[8] else None,
        }

    @staticmethod
    def _row_to_memory_dict(r: Any) -> dict[str, Any]:
        return {
            "memory_id": r[0], "namespace": r[1], "agent_id": r[2],
            "request_id": r[3], "project_id": r[4], "kind": r[5],
            "text": r[6], "outcome": r[7], "unvetted": bool(r[8]),
            "superseded_by": r[9],
            "created_at": r[10].isoformat() if r[10] else None,
            "use_count": int(r[11] or 0),
        }

    # ── Row mappers ─────────────────────────────────────────────────────

    @staticmethod
    def _row_to_doc(r: Any) -> KbDocument:
        return KbDocument(
            doc_id=r[0], namespace=r[1], project_id=r[2], source_type=r[3], title=r[4],
            uri=r[5], content_hash=r[6], sensitivity=r[7], status=r[8], superseded_by=r[9],
            version=r[10], curated_by=r[11], approved_at=r[12], created_at=r[13], ttl_days=r[14],
        )

    @staticmethod
    def _row_to_bucket(r: Any) -> KbBucket:
        return KbBucket(
            bucket_id=str(r[0]), name=r[1], slug=r[2], description=r[3], project_id=r[4],
            is_system=bool(r[5]), created_by=r[6], created_at=r[7],
        )


def _vec_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"
