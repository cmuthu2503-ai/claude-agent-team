"""WebSocket event emitter — broadcasts real-time events to connected clients."""

import asyncio
import json
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger()


class EventEmitter:
    """Emits events to WebSocket subscribers and logs them."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._global_subscribers: set[asyncio.Queue] = set()

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

    def get_subscriber_count(self, request_id: str | None = None) -> int:
        if request_id:
            return len(self._subscribers.get(request_id, set()))
        return len(self._global_subscribers)
