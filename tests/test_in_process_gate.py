"""HAI-63 (FR-035) — in-process gate interception + shared proposal factory."""

import pytest

from src.core.events import EventEmitter
from src.core.in_process_gate import GateOutcome, submit_gated_action
from src.core.proposal_factory import new_proposal
from src.models.base import ProposalStatus
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "gate.db"))
    await s.initialize()
    yield s
    await s.close()


def _events():
    em = EventEmitter()
    cap: list = []

    async def h(et, d):
        cap.append((et, d))

    em.on(h)
    return em, cap


# ── factory ──────────────────────────────────────────────────────────────────

def test_factory_builds_pending_proposal_with_token_hash():
    p, raw = new_proposal(action_type="deploy", proposed_by="system:auto-dispatch")
    assert p.status == ProposalStatus.PENDING
    assert p.proposal_id.startswith("prop-")
    assert raw and p.approval_token_hash and p.approval_token_hash != raw   # hash, not raw
    assert p.ttl_seconds == 86400


def test_factory_ids_and_tokens_are_unique():
    a, ta = new_proposal(action_type="deploy", proposed_by="x")
    b, tb = new_proposal(action_type="deploy", proposed_by="x")
    assert a.proposal_id != b.proposal_id and ta != tb


# ── gate: governed vs autonomous ─────────────────────────────────────────────

async def test_governed_gated_action_becomes_proposal_and_does_not_execute(store):
    em, cap = _events()
    ran = {"v": False}

    async def execute():
        ran["v"] = True
        return "EXECUTED"

    out = await submit_gated_action(
        store, em, governed=True, action_type="task.dispatch",
        proposed_by="system:auto-dispatch", target_ref="proj-1",
        payload={"task_id": "t1"}, execute=execute,
    )
    assert isinstance(out, GateOutcome) and out.gated is True
    assert ran["v"] is False                                  # the action did NOT run
    assert out.proposal_id is not None
    saved = await store.get_proposal(out.proposal_id)
    assert saved is not None and saved.status == ProposalStatus.PENDING
    # emitted proposal.created (in_process) with the human token, NOT executed
    created = [d for et, d in cap if et == "proposal.created"]
    assert created and created[0]["in_process"] is True and created[0]["approval_token"]


async def test_autonomous_mode_executes_immediately(store):
    em, cap = _events()

    async def execute():
        return "RAN"

    out = await submit_gated_action(
        store, em, governed=False, action_type="task.dispatch",
        proposed_by="system:auto-dispatch", execute=execute,
    )
    assert out.gated is False and out.result == "RAN"
    assert await store.list_proposals() == []                 # no proposal created
    assert not any(et == "proposal.created" for et, _ in cap)


async def test_ungated_action_executes_even_when_governed(store):
    em, _ = _events()

    async def execute():
        return "RAN"

    # 'noop' isn't in GATED_ACTION_TYPES → runs inline even under governance.
    out = await submit_gated_action(
        store, em, governed=True, action_type="noop", proposed_by="system", execute=execute,
    )
    assert out.gated is False and out.result == "RAN"


async def test_idempotency_key_dedups_proposals(store):
    em, _ = _events()

    async def execute():
        return "x"

    kw = dict(governed=True, action_type="task.dispatch", proposed_by="system", execute=execute)
    out1 = await submit_gated_action(store, em, idempotency_key="k1", **kw)
    out2 = await submit_gated_action(store, em, idempotency_key="k1", **kw)
    assert out1.proposal_id == out2.proposal_id
    assert out2.deduped is True
    assert len(await store.list_proposals()) == 1             # one row, not two


async def test_emit_failure_does_not_break_caller(store):
    class BoomEvents:
        async def emit(self, *a, **k):
            raise RuntimeError("sink down")

    async def execute():
        return "x"

    out = await submit_gated_action(
        store, BoomEvents(), governed=True, action_type="deploy",
        proposed_by="system", execute=execute,
    )
    assert out.gated is True and out.proposal_id is not None  # proposal still persisted
    assert await store.get_proposal(out.proposal_id) is not None
