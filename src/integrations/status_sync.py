"""JIRA status synchronization handler.

Listens for ``project.task_status.changed`` events and transitions the
corresponding JIRA issue to the mapped status. Runs asynchronously via
the EventEmitter — transitions never block the platform.
"""

from __future__ import annotations

import logging
from typing import Any

from src.state.sqlite_store import SQLiteStateStore
from src.integrations.jira import JiraCloudClient

logger = logging.getLogger(__name__)

# Default mapping: platform task_status → JIRA transition name
DEFAULT_STATUS_MAP: dict[str, str] = {
    "in_progress": "In Progress",
    "review":      "In Review",
    "testing":     "In Testing",
    "deployed":    "Done",
    "failed":      "To Do",
    "cancelled":   "Cancelled",
}


class JiraStatusSyncHandler:
    """Event handler for task status → JIRA transition synchronization."""

    def __init__(
        self,
        state: SQLiteStateStore,
        jira: JiraCloudClient,
        status_map: dict[str, str] | None = None,
    ) -> None:
        self._state = state
        self._jira = jira
        self._status_map = status_map or DEFAULT_STATUS_MAP

    async def __call__(self, event_name: str, payload: dict[str, Any]) -> None:
        """Entry point — called by EventEmitter for each matching event."""
        if event_name != "project.task_status.changed":
            return
        task_id = payload.get("task_id")
        new_status = payload.get("new_status")
        if not task_id or not new_status:
            return

        target_status = self._status_map.get(new_status)
        if not target_status:
            return  # no mapping → skip silently

        project_id = payload.get("project_id", "")
        mapping = await self._state.get_integration_mapping(
            project_id, "task", task_id, "jira",
        )
        if mapping is None:
            return  # task was never pushed to JIRA

        result = await self._jira.transition_issue(
            mapping.external_ref, target_status,
        )
        if result.ok:
            mapping.sync_status = "ok"
            mapping.sync_error = None
            logger.info(
                "jira.status_synced task=%s issue=%s status=%s",
                task_id, mapping.external_ref, target_status,
            )
        else:
            mapping.sync_status = "error"
            mapping.sync_error = result.error
            logger.warning(
                "jira.status_sync_failed task=%s issue=%s target=%s error=%s",
                task_id, mapping.external_ref, target_status, result.error,
            )
        await self._state.upsert_integration_mapping(mapping)
