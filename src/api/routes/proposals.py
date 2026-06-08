"""Proposal endpoints — the approval gate (HAI-23/26/27/28/30).

Create a PENDING proposal (the ONLY way a service principal can request a state
change), then a HUMAN confirms or rejects it. Two human paths:
  * Dashboard: POST /{id}/confirm | /{id}/reject  (JWT, get_current_user)
  * Channel:   POST /{id}/approve {token, decision} (a one-time token delivered to
               the human via the proposal.created event — never to the proposer)
Reads (list/get) are open to JWT or service token so Hermes can poll the queue.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.auth.service import get_current_user, get_principal, hash_service_token, principal_actor
from src.core.proposal_dispatcher import run_confirmed_proposal
from src.core.proposal_factory import new_proposal
from src.core.proposal_target import validate_proposal_target
from src.models.base import Proposal, ProposalStatus

router = APIRouter(prefix="/api/v1/proposals", tags=["proposals"])


class CreateProposalBody(BaseModel):
    action_type: str
    target_ref: str | None = None
    payload: dict[str, Any] | None = None
    ttl_seconds: int | None = None
    idempotency_key: str | None = None


class RejectProposalBody(BaseModel):
    reason: str | None = None


class ApproveProposalBody(BaseModel):
    token: str
    decision: Literal["confirm", "reject"] = "confirm"
    reason: str | None = None


def _public(p: Proposal) -> dict:
    # NOTE: never exposes approval_token_hash.
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


# ── shared confirm/reject logic (used by both the JWT and token paths) ────────

async def _load_pending_or_409(state, proposal_id: str) -> Proposal:
    """Fetch a proposal and require it be PENDING, with explicit terminal-state
    semantics (HAI-58 / FR-035d): a terminal proposal (failed/executed/rejected/
    expired) can NEVER be re-decided — the operator must create a new proposal to
    retry. A non-terminal-but-not-pending proposal (confirmed, mid-execution) is a
    transient race, not a dead end."""
    proposal = await state.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status == ProposalStatus.PENDING:
        return proposal
    if proposal.is_terminal:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Proposal {proposal_id} is in terminal state '{proposal.status}' and "
                "cannot be re-decided; create a new proposal to retry (FR-035d)."
            ),
        )
    raise HTTPException(
        status_code=409,
        detail=f"Proposal {proposal_id} is '{proposal.status}', not pending.",
    )


async def _do_confirm(request: Request, proposal_id: str, decided_by: str) -> Proposal:
    state = request.app.state.state_store
    registry = request.app.state.proposal_registry
    events = getattr(request.app.state, "events", None)

    proposal = await _load_pending_or_409(state, proposal_id)

    # HAI-59 — target integrity: the target may have been deleted/archived during
    # the approval window. Re-validate at confirm time; if invalid, fail the
    # proposal with the reason (terminal — operator re-proposes) rather than
    # executing against a stale target.
    target_reason = await validate_proposal_target(state, proposal)
    if target_reason is not None:
        now = datetime.utcnow().isoformat()
        won = await state.transition_proposal(
            proposal_id, ProposalStatus.PENDING, ProposalStatus.FAILED,
            decided_by=decided_by, decided_at=now, error=target_reason, executed_at=now,
        )
        if not won:
            current = await state.get_proposal(proposal_id)
            now_status = current.status if current else "gone"
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is no longer pending (now '{now_status}')",
            )
        if events is not None:
            await events.emit(
                "proposal.failed",
                {"proposal_id": proposal_id, "error": target_reason, "target_invalid": True},
            )
        return await state.get_proposal(proposal_id)

    won = await state.transition_proposal(
        proposal_id, ProposalStatus.PENDING, ProposalStatus.CONFIRMED,
        decided_by=decided_by, decided_at=datetime.utcnow().isoformat(),
    )
    if not won:
        current = await state.get_proposal(proposal_id)
        raise HTTPException(
            status_code=409,
            detail=f"Proposal is no longer pending (now '{current.status if current else 'gone'}')",
        )
    if events is not None:
        await events.emit(
            "proposal.confirmed", {"proposal_id": proposal_id, "decided_by": decided_by}
        )

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
    return await state.get_proposal(proposal_id)


async def _do_reject(
    request: Request, proposal_id: str, decided_by: str, reason: str | None
) -> Proposal:
    state = request.app.state.state_store
    events = getattr(request.app.state, "events", None)

    await _load_pending_or_409(state, proposal_id)

    won = await state.transition_proposal(
        proposal_id, ProposalStatus.PENDING, ProposalStatus.REJECTED,
        decided_by=decided_by, decided_at=datetime.utcnow().isoformat(), error=reason,
    )
    if not won:
        current = await state.get_proposal(proposal_id)
        raise HTTPException(
            status_code=409,
            detail=f"Proposal is no longer pending (now '{current.status if current else 'gone'}')",
        )
    if events is not None:
        await events.emit(
            "proposal.rejected",
            {"proposal_id": proposal_id, "decided_by": decided_by, "reason": reason},
        )
    return await state.get_proposal(proposal_id)


# ── routes ───────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_proposal(
    body: CreateProposalBody,
    request: Request,
    principal: dict = Depends(get_principal),   # JWT or service token
):
    """Create a pending proposal. No side effects beyond persisting + emitting
    ``proposal.created`` (FR-031). Idempotent on ``idempotency_key`` (FR-035a).

    Generates a one-time approval token (HAI-30): only its hash is stored; the RAW
    token is included in the ``proposal.created`` event (delivered to the human via
    push/dashboard) but NEVER in this API response — so the proposer can't get it.
    """
    action_type = (body.action_type or "").strip()
    if not action_type:
        raise HTTPException(status_code=400, detail="action_type is required")

    state = request.app.state.state_store
    events = getattr(request.app.state, "events", None)

    if body.idempotency_key:
        existing = await state.get_proposal_by_idempotency_key(body.idempotency_key)
        if existing is not None:
            return {"data": _public(existing), "meta": {"idempotent_replay": True}, "error": None}

    proposal, raw_approval_token = new_proposal(
        action_type=action_type,
        proposed_by=principal_actor(principal),
        target_ref=body.target_ref,
        payload=body.payload,
        ttl_seconds=body.ttl_seconds,
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
                # Delivered to the HUMAN (push/dashboard). Hermes only gets the API
                # response below, which omits it.
                "approval_token": raw_approval_token,
            },
        )
    return {"data": _public(proposal), "meta": None, "error": None}


@router.post("/{proposal_id}/confirm")
async def confirm_proposal(
    proposal_id: str,
    request: Request,
    user: dict = Depends(get_current_user),   # HUMAN-ONLY (FR-038)
):
    """Confirm + execute a pending proposal (dashboard path). pending→confirmed
    (atomic CAS) → run via the guarded dispatcher → executed | failed."""
    final = await _do_confirm(request, proposal_id, principal_actor(user))
    return {"data": _public(final), "meta": None, "error": None}


@router.post("/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: str,
    request: Request,
    body: RejectProposalBody | None = None,
    user: dict = Depends(get_current_user),   # HUMAN-ONLY (FR-038)
):
    """Reject a pending proposal — the action never runs (FR-033)."""
    reason = (body.reason if body else None) or None
    final = await _do_reject(request, proposal_id, principal_actor(user), reason)
    return {"data": _public(final), "meta": None, "error": None}


@router.post("/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: str,
    body: ApproveProposalBody,
    request: Request,
):
    """Approve (confirm or reject) a proposal with the one-time channel token
    (HAI-30 / FR-038) — no dashboard JWT needed. The token IS the authority: it was
    delivered only to the human via the proposal.created event, is single-use, and
    a service-token bearer is blocked from this path by the write-block. Still human
    authority — Hermes never has the token, so it can't self-approve.
    """
    state = request.app.state.state_store
    if await state.get_proposal(proposal_id) is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    # Spend the token atomically (single-use). Wrong/used token → 403.
    ok = await state.consume_approval_token(proposal_id, hash_service_token(body.token))
    if not ok:
        raise HTTPException(status_code=403, detail="Invalid or already-used approval token")

    if body.decision == "reject":
        final = await _do_reject(request, proposal_id, "channel:one-time-token", body.reason)
    else:
        final = await _do_confirm(request, proposal_id, "channel:one-time-token")
    return {"data": _public(final), "meta": None, "error": None}


@router.get("")
async def list_proposals(
    request: Request,
    status: str | None = None,
    action_type: str | None = None,
    proposed_by: str | None = None,
    limit: int = 100,
    principal: dict = Depends(get_principal),   # JWT or service token (Hermes polls)
):
    """List proposals (newest first) with optional status / action_type /
    proposed_by filters (FR-034, FR-080)."""
    state = request.app.state.state_store
    items = await state.list_proposals(
        status=status, action_type=action_type, proposed_by=proposed_by, limit=limit
    )
    return {"data": [_public(p) for p in items], "meta": {"count": len(items)}, "error": None}


@router.get("/{proposal_id}")
async def get_proposal(
    proposal_id: str,
    request: Request,
    principal: dict = Depends(get_principal),
):
    """Fetch one proposal (FR-034)."""
    state = request.app.state.state_store
    proposal = await state.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return {"data": _public(proposal), "meta": None, "error": None}
