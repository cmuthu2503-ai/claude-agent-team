"""HAI-25 (FR-035) — central confirmation guard / dispatcher."""

import pytest

from src.core.proposal_dispatcher import ProposalGuardError, run_confirmed_proposal
from src.core.proposal_registry import ProposalActionRegistry
from src.models.base import Proposal, ProposalStatus


def _p(status=ProposalStatus.CONFIRMED, action_type="deploy") -> Proposal:
    return Proposal(
        proposal_id="prop-1", action_type=action_type,
        proposed_by="service:hermes", status=status,
    )


async def test_guard_refuses_non_confirmed():
    reg = ProposalActionRegistry()
    for st in (ProposalStatus.PENDING, ProposalStatus.REJECTED, ProposalStatus.EXECUTED):
        with pytest.raises(ProposalGuardError):
            await run_confirmed_proposal(_p(status=st), reg, ctx=None)


async def test_no_handler_fails_cleanly():
    reg = ProposalActionRegistry()  # nothing registered
    out = await run_confirmed_proposal(_p(), reg, ctx=None)
    assert out["status"] == "failed"
    assert "No handler" in out["error"]
    assert out["result_ref"] is None


async def test_handler_runs_and_returns_result_ref():
    reg = ProposalActionRegistry()
    seen = {}

    async def handler(proposal, ctx):
        seen["proposal_id"] = proposal.proposal_id
        seen["ctx"] = ctx
        return {"result_ref": "REQ-NEW"}

    reg.register("deploy", handler)
    out = await run_confirmed_proposal(_p(), reg, ctx="the-app")
    assert out == {"status": "executed", "result_ref": "REQ-NEW", "error": None}
    assert seen == {"proposal_id": "prop-1", "ctx": "the-app"}


async def test_handler_exception_becomes_failed():
    reg = ProposalActionRegistry()

    async def boom(proposal, ctx):
        raise RuntimeError("kaboom")

    reg.register("deploy", boom)
    out = await run_confirmed_proposal(_p(), reg, ctx=None)
    assert out["status"] == "failed"
    assert "kaboom" in out["error"]
