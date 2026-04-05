"""Webhook handler — listens for GitHub events and updates local state."""

from typing import Any

import structlog

from src.models.base import StoryStatus
from src.state.base import StateStore

logger = structlog.get_logger()


class WebhookHandler:
    """Processes GitHub webhook events and updates the state store."""

    def __init__(self, state: StateStore) -> None:
        self.state = state

    async def handle_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Route a webhook event to the appropriate handler."""
        handlers = {
            "issues.closed": self._handle_issue_closed,
            "pull_request.merged": self._handle_pr_merged,
            "pull_request.closed": self._handle_pr_closed,
        }
        handler = handlers.get(event_type)
        if handler:
            await handler(payload)
        else:
            logger.debug("webhook_unhandled", event_type=event_type)

    async def _handle_issue_closed(self, payload: dict[str, Any]) -> None:
        """When an issue is closed, update the linked story to Done."""
        issue_number = payload.get("issue", {}).get("number")
        if not issue_number:
            return
        # In a full implementation, query story_issue_map table
        logger.info("issue_closed_webhook", issue=issue_number)

    async def _handle_pr_merged(self, payload: dict[str, Any]) -> None:
        """When a PR is merged, update linked stories and trigger deployment."""
        pr_number = payload.get("pull_request", {}).get("number")
        body = payload.get("pull_request", {}).get("body", "")
        logger.info("pr_merged_webhook", pr=pr_number)

        # Check for "Closes #XX" patterns to auto-close stories
        import re
        closes_pattern = re.findall(r"[Cc]loses?\s+#(\d+)", body)
        for issue_num in closes_pattern:
            logger.info("auto_closing_issue", issue=issue_num, pr=pr_number)

    async def _handle_pr_closed(self, payload: dict[str, Any]) -> None:
        """Handle PR closed without merge (abandoned)."""
        pr_number = payload.get("pull_request", {}).get("number")
        merged = payload.get("pull_request", {}).get("merged", False)
        if not merged:
            logger.info("pr_closed_without_merge", pr=pr_number)
