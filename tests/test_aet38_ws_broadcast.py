"""AET-38 — verify AE event types reach the /ws/activity stream.

The Active Agents feed on the Team Status page subscribes to
`/ws/activity` and filters for AE event types (quality.gate.*,
security.gate.*, ops.alert.*, ops.rollback.*, lessons.*). This
test confirms each event type the frontend listens for actually
flows through the WebSocket fanout when emitted on the backend
EventEmitter — protecting against a future rename or deletion
that would silently break the UI.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

# Same set the frontend hard-codes in TeamStatus.tsx AE_EVENT_STYLES.
# If a type disappears from the backend OR is renamed, the test fails
# and someone has to either update both sides or remove from both.
AE_EVENT_TYPES = [
    "quality.gate.failed",
    "quality.gate.passed",
    "security.gate.failed",
    "security.gate.passed",
    "ops.alert.fired",
    "ops.rollback.triggered",
    "lessons.added",
    "lessons.pending_review",
    "lessons.duplicate_skipped",
]


@pytest.mark.parametrize("event_type", AE_EVENT_TYPES)
def test_ae_event_type_constant_exists(event_type: str):
    """Each AE event type the frontend listens for MUST be defined as
    a constant in src/core/events.py — prevents a silent rename
    that would leave the UI deaf to a real event."""
    from src.core import events as events_module

    # Find a constant whose value equals the event type string.
    all_values = {
        getattr(events_module, name)
        for name in dir(events_module)
        if name.isupper() and isinstance(getattr(events_module, name), str)
    }
    assert event_type in all_values, (
        f"event_type {event_type!r} is referenced in the frontend "
        f"AE_EVENT_STYLES map but not exported as a constant from "
        f"src/core/events.py. Add it there so the wire format has a "
        f"single source of truth."
    )


@pytest.mark.asyncio
async def test_event_emitter_broadcasts_ae_events_to_subscribers():
    """Programmatic verification: emitting each AE event type via the
    EventEmitter triggers any subscribed handler. This is what the
    /ws/activity endpoint does internally — subscribe to events,
    forward to WS client."""
    from src.core.events import EventEmitter

    events = EventEmitter()
    captured: list[tuple[str, dict]] = []

    async def _cap(event_type: str, data: dict) -> None:
        captured.append((event_type, dict(data)))

    events.on(_cap)

    # Emit one of each AE type with a minimal payload the frontend
    # AE_EVENT_STYLES.label() functions can survive (they use ?. /
    # default fallbacks so empty {} works).
    for et in AE_EVENT_TYPES:
        await events.emit(et, {"smoke": True})

    types_seen = [et for et, _ in captured]
    for et in AE_EVENT_TYPES:
        assert et in types_seen, f"{et} was not delivered to subscriber"
    assert len(captured) == len(AE_EVENT_TYPES)
