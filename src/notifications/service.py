"""Notification service — creates notifications and emits WebSocket events."""

import uuid
from datetime import datetime

import structlog

from src.core.events import EventEmitter
from src.models.base import Notification
from src.notifications.catalog import NotificationCatalog
from src.state.base import StateStore

logger = structlog.get_logger()


class NotificationService:
    """Receives events, formats notifications, stores in DB, emits via WebSocket."""

    def __init__(self, state: StateStore, events: EventEmitter) -> None:
        self.state = state
        self.events = events
        self.catalog = NotificationCatalog()

    async def notify(self, event_id: str, context: dict) -> Notification | None:
        """Create and deliver a notification for the given event."""
        event = self.catalog.get(event_id)
        if not event:
            logger.warning("unknown_notification_event", event_id=event_id)
            return None

        title = self.catalog.format_title(event_id, context)
        message = self.catalog.format_message(event_id, context)

        notification = Notification(
            notification_id=str(uuid.uuid4()),
            event_id=event_id,
            severity=event.severity,
            title=title,
            message=message,
            request_id=context.get("request_id"),
            user_id=context.get("user_id"),
        )

        await self.state.create_notification(notification)
        logger.info("notification_created", event_id=event_id, severity=event.severity)

        # Emit to WebSocket
        await self.events.emit("notification.new", {
            "notification_id": notification.notification_id,
            "event_id": event_id,
            "severity": event.severity,
            "title": title,
            "message": message,
            "request_id": context.get("request_id"),
        })

        # Emit toast for warning/critical
        if event.show_toast:
            await self.events.emit("notification.toast", {
                "severity": event.severity,
                "title": title,
                "message": message,
            })

        return notification
