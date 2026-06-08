"""Proposal auto-expire sweeper (HAI-29 / FR-036).

A background task that expires PENDING proposals whose TTL has elapsed, so an
unanswered approval queue can't pile up invisibly. Expiry is via the atomic CAS
(``transition_proposal`` pending→expired) so it (a) NEVER executes the action and
(b) can't clobber a proposal that just got confirmed/rejected in the same tick.
Emits ``proposal.expired``. Soft-fails — a sweep error is logged, the loop lives.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import structlog

from src.models.base import ProposalStatus

logger = structlog.get_logger()

_DEFAULT_INTERVAL_SECONDS = 60.0
# Pull pending proposals in a generous batch; they expire, so the live pending
# set stays small in practice.
_SCAN_LIMIT = 10_000


async def sweep_expired_once(state: Any, events: Any, *, now: datetime | None = None) -> list[str]:
    """One sweep: expire every pending proposal whose ttl has elapsed. Returns the
    expired proposal_ids. ``now`` is injectable for tests."""
    now = now or datetime.utcnow()
    expired: list[str] = []
    pending = await state.list_proposals(status="pending", limit=_SCAN_LIMIT)
    for p in pending:
        if p.created_at + timedelta(seconds=p.ttl_seconds) > now:
            continue  # not yet due
        # CAS: only expire if STILL pending (a concurrent confirm/reject wins).
        won = await state.transition_proposal(
            p.proposal_id,
            ProposalStatus.PENDING,
            ProposalStatus.EXPIRED,
            decided_at=now.isoformat(),
        )
        if not won:
            continue
        expired.append(p.proposal_id)
        logger.info("proposal_expired", proposal_id=p.proposal_id, action_type=p.action_type)
        if events is not None:
            try:
                await events.emit(
                    "proposal.expired",
                    {"proposal_id": p.proposal_id, "action_type": p.action_type},
                )
            except Exception as e:  # noqa: BLE001 — emit must not break the sweep
                logger.warning("proposal_expired_emit_failed", proposal_id=p.proposal_id, error=str(e))
    return expired


def make_proposal_expiry_sweeper(state: Any, events: Any, *, interval: float = _DEFAULT_INTERVAL_SECONDS):
    """Build the periodic sweeper coroutine. Register with
    ``asyncio.create_task(...)`` and cancel on shutdown."""

    async def sweeper() -> None:
        logger.info("proposal_expiry_sweeper_started", interval=interval)
        while True:
            try:
                await sweep_expired_once(state, events)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — one bad sweep can't kill the loop
                logger.warning("proposal_expiry_sweep_failed", error=str(e))
            await asyncio.sleep(interval)

    return sweeper
