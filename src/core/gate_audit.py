"""Approval-gate audit query (HAI-50 / M1, M5).

The headline guarantee of the whole integration: a service principal (Hermes) can
NEVER execute a gated action without a human in the loop. This query produces the
evidence for that claim from the proposal trail, so an operator (or an auditor) can
verify M1 at any time rather than taking it on faith.

The proof is structural: every gated action executes only via a CONFIRMED proposal
(the dispatcher guard), and a confirm/reject can only be made by a human JWT or the
one-time channel token (a service token is 403'd). So the audit looks for the one
shape that would represent a breach — a proposal whose DECISION was made by a
service identity (i.e. self-approval) — and proves there are none.
"""

from __future__ import annotations

from typing import Any

_RUN_STATES = ("executed", "confirmed", "failed")
_SERVICE_PREFIX = "service:"
_AUTO_APPROVE_ACTOR = "policy:auto-approve"
_SCAN_LIMIT = 100_000


def _is_service(actor: str | None) -> bool:
    return bool(actor) and actor.startswith(_SERVICE_PREFIX)


async def audit_service_principal_gating(state: Any) -> dict[str, Any]:
    """Return M1 evidence: counts of service-proposed proposals and — crucially —
    any proposal DECIDED by a service identity (a self-approval breach). ``clean``
    is True iff there are zero such breaches."""
    proposals = await state.list_proposals(limit=_SCAN_LIMIT)

    service_proposed = [p for p in proposals if _is_service(p.proposed_by)]
    service_executed = [p for p in service_proposed if str(p.status) == "executed"]

    # The ONLY shape that would be an ungated execution by the service principal:
    # a proposal whose decision was made by a service identity. Confirm/reject are
    # human-only, so this must be empty — we surface any offenders explicitly.
    self_approved = [
        p.proposal_id for p in proposals
        if str(p.status) in _RUN_STATES and _is_service(p.decided_by)
    ]

    # HAI-64 — transparency: actions executed under the operator's standing
    # auto-approve policy (decided_by=policy:auto-approve). These are NOT breaches
    # (the decider isn't a service identity — it's a human/operator authorization
    # expressed as config), but an auditor should see how many ran without a
    # per-action human click.
    auto_approved = [
        p.proposal_id for p in proposals
        if str(p.status) in _RUN_STATES and p.decided_by == _AUTO_APPROVE_ACTOR
    ]

    return {
        "service_proposed_total": len(service_proposed),
        "service_proposed_executed": len(service_executed),
        # positive evidence: every executed service proposal was decided by a
        # NON-service identity (a human, the one-time channel token, or the
        # operator's auto-approve policy).
        "executed_decided_by": sorted(
            {p.decided_by for p in service_executed if p.decided_by}
        ),
        "ungated_executions": len(self_approved),
        "ungated_execution_proposal_ids": self_approved,
        "auto_approved": len(auto_approved),
        "auto_approved_proposal_ids": auto_approved,
        "clean": len(self_approved) == 0,
    }
