"""In-process approval-gate interception (HAI-63 / FR-035).

The service-token write-block (HAI-51) stops a *remote* principal (Hermes) from
mutating state except via a proposal. But the platform also has IN-PROCESS callers
that mutate gated state without going through any HTTP route — e.g. ``auto_dispatch``
calling ``orchestrator.submit()`` (BPD-24), or ``ops_heal`` triggering a rollback
(AET-31). A middleware can't see those; they'd bypass the gate entirely.

This module is the chokepoint those callers route through. In **governed mode**
(the default — "nothing moves without my approval") a gated action is turned into
a PENDING proposal and NOT executed; a human confirms it later, at which point the
normal dispatcher (HAI-25) runs it. In **autonomous mode** the action executes
immediately (legacy behavior), for operators who opt out of the gate.

P3's HAI-46/47 apply this to the concrete call sites (task.dispatch, rollback);
HAI-63 is the reusable primitive + the governed-mode switch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from src.core.proposal_factory import new_proposal
from src.core.proposal_registry import ProposalActionRegistry

logger = structlog.get_logger()


@dataclass
class GateOutcome:
    """Result of routing an in-process action through the gate.

    ``gated=True``  → a proposal was created and is awaiting human approval; the
                      action did NOT run. ``proposal_id`` identifies it.
    ``gated=False`` → the action ran now (autonomous mode or an ungated action);
                      ``result`` is whatever ``execute`` returned.
    """

    gated: bool
    proposal_id: str | None = None
    result: Any = None
    deduped: bool = False


async def submit_gated_action(
    state: Any,
    events: Any,
    *,
    governed: bool,
    action_type: str,
    proposed_by: str,
    target_ref: str | None = None,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    execute: Callable[[], Awaitable[Any]],
) -> GateOutcome:
    """Route an in-process state change through the approval gate.

    In governed mode a *gated* ``action_type`` becomes a pending proposal (emits
    ``proposal.created``) instead of executing. Otherwise ``execute()`` runs now.
    ``idempotency_key`` dedups repeat proposals (same key → existing proposal),
    so a retrying caller can't queue duplicates.
    """
    # Ungated actions never gate (defensive — callers shouldn't pass these), and
    # autonomous mode runs everything inline.
    if not governed or not ProposalActionRegistry.is_gated(action_type):
        return GateOutcome(gated=False, result=await execute())

    if idempotency_key:
        existing = await state.get_proposal_by_idempotency_key(idempotency_key)
        if existing is not None:
            return GateOutcome(gated=True, proposal_id=existing.proposal_id, deduped=True)

    proposal, raw_token = new_proposal(
        action_type=action_type,
        proposed_by=proposed_by,
        target_ref=target_ref,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    await state.create_proposal(proposal)
    logger.info(
        "in_process_action_gated",
        action_type=action_type,
        proposal_id=proposal.proposal_id,
        proposed_by=proposed_by,
        target_ref=target_ref,
    )
    if events is not None:
        try:
            await events.emit(
                "proposal.created",
                {
                    "proposal_id": proposal.proposal_id,
                    "action_type": proposal.action_type,
                    "proposed_by": proposal.proposed_by,
                    "target_ref": proposal.target_ref,
                    # For the HUMAN's surface; the push bridge redacts it (HAI-61).
                    "approval_token": raw_token,
                    "in_process": True,
                },
            )
        except Exception as e:  # noqa: BLE001 — emit must not break the caller
            logger.warning(
                "in_process_gate_emit_failed", proposal_id=proposal.proposal_id, error=str(e)
            )

    return GateOutcome(gated=True, proposal_id=proposal.proposal_id)
