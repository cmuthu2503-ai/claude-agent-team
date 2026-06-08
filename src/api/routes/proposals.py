"""Proposal endpoints (HAI-23 / FR-031).

Create a PENDING proposal — the ONLY way a service principal (Hermes) can request
a state change (the write-block, HAI-51, lets a service token reach exactly this
route and nothing else mutating). Creating a proposal has NO side effects beyond
persisting it and emitting ``proposal.created``; it executes only after a human
confirms it (HAI-26). Confirm/reject and list land in later P2 tasks.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.auth.service import get_principal, principal_actor
from src.models.base import Proposal

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
        "ttl_seconds": p.ttl_seconds,
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
