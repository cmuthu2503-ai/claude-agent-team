"""PDB-25 — request.* event → project_task.task_status mapping.

When a Request was created from a project task via the dispatcher (i.e.
`requests.source_task_id` is set), the per-project Story Board needs to
reflect that task's current state. We piggy-back on the existing
`request.status_changed`, `request.completed`, and `request.failed`
events and translate them into `project_tasks.task_status` updates.

The handler is intentionally tolerant: it looks up the Request, ignores
ones without a source_task_id, and skips events it doesn't know how to
map. A failure here must NOT block event broadcasting.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.models.base import TaskStatus
from src.state.base import StateStore

logger = structlog.get_logger()


# request status string → task status enum
_REQUEST_STATUS_TO_TASK_STATUS: dict[str, TaskStatus] = {
    # PRD §8.3 mapping table.
    "received": TaskStatus.DISPATCHED,
    "analyzing": TaskStatus.DISPATCHED,
    "in_progress": TaskStatus.IN_PROGRESS,
    "review_pending": TaskStatus.REVIEW,
    "testing": TaskStatus.TESTING,
    "completed": TaskStatus.DEPLOYED,
    "failed": TaskStatus.FAILED,
    "cancelled": TaskStatus.CANCELLED,
}


def make_project_task_status_handler(state: StateStore):
    """Returns an EventEmitter-compatible async handler bound to the state
    store. We use a closure rather than a class so the EventEmitter doesn't
    need to know about StateStore types."""

    async def handler(event_type: str, data: dict[str, Any]) -> None:
        # Cheap early-exit for the 99% of events that don't matter to us.
        if not event_type.startswith("request."):
            return

        request_id = data.get("request_id")
        if not request_id:
            return

        # Need to look up the Request to find source_task_id. If a future
        # version stores source_task_id directly on the event payload we
        # can skip this query.
        req = await state.get_request(request_id)
        if req is None or not req.source_task_id:
            return

        # Translate event → task_status. Some events carry the new status
        # in `data["status"]`; others (completed, failed) don't but are
        # themselves the signal.
        new_task_status: TaskStatus | None = None
        if event_type == "request.completed":
            new_task_status = TaskStatus.DEPLOYED
        elif event_type == "request.failed":
            new_task_status = TaskStatus.FAILED
        elif event_type == "request.cancelled":
            new_task_status = TaskStatus.CANCELLED
        elif event_type == "request.status_changed":
            status = data.get("status")
            if isinstance(status, str):
                new_task_status = _REQUEST_STATUS_TO_TASK_STATUS.get(status)

        if new_task_status is None:
            return

        await state.set_task_status(req.source_task_id, new_task_status)
        logger.info(
            "project_task_status_updated",
            task_id=req.source_task_id,
            request_id=request_id,
            new_status=str(new_task_status),
            from_event=event_type,
        )

    return handler
