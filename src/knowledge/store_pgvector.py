"""PgVectorStore — semantic search on PostgreSQL + pgvector (KB-02).

Operates against the chunk table (``kb_chunks`` by default; created by KB-03)
which carries an ``embedding vector(N)`` column, ``chunk_id``, ``namespace``,
and a ``metadata jsonb`` column. Cosine distance (``<=>``) is the metric;
scores are returned as ``1 - distance`` so higher = closer.

Vectors are bound as the pgvector string literal (``'[1,2,3]'::vector``) —
the most portable form, independent of adapter quirks.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from src.knowledge.interfaces import VectorHit, VectorStore

logger = structlog.get_logger()


def _vec_literal(vector: list[float]) -> str:
    """pgvector text literal: [1,2,3]."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _safe_filter_sql(
    filters: dict[str, Any] | None, alias: str = ""
) -> tuple[str, list[Any]]:
    """Build an optional ``AND metadata->>key = value`` clause set. Only
    string-equality metadata filters are supported in Phase 1; keys are
    whitelisted to identifier-safe characters to avoid injection. ``alias``
    prefixes the column (e.g. ``c`` → ``c.metadata``) for joined queries."""
    if not filters:
        return "", []
    col = f"{alias}.metadata" if alias else "metadata"
    clauses: list[str] = []
    params: list[Any] = []
    for key, value in filters.items():
        if not key.replace("_", "").isalnum():
            logger.warning("kb_vector_filter_key_rejected", key=key)
            continue
        clauses.append(f"{col}->>'{key}' = %s")
        params.append(str(value))
    sql = (" AND " + " AND ".join(clauses)) if clauses else ""
    return sql, params


class PgVectorStore(VectorStore):
    def __init__(self, pool: Any, table: str = "kb_chunks", dimensions: int = 1024) -> None:
        self._pool = pool
        self._table = table
        self._dim = dimensions

    async def health(self) -> bool:
        try:
            async with self._pool.connection() as conn:
                cur = await conn.execute("SELECT 1")
                await cur.fetchone()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("kb_vector_health_failed", error=str(e))
            return False

    async def upsert(
        self,
        namespace: str,
        items: list[tuple[str, list[float], dict[str, Any]]],
    ) -> int:
        if not items:
            return 0
        sql = (
            f"INSERT INTO {self._table} (chunk_id, namespace, embedding, metadata) "
            f"VALUES (%s, %s, %s::vector, %s::jsonb) "
            f"ON CONFLICT (chunk_id) DO UPDATE SET "
            f"  namespace = EXCLUDED.namespace, "
            f"  embedding = EXCLUDED.embedding, "
            f"  metadata = EXCLUDED.metadata"
        )
        rows = [
            (chunk_id, namespace, _vec_literal(vec), json.dumps(meta or {}))
            for chunk_id, vec, meta in items
        ]
        async with self._pool.connection() as conn:
            cur = conn.cursor()
            await cur.executemany(sql, rows)
        return len(rows)

    async def search(
        self,
        namespace: str,
        query_vector: list[float],
        top_k: int,
        bucket_ids: list[str] | None = None,
        approved_only: bool = True,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorHit]:
        filt_sql, filt_params = _safe_filter_sql(filters, alias="c")
        qvec = _vec_literal(query_vector)
        join = (
            " JOIN kb_documents d ON d.doc_id = c.doc_id" if approved_only else ""
        )
        where = ["c.namespace = %s"]
        params: list[Any] = [qvec, namespace]   # qvec is first for the SELECT
        if bucket_ids:
            where.append("c.bucket_ids && %s::uuid[]")
            params.append(list(bucket_ids))
        if approved_only:
            where.append("d.status = 'approved'")
        # Cosine distance via <=>; score = 1 - distance (higher = closer).
        sql = (
            f"SELECT c.chunk_id, c.namespace, c.metadata, "
            f"       1 - (c.embedding <=> %s::vector) AS score "
            f"FROM {self._table} c{join} "
            f"WHERE {' AND '.join(where)}{filt_sql} "
            f"ORDER BY c.embedding <=> %s::vector "
            f"LIMIT %s"
        )
        params = [*params, *filt_params, qvec, top_k]
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
        hits: list[VectorHit] = []
        for chunk_id, ns, meta, score in rows:
            hits.append(
                VectorHit(
                    chunk_id=chunk_id,
                    score=float(score),
                    namespace=ns,
                    metadata=meta if isinstance(meta, dict) else {},
                )
            )
        return hits

    async def delete(self, namespace: str, chunk_ids: list[str]) -> int:
        if not chunk_ids:
            return 0
        sql = f"DELETE FROM {self._table} WHERE namespace = %s AND chunk_id = ANY(%s)"
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, [namespace, chunk_ids])
            return int(cur.rowcount or 0)
