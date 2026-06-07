"""KB-30 — retention + forgetting automation (the lifecycle's back half).

Memory is a lifecycle, not a store: write → consolidate → retrieve → reinforce →
**forget**. KB-26 handles consolidation; this module handles the forgetting that
keeps episodic memory from growing without bound and honours data-subject
erasure:

1. **TTL expiry** — episodes past their per-row ``ttl_days`` are deleted.
2. **Relevance pruning** — raw ``episode`` rows never (or barely) recalled and
   untouched for ``stale_days`` are shed. Summaries (KB-26) and actively-used
   memory survive, so the distilled value is kept while the landfill is cleared.
3. **Right-to-be-forgotten** — ``store.forget_subject`` (called from the API)
   erases a subject across both stores; every deletion here and there is written
   to ``kb_retention_audit``.

The sweep is deterministic, soft-fails per-namespace, and runs on the same
periodic-asyncio-task shape as the AET-31 anomaly sweeper / KB-26 consolidation
job, wired from ``src/main.py``'s lifespan.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()

# Tunables (env-overridable, like the sibling background jobs).
RETENTION_INTERVAL_S = int(os.getenv("KB_RETENTION_INTERVAL_S", str(12 * 3600)))
MEMORY_PRUNE_STALE_DAYS = int(os.getenv("KB_MEMORY_PRUNE_STALE_DAYS", "90"))
MEMORY_PRUNE_MAX_USE = int(os.getenv("KB_MEMORY_PRUNE_MAX_USE", "0"))


@dataclass
class RetentionResult:
    namespaces: int = 0
    ttl_expired: int = 0
    pruned: int = 0


async def run_retention_sweep(
    subsystem: Any, *, stale_days: int = MEMORY_PRUNE_STALE_DAYS,
    max_use_count: int = MEMORY_PRUNE_MAX_USE,
) -> RetentionResult:
    """One full pass: TTL expiry + relevance pruning across every memory
    namespace. Writes a single ``kb_retention_audit`` summary row when anything
    was actually forgotten. Per-namespace soft-fail."""
    store = subsystem.knowledge_store
    res = RetentionResult()
    namespaces = await store.distinct_memory_namespaces("episode")
    res.namespaces = len(namespaces)
    for ns in namespaces:
        try:
            res.ttl_expired += await store.expire_memory_by_ttl(ns)
            res.pruned += await store.prune_unused_memory(
                ns, stale_days=stale_days, max_use_count=max_use_count,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("kb_retention_namespace_failed", namespace=ns, error=str(e))
    if res.ttl_expired or res.pruned:
        try:
            await store.record_retention_audit(
                action="sweep", scope=None,
                counts={"ttl_expired": res.ttl_expired, "pruned": res.pruned,
                        "namespaces": res.namespaces},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("kb_retention_audit_failed", error=str(e))
        logger.info(
            "kb_retention_sweep", namespaces=res.namespaces,
            ttl_expired=res.ttl_expired, pruned=res.pruned,
        )
    return res


def make_retention_job(subsystem: Any) -> Callable[[], Awaitable[None]]:
    """Return the periodic background task callable (wrapped in
    ``asyncio.create_task`` by the lifespan). No-ops when the KB is down."""

    async def _loop() -> None:
        # Stagger the first run well after boot (and after consolidation).
        await asyncio.sleep(min(300, RETENTION_INTERVAL_S))
        while True:
            if subsystem is not None and getattr(subsystem, "available", False):
                try:
                    await run_retention_sweep(subsystem)
                except Exception as e:  # noqa: BLE001
                    logger.warning("kb_retention_loop_error", error=str(e))
            await asyncio.sleep(RETENTION_INTERVAL_S)

    return _loop
