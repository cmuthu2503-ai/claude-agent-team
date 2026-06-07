"""Shared async Postgres connection pool for the KB stores (KB-02).

Both ``PgVectorStore`` and ``PostgresFtsStore`` run against the SAME pool —
one set of connections, configured once to register the pgvector type and
ensure the ``vector`` extension exists. psycopg is imported lazily so this
module (and the interfaces) stay importable without the dep present.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


async def _configure(conn: Any) -> None:
    """Per-connection setup: register the pgvector type adapter so vectors
    round-trip as Python objects. Extension creation happens once at pool
    open (see ``open_pool``), not per connection."""
    try:
        from pgvector.psycopg import register_vector_async  # type: ignore[import-untyped]

        await register_vector_async(conn)
    except Exception as e:  # noqa: BLE001
        # If the extension isn't created yet the registration can fail; the
        # pool still works for non-vector statements. open_pool ensures the
        # extension first, so this is just defensive.
        logger.debug("pgvector_register_skipped", error=str(e))


async def open_pool(dsn: str, pool_min: int, pool_max: int) -> Any:
    """Create + open an ``AsyncConnectionPool``, ensure the pgvector
    extension exists, and return the pool. Raises on connection failure —
    the caller (subsystem factory) catches and soft-fails."""
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=pool_min,
        max_size=pool_max,
        open=False,
        configure=_configure,
        # Fail fast on a dead Postgres rather than blocking boot.
        timeout=10,
    )
    await pool.open(wait=True, timeout=10)

    # Ensure the extension once, up front, so _configure's per-connection
    # registration always succeeds afterward.
    async with pool.connection() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    logger.info("kb_pg_pool_opened", min=pool_min, max=pool_max)
    return pool
