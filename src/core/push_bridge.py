"""Outbound push bridge (HAI-17/18/54 — FR-070..072, FR-074, FR-075, NFR-002).

Forwards a CURATED set of internal events to a configured webhook (a Hermes
inbound channel), so Hermes is *pinged* the moment something notable happens
instead of only learning on its next poll.

Design (HAI-54 — bounded, non-blocking, durable-by-pull):
  * The EventEmitter handler only ENQUEUES (``put_nowait``) — it never does
    network I/O, so it can't block or slow the emit loop (NFR-002).
  * A background worker drains the queue and delivers with BOUNDED RETRY.
  * The queue is BOUNDED (``max_buffer``): if a dead webhook backs it up, new
    events are dropped (logged), not accumulated forever.
  * Push is best-effort-low-latency; the durability guarantee is Hermes's
    scheduled PULL (FR-073) — any dropped/missed alert is reconciled on the next
    poll, so the gap window is ≤ the pull interval (FR-074).

Scope in P1 (FR-075): only the three real, already-emitted events. ``proposal.*``
forwarding is added in P2 (HAI-61).
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

# Verified to be really emitted today: request.failed / request.completed
# (orchestrator) and deploy_health.anomaly_detected (anomaly sweeper). NO
# request.deployed (never emitted), NO proposal.* (don't exist until P2).
DEFAULT_PUSH_EVENTS: frozenset[str] = frozenset(
    {"request.failed", "request.completed", "deploy_health.anomaly_detected"}
)


def _summarize(event_type: str, data: dict[str, Any]) -> str:
    rid = data.get("request_id") or "?"
    if event_type == "request.failed":
        err = str(data.get("error") or data.get("final_error") or "").strip()
        return f"Request {rid} FAILED" + (f": {err[:160]}" if err else "")
    if event_type == "request.completed":
        return f"Request {rid} completed"
    if event_type == "deploy_health.anomaly_detected":
        env = data.get("environment") or data.get("env") or "?"
        verdict = data.get("verdict") or "ANOMALY"
        return f"Deploy anomaly in {env}: {verdict}"
    return f"{event_type} ({rid})"


def _build_payload(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Concise payload linking back to the request id (FR-072)."""
    return {
        "source": "agent-team",
        "event": event_type,
        "request_id": data.get("request_id"),
        "project_id": data.get("project_id"),
        "summary": _summarize(event_type, data),
        "detail": data,
        "ts": datetime.utcnow().isoformat() + "Z",
    }


class PushBridge:
    """Buffered, retrying outbound forwarder. Register ``bridge.handler`` with
    ``EventEmitter.on`` and call ``start()``; ``stop()`` on shutdown."""

    def __init__(
        self,
        webhook_url: str,
        *,
        events: frozenset[str] | set[str] | None = None,
        secret: str | None = None,
        timeout: float = 5.0,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        max_buffer: int = 1000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = webhook_url
        self._forwarded = frozenset(events) if events else DEFAULT_PUSH_EVENTS
        self._headers = {"X-Push-Secret": secret} if secret else {}
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = retry_backoff
        self._transport = transport
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_buffer)
        self._worker: asyncio.Task | None = None

    async def handler(self, event_type: str, data: dict[str, Any]) -> None:
        """EventEmitter handler — ENQUEUE only (non-blocking, bounded). Never does
        network I/O on the emit path."""
        if event_type not in self._forwarded:
            return
        try:
            self._queue.put_nowait(_build_payload(event_type, data))
        except asyncio.QueueFull:
            logger.warning(
                "push_bridge_buffer_full_dropped",
                event_type=event_type,
                request_id=data.get("request_id"),
                hint="webhook backed up; Hermes pull (FR-073) reconciles the gap",
            )

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="push_bridge_worker")

    async def stop(self) -> None:
        if self._worker and not self._worker.done():
            self._worker.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._worker

    async def _run(self) -> None:
        while True:
            payload = await self._queue.get()
            try:
                await self._deliver_with_retry(payload)
            except Exception as e:  # noqa: BLE001 — worker must never die
                logger.warning("push_bridge_worker_error", error=str(e))
            finally:
                self._queue.task_done()

    async def _deliver_with_retry(self, payload: dict[str, Any]) -> bool:
        """Deliver one payload with bounded retry. Returns True on success.
        Soft-fail: exhausting retries drops the event (logged) — pull reconciles."""
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                    resp = await client.post(self._url, json=payload, headers=self._headers)
                if resp.status_code < 400:
                    return True
                logger.warning(
                    "push_bridge_delivery_status", status=resp.status_code, attempt=attempt,
                    request_id=payload.get("request_id"),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "push_bridge_delivery_error", error=str(e), attempt=attempt,
                    request_id=payload.get("request_id"),
                )
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff * (attempt + 1))
        logger.warning(
            "push_bridge_dropped_after_retries",
            request_id=payload.get("request_id"),
            dropped_event=payload.get("event"),
            hint="exhausted retries; Hermes pull (FR-073) reconciles the gap",
        )
        return False
