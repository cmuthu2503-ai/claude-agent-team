"""HAI-32 (NFR-004, M1) — P2 acceptance: NO gated path executes ungated.

This is the certification test for the whole approval gate. It asserts the single
invariant the gate exists to guarantee — a state-changing (gated) action can only
run after a human confirms a proposal — across BOTH surfaces a caller could use:

  1. In-process  — submit_gated_action() (auto_dispatch / ops_heal style callers).
  2. Execution chokepoint — run_confirmed_proposal() (what the HTTP confirm path,
     and any future in-process executor, must funnel through).

Plus the remote surface is covered by test_proposals_authz.py (service token 403 on
confirm/reject/approve) and the write-block by test_service_token_write_block.py.

Idempotency / CAS / recovery coverage lives in test_proposals_route.py,
test_proposals_store.py, and test_proposal_recovery.py respectively.
"""

import pytest

from src.core.in_process_gate import submit_gated_action
from src.core.proposal_dispatcher import ProposalGuardError, run_confirmed_proposal
from src.core.proposal_registry import GATED_ACTION_TYPES, ProposalActionRegistry
from src.models.base import Proposal, ProposalStatus
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "inv.db"))
    await s.initialize()
    yield s
    await s.close()


# ── In-process surface: EVERY gated action gates (governed mode) ─────────────

@pytest.mark.parametrize("action_type", sorted(GATED_ACTION_TYPES))
async def test_every_gated_action_is_intercepted_in_process(store, action_type):
    """For each gated action_type, governed mode must turn it into a pending
    proposal and NOT execute it. (If a new gated action is added without a gate,
    this fails — the guardrail can't silently regress per action.)"""
    ran = {"v": False}

    async def execute():
        ran["v"] = True
        return "SHOULD-NOT-RUN"

    out = await submit_gated_action(
        store, events=None, governed=True, action_type=action_type,
        proposed_by="system:auto", execute=execute,
    )
    assert out.gated is True and ran["v"] is False
    saved = await store.get_proposal(out.proposal_id)
    assert saved is not None and saved.status == ProposalStatus.PENDING


async def test_autonomous_mode_is_the_only_ungated_path(store):
    """The ONLY way a gated action runs without a proposal is explicit autonomous
    mode (governed=False) — documents the single deliberate escape hatch."""
    ran = {"v": False}

    async def execute():
        ran["v"] = True
        return "RAN"

    out = await submit_gated_action(
        store, events=None, governed=False, action_type="deploy",
        proposed_by="system:auto", execute=execute,
    )
    assert out.gated is False and ran["v"] is True
    assert await store.list_proposals() == []          # no proposal — ran directly


# ── Execution chokepoint: nothing runs unless CONFIRMED ──────────────────────

@pytest.mark.parametrize(
    "status",
    [ProposalStatus.PENDING, ProposalStatus.REJECTED, ProposalStatus.EXPIRED,
     ProposalStatus.EXECUTED, ProposalStatus.FAILED],
)
async def test_dispatcher_refuses_any_non_confirmed_status(status):
    """run_confirmed_proposal is the single execution chokepoint: it HARD-raises
    on anything not 'confirmed', so a non-confirmed proposal can never execute even
    if a caller reaches the dispatcher directly."""
    executed = {"v": False}
    reg = ProposalActionRegistry()

    async def handler(proposal, ctx):
        executed["v"] = True
        return {"result_ref": "X"}

    reg.register("deploy", handler)
    proposal = Proposal(proposal_id="p1", action_type="deploy", proposed_by="x", status=status)

    with pytest.raises(ProposalGuardError):
        await run_confirmed_proposal(proposal, reg, ctx=None)
    assert executed["v"] is False                      # handler never invoked


async def test_confirmed_proposal_is_the_one_path_that_executes():
    """Positive control: a CONFIRMED proposal with a handler runs — proving the
    guard gates on status, not on everything."""
    reg = ProposalActionRegistry()

    async def handler(proposal, ctx):
        return {"result_ref": "DONE"}

    reg.register("deploy", handler)
    proposal = Proposal(
        proposal_id="p1", action_type="deploy", proposed_by="x", status=ProposalStatus.CONFIRMED
    )
    out = await run_confirmed_proposal(proposal, reg, ctx=None)
    assert out["status"] == "executed" and out["result_ref"] == "DONE"
