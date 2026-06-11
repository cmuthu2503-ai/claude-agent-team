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
        # HAI-66 — finalize actions
        "prd.finalize",
        "apispec.finalize",
        "tasks.finalize",
        "epics.finalize",
        "features.finalize",
    }
)

# ── HAI-64 — auto-approve policy (operator-configured standing authorization) ──
# By default EVERY gated action waits for a human confirm. An operator can declare
# a subset that still needs a human and let the rest auto-approve, via the env var
# PROPOSAL_REQUIRE_APPROVAL (comma-separated action_types), resolved once at
# startup onto app.state.proposal_require_approval:
#   - UNSET  → None → safe default: ALL gated actions require a human (original
#              posture; fully backward compatible).
#   - SET    → only the listed action_types require a human; every other gated
#              action auto-approves and executes immediately. Empty string ("")
#              → no action requires a human (fully autonomous).
#   e.g. PROPOSAL_REQUIRE_APPROVAL=deploy  → only `deploy` is human-gated.
#
# Auto-approval is recorded with decided_by=AUTO_APPROVE_ACTOR, which is NOT a
# service identity ("service:…"), so the HAI-50 self-approval audit ("no service
# principal ever decides its own proposal") still holds — this is a standing
# HUMAN/operator authorization expressed as config, not Hermes approving itself.
AUTO_APPROVE_ACTOR = "policy:auto-approve"
REQUIRE_APPROVAL_ENV = "PROPOSAL_REQUIRE_APPROVAL"


def parse_require_approval(raw: str | None) -> frozenset[str] | None:
    """Parse the PROPOSAL_REQUIRE_APPROVAL value. ``None`` (env unset) → ``None``
    (gate everything — the safe default). A present value (even empty) → the
    explicit set of action_types that require a human (empty = fully autonomous)."""
    if raw is None:
        return None
    return frozenset(a.strip() for a in raw.split(",") if a.strip())


def requires_human_approval(
    action_type: str, require_approval: frozenset[str] | None
) -> bool:
    """Does this gated action need a HUMAN confirm (vs. auto-approve on creation)?

    ``require_approval`` is the resolved policy (``parse_require_approval`` output):
    ``None`` → default, every gated action needs a human; otherwise only the
    listed action_types do."""
    if require_approval is None:
        return True
    return action_type in require_approval


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
