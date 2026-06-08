"""Central confirmation guard / proposal dispatcher (HAI-25 / FR-035).

The SINGLE chokepoint through which a gated action executes. The invariant: a
gated action runs ONLY via a ``confirmed`` proposal, and ONLY through a registered
handler. Every path that would mutate gated state — the confirm endpoint (HAI-26)
and, later, the autonomous loops rerouted in P4 (HAI-63) — goes through here, so
the "nothing runs without a confirmed proposal" rule can't be bypassed.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.core.proposal_registry import ProposalActionRegistry
from src.models.base import Proposal, ProposalStatus

logger = structlog.get_logger()


class ProposalGuardError(RuntimeError):
    """Raised when something attempts to execute a proposal that is not in the
    ``confirmed`` state — a hard guard against bypassing the gate (FR-035)."""


async def run_confirmed_proposal(
    proposal: Proposal,
    registry: ProposalActionRegistry,
    ctx: Any,
) -> dict[str, Any]:
    """Execute a CONFIRMED proposal's action. Returns
    ``{"status": "executed"|"failed", "result_ref": str|None, "error": str|None}``.

    GUARD: raises ``ProposalGuardError`` if the proposal isn't confirmed — callers
    must transition pending→confirmed first. A confirmed proposal whose action_type
    has no registered handler fails cleanly (FR-035d) rather than running anything.
    """
    if proposal.status != ProposalStatus.CONFIRMED:
        raise ProposalGuardError(
            f"Proposal {proposal.proposal_id} is '{proposal.status}', not 'confirmed' — "
            "a gated action can only execute via a confirmed proposal (FR-035)."
        )

    handler = registry.get_handler(proposal.action_type)
    if handler is None:
        logger.warning(
            "proposal_no_handler",
            proposal_id=proposal.proposal_id,
            action_type=proposal.action_type,
        )
        return {
            "status": "failed",
            "result_ref": None,
            "error": f"No handler registered for action_type '{proposal.action_type}'.",
        }

    try:
        result = await handler(proposal, ctx)
        result_ref = result.get("result_ref") if isinstance(result, dict) else None
        logger.info(
            "proposal_executed",
            proposal_id=proposal.proposal_id,
            action_type=proposal.action_type,
            result_ref=result_ref,
        )
        return {"status": "executed", "result_ref": result_ref, "error": None}
    except Exception as e:  # noqa: BLE001 — failure is a normal terminal state (FR-035d)
        logger.warning(
            "proposal_execution_failed",
            proposal_id=proposal.proposal_id,
            action_type=proposal.action_type,
            error=str(e),
        )
        return {"status": "failed", "result_ref": None, "error": f"{type(e).__name__}: {e}"}
