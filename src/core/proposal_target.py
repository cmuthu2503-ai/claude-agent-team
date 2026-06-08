"""Target-integrity validation at confirm time (HAI-59 / FR-035e).

A proposal can sit in the queue for up to its TTL (default 24h). In that window
the thing it targets can change underneath it — a project gets archived or
deleted. Confirming then would execute an action against a stale / illegal
target. So at confirm time (not just create time) we re-validate ``target_ref``
exists and is in a legal state, and fail the proposal with a clear reason if not
(the operator re-proposes against a valid target, per HAI-58).

Scope (FR-035e): the project-scoped gated actions, whose ``target_ref`` is a
project id. Validation is best-effort and FAIL-OPEN on a lookup error — a DB blip
must not block a human-confirmed action; if the target really is gone the handler
fails cleanly downstream anyway. Action types we don't know how to validate pass
through (return None).
"""

from __future__ import annotations

from typing import Any

import structlog

from src.models.base import ProjectStatus, Proposal

logger = structlog.get_logger()

# Gated actions whose target_ref is a project id. (project.create has no
# pre-existing target; task.dispatch/request.*/deploy/rollback target other
# entities and are left to best-effort / handler-time checks.)
_PROJECT_TARGET_ACTIONS: frozenset[str] = frozenset(
    {
        "project.brief.set",
        "prd.generate",
        "apispec.generate",
        "epics.generate",
        "features.generate",
        "tasks.generate",
        "buildplan.generate",
    }
)


async def validate_proposal_target(state: Any, proposal: Proposal) -> str | None:
    """Return a human-readable failure reason if the proposal's target is missing
    or in an illegal state at confirm time, else None (ok to execute)."""
    action_type = proposal.action_type
    ref = proposal.target_ref

    if action_type in _PROJECT_TARGET_ACTIONS:
        if not ref:
            return f"action '{action_type}' requires a target project, but target_ref is empty"
        try:
            project = await state.get_project(ref)
        except Exception as e:  # noqa: BLE001 — fail-open: a lookup blip must not block
            logger.warning("proposal_target_lookup_failed", target_ref=ref, error=str(e))
            return None
        if project is None:
            return (
                f"target project '{ref}' no longer exists "
                "(deleted during the approval window); re-propose against a live project"
            )
        if project.status == ProjectStatus.ARCHIVED:
            return f"target project '{ref}' is archived; unarchive it and re-propose"

    return None
