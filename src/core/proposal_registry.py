"""Gated-action registry (HAI-24 / FR-037).

The set of ``action_type``s that MUST go through the approval gate, and the map
from each to the async handler that actually performs it. WHICH actions are gated
is config (``GATED_ACTION_TYPES``); the handlers are code, registered by the P3
lifecycle tasks (HAI-36+). Reads are never gated.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger()

# FR-037 — every state-changing action a service principal can request. A
# Proposal's action_type must be one of these; reads are never proposals.
GATED_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "project.create",
        "project.brief.set",
        "prd.generate",
        "apispec.generate",
        "epics.generate",
        "features.generate",
        "tasks.generate",
        "buildplan.generate",
        "task.dispatch",
        "request.submit",
        "request.cancel",
        "agent.model.set",
        "deploy",
        "rollback",
    }
)

# A handler runs a confirmed proposal: (proposal, ctx) -> result dict. The result
# may carry {"result_ref": "<id>"} pointing at what it produced (e.g. a request_id).
ProposalHandler = Callable[..., Awaitable[dict[str, Any]]]


class ProposalActionRegistry:
    """Maps gated action_types to their execution handlers. P3 registers the real
    handlers; until then an action_type is gated but has no handler (the
    dispatcher fails such a proposal cleanly rather than running anything)."""

    def __init__(self) -> None:
        self._handlers: dict[str, ProposalHandler] = {}

    @staticmethod
    def is_gated(action_type: str) -> bool:
        return action_type in GATED_ACTION_TYPES

    def register(self, action_type: str, handler: ProposalHandler) -> None:
        if action_type not in GATED_ACTION_TYPES:
            logger.warning("proposal_handler_for_ungated_action", action_type=action_type)
        self._handlers[action_type] = handler

    def get_handler(self, action_type: str) -> ProposalHandler | None:
        return self._handlers.get(action_type)

    def has_handler(self, action_type: str) -> bool:
        return action_type in self._handlers

    def registered_action_types(self) -> list[str]:
        return sorted(self._handlers)
