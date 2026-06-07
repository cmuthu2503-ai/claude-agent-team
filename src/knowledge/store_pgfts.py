"""PostgresFtsStore — lexical search via Postgres full-text search (KB-02).

The deterministic arm of hybrid retrieval. Uses ``to_tsvector`` /
``plainto_tsquery`` over the chunk text with ``ts_rank`` scoring. Shares the
same connection pool as ``PgVectorStore``.

Phase 1 computes the tsvector on the fly (``to_tsvector('english', text)``).
If lexical quality or latency becomes a bottleneck (eval-driven), KB-03 can
add a stored, GIN-indexed ``tsv`` column — a drop-in upgrade behind this
same interface.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.knowledge.interfaces import KeywordHit, KeywordStore
from src.knowledge.store_pgvector import _safe_filter_sql

logger = structlog.get_logger()

# Default text-search configuration. English stemming is fine for the
# platform's docs; configurable later if multilingual corpora appear.
_TS_CONFIG = "english"


class PostgresFtsStore(KeywordStore):
    def __init__(self, pool: Any, table: str = "kb_chunks") -> None:
        self._pool = pool
        self._table = table

    async def health(self) -> bool:
        try:
            async with self._pool.connection() as conn:
                cur = await conn.execute("SELECT 1")
                await cur.fetchone()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("kb_keyword_health_failed", error=str(e))
            return False

    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int,
        bucket_ids: list[str] | None = None,
        approved_only: bool = True,
        filters: dict[str, Any] | None = None,
    ) -> list[KeywordHit]:
        if not query or not query.strip():
            return []
        filt_sql, filt_params = _safe_filter_sql(filters, alias="c")
        join = " JOIN kb_documents d ON d.doc_id = c.doc_id" if approved_only else ""
        scope: list[str] = []
        scope_params: list[Any] = []
        if bucket_ids:
            scope.append("c.bucket_ids && %s::uuid[]")
            scope_params.append(list(bucket_ids))
        if approved_only:
            scope.append("d.status = 'approved'")
        scope_sql = ("".join(f" AND {s}" for s in scope))
        sql = (
            f"SELECT c.chunk_id, c.namespace, c.metadata, "
            f"       ts_rank(to_tsvector(%s, c.text), plainto_tsquery(%s, %s)) AS score "
            f"FROM {self._table} c{join} "
            f"WHERE c.namespace = %s{filt_sql}{scope_sql} "
            f"  AND to_tsvector(%s, c.text) @@ plainto_tsquery(%s, %s) "
            f"ORDER BY score DESC "
            f"LIMIT %s"
        )
        params = [
            _TS_CONFIG, _TS_CONFIG, query,        # SELECT ts_rank
            namespace, *filt_params, *scope_params,  # WHERE namespace + filters + scope
            _TS_CONFIG, _TS_CONFIG, query,        # WHERE @@ match
            top_k,
        ]
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
        return [
            KeywordHit(
                chunk_id=chunk_id,
                score=float(score),
                namespace=ns,
                metadata=meta if isinstance(meta, dict) else {},
            )
            for chunk_id, ns, meta, score in rows
        ]
