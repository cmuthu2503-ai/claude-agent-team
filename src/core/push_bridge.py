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

Scope: the three real request/deploy events PLUS the full ``proposal.*`` lifecycle
(HAI-61), so Hermes is pinged when an action needs approval and when it resolves.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

# Request/deploy events verified to be really emitted: request.failed /
# request.completed (orchestrator) and deploy_health.anomaly_detected (anomaly
# sweeper). NO request.deployed (never emitted).
_REQUEST_PUSH_EVENTS: frozenset[str] = frozenset(
    {"request.failed", "request.completed", "deploy_health.anomaly_detected"}
)

# HAI-61 — the proposal lifecycle. proposal.created tells Hermes "an action needs
# human approval"; the terminal ones tell it how its request resolved, so it can
# stop waiting / react. (Producers: the proposals route + expiry sweeper + crash
# recovery.)
_PROPOSAL_PUSH_EVENTS: frozenset[str] = frozenset(
    {
        "proposal.created",
        "proposal.confirmed",
        "proposal.executed",
        "proposal.failed",
        "proposal.rejected",
        "proposal.expired",
    }
)

DEFAULT_PUSH_EVENTS: frozenset[str] = _REQUEST_PUSH_EVENTS | _PROPOSAL_PUSH_EVENTS

# SECURITY (HAI-30 + HAI-61): the proposal.created event carries the raw one-time
# approval token, which is delivered to the HUMAN's dashboard/event stream. The
# push bridge forwards to HERMES — the *proposer*. Forwarding the token here would
# let Hermes self-approve its own proposal, breaking the gate's core invariant.
# So these keys are stripped from every forwarded payload, no exceptions.
_REDACT_KEYS: frozenset[str] = frozenset({"approval_token", "approval_token_hash"})


def _summarize_proposal(event_type: str, data: dict[str, Any]) -> str:
    pid = data.get("proposal_id") or "?"
    action = data.get("action_type")
    tail = f" [{action}]" if action else ""
    if event_type == "proposal.created":
        tgt = data.get("target_ref")
        return f"Approval needed: {action or 'action'} ({pid})" + (f" on {tgt}" if tgt else "")
    if event_type == "proposal.confirmed":
        return f"Proposal {pid} confirmed by {data.get('decided_by') or '?'}"
    if event_type == "proposal.executed":
        rr = data.get("result_ref")
        return f"Proposal {pid} executed" + (f" → {rr}" if rr else "") + tail
    if event_type == "proposal.failed":
        err = str(data.get("error") or "").strip()
        return f"Proposal {pid} FAILED" + (f": {err[:160]}" if err else "") + tail
    if event_type == "proposal.rejected":
        reason = str(data.get("reason") or "").strip()
        return f"Proposal {pid} rejected" + (f": {reason[:160]}" if reason else "")
    if event_type == "proposal.expired":
        return f"Proposal {pid} expired unactioned" + tail
    return f"{event_type} ({pid})"


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
    if event_type.startswith("proposal."):
        return _summarize_proposal(event_type, data)
    return f"{event_type} ({rid})"


def _build_payload(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Concise payload linking back to the request/proposal id (FR-072). Strips
    secret keys (the one-time approval token) so the forwarded copy can never be
    used to self-approve — see _REDACT_KEYS."""
    detail = {k: v for k, v in data.items() if k not in _REDACT_KEYS}
    return {
        "source": "agent-team",
        "event": event_type,
        "request_id": data.get("request_id"),
        "proposal_id": data.get("proposal_id"),
        "project_id": data.get("project_id"),
        "summary": _summarize(event_type, data),
        "detail": detail,
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
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
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
