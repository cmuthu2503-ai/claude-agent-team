"""Proposal endpoints (HAI-23 / FR-031).

Create a PENDING proposal — the ONLY way a service principal (Hermes) can request
a state change (the write-block, HAI-51, lets a service token reach exactly this
route and nothing else mutating). Creating a proposal has NO side effects beyond
persisting it and emitting ``proposal.created``; it executes only after a human
confirms it (HAI-26). Confirm/reject and list land in later P2 tasks.
"""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.auth.service import get_current_user, get_principal, principal_actor
from src.core.proposal_dispatcher import run_confirmed_proposal
from src.models.base import Proposal, ProposalStatus

router = APIRouter(prefix="/api/v1/proposals", tags=["proposals"])

_DEFAULT_TTL_SECONDS = 86400  # 24h


class CreateProposalBody(BaseModel):
    action_type: str
    target_ref: str | None = None
    payload: dict[str, Any] | None = None
    ttl_seconds: int | None = None
    idempotency_key: str | None = None


def _public(p: Proposal) -> dict:
    return {
        "proposal_id": p.proposal_id,
        "action_type": p.action_type,
        "target_ref": p.target_ref,
        "payload": p.payload,
        "status": str(p.status),
        "proposed_by": p.proposed_by,
        "created_at": p.created_at.isoformat(),
        "decided_by": p.decided_by,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "executed_at": p.executed_at.isoformat() if p.executed_at else None,
        "ttl_seconds": p.ttl_seconds,
        "result_ref": p.result_ref,
        "error": p.error,
    }


@router.post("", status_code=201)
async def create_proposal(
    body: CreateProposalBody,
    request: Request,
    # JWT or service token — Hermes (service) reaches this via the write-block
    # allow-list; humans can also propose.
    principal: dict = Depends(get_principal),
):
    """Create a pending proposal. No side effects beyond persisting + emitting
    ``proposal.created`` (FR-031). Idempotent on ``idempotency_key`` (FR-035a):
    a repeat returns the existing proposal instead of a duplicate."""
    action_type = (body.action_type or "").strip()
    if not action_type:
        raise HTTPException(status_code=400, detail="action_type is required")

    state = request.app.state.state_store
    events = getattr(request.app.state, "events", None)

    # FR-035a — dedup on idempotency key (guards client retries / re-prompts).
    if body.idempotency_key:
        existing = await state.get_proposal_by_idempotency_key(body.idempotency_key)
        if existing is not None:
            return {
                "data": _public(existing),
                "meta": {"idempotent_replay": True},
                "error": None,
            }

    proposal = Proposal(
        proposal_id=f"prop-{uuid.uuid4().hex[:12]}",
        action_type=action_type,
        target_ref=body.target_ref,
        payload=body.payload or {},
        proposed_by=principal_actor(principal),
        ttl_seconds=body.ttl_seconds if (body.ttl_seconds and body.ttl_seconds > 0) else _DEFAULT_TTL_SECONDS,
        idempotency_key=body.idempotency_key,
    )
    await state.create_proposal(proposal)

    if events is not None:
        await events.emit(
            "proposal.created",
            {
                "proposal_id": proposal.proposal_id,
                "action_type": proposal.action_type,
                "proposed_by": proposal.proposed_by,
                "target_ref": proposal.target_ref,
            },
        )
    return {"data": _public(proposal), "meta": None, "error": None}


@router.post("/{proposal_id}/confirm")
async def confirm_proposal(
    proposal_id: str,
    request: Request,
    # HUMAN-ONLY (FR-038): get_current_user accepts a JWT only — a service token
    # is not a JWT, so it fails auth here and can never self-approve. (The
    # write-block middleware also rejects a service token on this path.)
    user: dict = Depends(get_current_user),
):
    """Confirm a pending proposal and execute it. pending→confirmed (atomic CAS)
    → run the action via the central guarded dispatcher (HAI-25) → executed |
    failed. Emits proposal.confirmed, then proposal.executed | proposal.failed.
    """
    state = request.app.state.state_store
    registry = request.app.state.proposal_registry
    events = getattr(request.app.state, "events", None)

    proposal = await state.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != ProposalStatus.PENDING:
        raise HTTPException(
            status_code=409, detail=f"Proposal is '{proposal.status}', not pending"
        )

    decided_by = principal_actor(user)
    # Atomic pending→confirmed — one winner if two confirms race or expiry fires.
    won = await state.transition_proposal(
        proposal_id,
        ProposalStatus.PENDING,
        ProposalStatus.CONFIRMED,
        decided_by=decided_by,
        decided_at=datetime.utcnow().isoformat(),
    )
    if not won:
        current = await state.get_proposal(proposal_id)
        raise HTTPException(
            status_code=409,
            detail=f"Proposal is no longer pending (now '{current.status if current else 'gone'}')",
        )
    if events is not None:
        await events.emit("proposal.confirmed", {"proposal_id": proposal_id, "decided_by": decided_by})

    # Execute through the central guard (refuses anything not confirmed).
    confirmed = await state.get_proposal(proposal_id)
    outcome = await run_confirmed_proposal(confirmed, registry, request)
    await state.update_proposal(
        proposal_id,
        {
            "status": outcome["status"],
            "result_ref": outcome["result_ref"],
            "error": outcome["error"],
            "executed_at": datetime.utcnow().isoformat(),
        },
    )
    if events is not None:
        ev = "proposal.executed" if outcome["status"] == "executed" else "proposal.failed"
        await events.emit(
            ev,
            {
                "proposal_id": proposal_id,
                "result_ref": outcome["result_ref"],
                "error": outcome["error"],
            },
        )

    final = await state.get_proposal(proposal_id)
    return {"data": _public(final), "meta": None, "error": None}
