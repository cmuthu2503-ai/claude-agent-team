"""Crash-recovery reconciliation for stranded proposals (HAI-57 / FR-035c).

A confirmed proposal executes in two steps (see the confirm endpoint): first an
atomic CAS pending→confirmed, then run the action and ``update_proposal`` it to
``executed`` | ``failed``. A crash *between* those steps strands the proposal in
``confirmed`` with ``executed_at = NULL`` — the human said yes, but whether the
action ran (and with what outcome) is unknown.

On startup we reconcile every such proposal. We mark them **failed**, we do NOT
re-drive them: the action registry makes no idempotency guarantee, so blindly
re-running a confirmed deploy / commit / mutation could double-apply a side
effect that may already have partially happened. Failing is the safe, never-
strand outcome — and per HAI-58 a ``failed`` proposal cannot be re-confirmed, so
the operator consciously re-proposes if they still want the action. Emits
``proposal.failed`` (with ``reconciled: True``) so the normal push / observability
path handles it like any other failure.

The whole pass is an atomic CAS per row (``transition_proposal`` confirmed→failed),
so it can't clobber a proposal that some other actor legitimately moves out of
``confirmed`` concurrently, and re-running recovery is idempotent (a second pass
finds nothing left in ``confirmed``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from src.models.base import ProposalStatus

logger = structlog.get_logger()

# Pull confirmed proposals in a generous batch. In a healthy system this set is
# transient (confirm → executed happens in one request), so recovery normally
# finds zero rows.
_SCAN_LIMIT = 10_000

RECONCILE_ERROR = (
    "reconciled on startup: confirmed but never executed (interrupted by a "
    "restart); re-propose to retry"
)


async def reconcile_confirmed_proposals_once(
    state: Any, events: Any = None, *, now: datetime | None = None
) -> list[str]:
    """One reconciliation pass: mark every ``confirmed``-but-unexecuted proposal
    ``failed`` so none is left stranded after a crash. Returns the reconciled
    proposal_ids. ``now`` is injectable for tests."""
    now = now or datetime.utcnow()
    reconciled: list[str] = []

    confirmed = await state.list_proposals(status="confirmed", limit=_SCAN_LIMIT)
    for p in confirmed:
        # CAS confirmed→failed: only acts if STILL confirmed (a concurrent
        # executor that finishes first wins and we skip).
        won = await state.transition_proposal(
            p.proposal_id,
            ProposalStatus.CONFIRMED,
            ProposalStatus.FAILED,
            error=RECONCILE_ERROR,
            executed_at=now.isoformat(),
        )
        if not won:
            continue
        reconciled.append(p.proposal_id)
        logger.warning(
            "proposal_reconciled_failed",
            proposal_id=p.proposal_id,
            action_type=p.action_type,
        )
        if events is not None:
            try:
                await events.emit(
                    "proposal.failed",
                    {
                        "proposal_id": p.proposal_id,
                        "action_type": p.action_type,
                        "error": RECONCILE_ERROR,
                        "reconciled": True,
                    },
                )
            except Exception as e:  # noqa: BLE001 — emit must not break recovery
                logger.warning(
                    "proposal_reconcile_emit_failed", proposal_id=p.proposal_id, error=str(e)
                )

    if reconciled:
        logger.warning("proposal_crash_recovery_complete", count=len(reconciled))
    return reconciled
