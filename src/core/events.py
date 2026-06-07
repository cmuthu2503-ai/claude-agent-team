"""WebSocket event emitter — broadcasts real-time events to connected clients.

Event type constants
--------------------
Use the ``OPS_*`` constants below when emitting ops_heal_agent events so
string literals don't drift between the orchestrator, the WebSocket handler,
and the SystemHealthPill UI component.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger()

# ── ops_heal_agent event type constants ─────────────────────────────────────
# Emitted by orchestrator._trigger_ops_monitor() after each deployment.
# Consumed by the SystemHealthPill component (CommandCenter) and any WebSocket
# subscribers that want to react to post-deploy health verdicts.

#: Stack is healthy — all checks passed (HTTP 200, disk/mem OK, logs clean).
OPS_HEALTHY = "ops.healthy"

#: At least one check degraded or error-log issues found; may be self-healing.
OPS_ISSUE_DETECTED = "ops.issue_detected"

#: ops_heal_agent monitoring cycle started (emitted before the LLM runs).
OPS_MONITORING_STARTED = "ops.monitoring_started"

#: ops_heal_agent raised an internal exception (agent-level error, not infra).
OPS_ERROR = "ops.error"

# ── Phase AE-3 quality_guardian event type constants ────────────────────────
# Emitted by the workflow runner's quality_guardian_approval gate (AET-06)
# after evaluating policy_check's structured verdict (AET-03). Consumed by:
#   - the WebSocket clients (AET-07 UI surfaces the blocked-by-quality-gate
#     chip on the Request detail page when QUALITY_GATE_FAILED fires)
#   - structlog audit pipeline (every emit logs the rule_ids that fired)
# Payload contract: src/core/quality_gate.py::build_quality_gate_payload

#: policy_check returned verdict='BLOCK' — at least one enforce-severity
#: rule violated. The workflow halts at the gate; the next rework cycle
#: receives the violations + fix_hints as input.
QUALITY_GATE_FAILED = "quality.gate.failed"

#: policy_check returned verdict='PASS' (clean) or 'PASS_WITH_WARNINGS'
#: (warn-severity rules fired but enforce did not). Workflow advances;
#: the inner `verdict` field on the payload tells subscribers which flavor.
QUALITY_GATE_PASSED = "quality.gate.passed"

# Server-side async handler: receives (event_type, data) and may perform
# side effects (e.g. PDB-25 mapping request status → task status). Errors
# are logged and swallowed so a failing handler can't break event delivery.
EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class EventEmitter:
    """Emits events to WebSocket subscribers and logs them."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._global_subscribers: set[asyncio.Queue] = set()
        # Server-side handlers, registered at app boot via on(...). Run after
        # WS delivery so a slow handler can't stall the broadcast.
        self._handlers: list[EventHandler] = []

    def subscribe(self, request_id: str | None = None) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        if request_id:
            if request_id not in self._subscribers:
                self._subscribers[request_id] = set()
            self._subscribers[request_id].add(queue)
        else:
            self._global_subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue, request_id: str | None = None) -> None:
        if request_id and request_id in self._subscribers:
            self._subscribers[request_id].discard(queue)
        self._global_subscribers.discard(queue)

    def on(self, handler: EventHandler) -> None:
        """Register a server-side async handler. Called once per emit AFTER
        WebSocket subscribers receive the event. Handlers should be tolerant —
        they may run on every emit regardless of event type and decide
        internally which events they care about."""
        self._handlers.append(handler)

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        event = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        }
        logger.debug("event_emitted", event_type=event_type, request_id=data.get("request_id"))

        # Send to request-specific subscribers
        request_id = data.get("request_id")
        if request_id and request_id in self._subscribers:
            for queue in self._subscribers[request_id]:
                await queue.put(event)

        # Send to global subscribers (Command Center)
        for queue in self._global_subscribers:
            await queue.put(event)

        # Server-side handlers — best-effort, swallow errors so a buggy
        # handler can't tank the rest of the event flow.
        for handler in self._handlers:
            try:
                await handler(event_type, data)
            except Exception as e:
                logger.warning("event_handler_failed", event_type=event_type, error=str(e))

    def get_subscriber_count(self, request_id: str | None = None) -> int:
        if request_id:
            return len(self._subscribers.get(request_id, set()))
        return len(self._global_subscribers)
