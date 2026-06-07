"""KB-33 — ingestion dispatch (the async-ingest + scale-out seam).

Ingestion (embed + chunk + write) is the platform's heaviest KB operation. At
low volume it runs **inline** (await it, get the doc_id back) — the default and
what every existing call site expects. As volume grows it should move OFF the
request/event path so a big ingest never blocks an event handler or a worker.

This dispatcher is that seam. One method, ``submit(awaitable)``, with three
modes selected by ``knowledge_base.ingest_mode`` in config:

- ``inline``     — await and return the result (today's behaviour; unchanged).
- ``background`` — fire-and-forget via ``asyncio.create_task``; returns None.
                   The platform stays responsive; ingestion completes out of
                   band. Tasks are tracked so they aren't GC'd mid-flight.
- ``queue``      — hand off to an external worker pool (Redis + arq). Wired by
                   passing an ``enqueue`` callable; absent one, it degrades to
                   ``background`` so a misconfig never drops the ingest. This is
                   the documented scale-out path (see docs/kb-scale-out.md) —
                   the call sites don't change, only the dispatch mode does.

Soft-fail: a background/queue ingestion that raises is logged, never bubbled —
ingestion must not crash the loop that scheduled it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import structlog

logger = structlog.get_logger()

T = TypeVar("T")

_MODES = ("inline", "background", "queue")


class IngestionDispatcher:
    """Routes an ingestion awaitable to inline / background / queued execution.
    One per subsystem; constructed from ``settings.ingest_mode``."""

    def __init__(
        self, mode: str = "inline",
        enqueue: Callable[[Awaitable[Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._mode = mode if mode in _MODES else "inline"
        self._enqueue = enqueue
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def mode(self) -> str:
        return self._mode

    async def submit(self, awaitable: Awaitable[T]) -> T | None:
        """Run ``awaitable`` per the configured mode. ``inline`` returns its
        result; ``background``/``queue`` return None (fire-and-forget)."""
        if self._mode == "inline":
            return await awaitable

        if self._mode == "queue" and self._enqueue is not None:
            try:
                await self._enqueue(awaitable)
                return None
            except Exception as e:  # noqa: BLE001 — fall back, never drop
                logger.warning("kb_ingest_enqueue_failed_fallback", error=str(e))

        # background (or queue with no backend): fire-and-forget asyncio task.
        task = asyncio.create_task(self._run(awaitable))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return None

    async def _run(self, awaitable: Awaitable[Any]) -> None:
        try:
            await awaitable
        except Exception as e:  # noqa: BLE001 — background ingest never crashes the loop
            logger.warning("kb_background_ingest_failed", error=str(e))

    async def drain(self) -> None:
        """Await any in-flight background tasks (used in tests + on shutdown so
        a fire-and-forget ingest isn't lost when the process stops)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


__all__ = ["IngestionDispatcher"]
